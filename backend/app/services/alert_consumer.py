"""Kafka consumer for pipeline alerts → DB + WebSocket + TTS audio URL.

Consumes wastewatch.alerts, writes to the alerts PostgreSQL table (which
triggers Supabase realtime for the frontend), broadcasts to WebSocket,
and generates TTS audio for the alert message.
"""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime
from pathlib import Path

import orjson
from aiokafka import AIOKafkaConsumer
from sqlalchemy import insert, select, update

# Use a SEPARATE group from the escalation worker so both receive messages
GROUP_ALERT_DISPATCH = "wastewatch.group.api-alert-consumer"
from app.db.engine import async_session
from app.db.models import Camera, Zone
from app.ws.alerts import broadcast_alert

logger = logging.getLogger(__name__)

TOPIC_ALERTS = "wastewatch.alerts"
TOPIC_VLM_RESULTS = "wastewatch.vlm.results"
TOPIC_TTS = "wastewatch.tts"
TTS_DIR = Path("/tmp/wastewatch/tts")


class AlertConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._tts_producer = None  # Lazy init for producing TTS requests from VLM results

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_ALERTS, TOPIC_VLM_RESULTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ALERT_DISPATCH,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertConsumer started — consuming %s + %s", TOPIC_ALERTS, TOPIC_VLM_RESULTS)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        logger.info("AlertConsumer stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                try:
                    if msg.topic == TOPIC_ALERTS:
                        await self._handle_alert(msg.value)
                    elif msg.topic == TOPIC_VLM_RESULTS:
                        await self._handle_vlm_result(msg.value)
                except Exception:
                    logger.exception("Failed to handle %s message: %s", msg.topic, str(msg.value)[:200])
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertConsumer loop error")

    async def _resolve_camera_uuid(self, camera_name: str) -> tuple[str | None, str | None]:
        """Resolve pipeline camera name (e.g. 'cam-01') to (DB UUID, tenant_id)."""
        try:
            async with async_session() as session:
                try:
                    parsed = uuid.UUID(camera_name)
                    _where = Camera.id == parsed
                except ValueError:
                    _where = Camera.name == camera_name
                result = await session.execute(
                    select(Camera.id, Camera.tenant_id).where(_where)
                )
                row = result.first()
                if row:
                    return str(row.id), str(row.tenant_id)
        except Exception:
            logger.debug("Failed to resolve camera UUID for %s", camera_name)
        return None, None

    async def _resolve_camera_info(self, camera_id_or_name: str) -> dict | None:
        """Fetch name/zone/location for a camera (by UUID or by pipeline name).

        Alerts arrive keyed by the pipeline's raw camera_id (a DB UUID in
        practice), which means nothing to an operator reading a Telegram
        message or the alert panel -- "Zone A / Main Entrance / Entry" is
        what identifies a physical location, not a UUID.
        """
        try:
            async with async_session() as session:
                try:
                    uuid.UUID(camera_id_or_name)
                    stmt = (
                        select(Camera.name, Zone.name.label("zone"), Camera.location)
                        .join(Zone, Camera.zone_id == Zone.id)
                        .where(Camera.id == camera_id_or_name)
                    )
                except ValueError:
                    stmt = (
                        select(Camera.name, Zone.name.label("zone"), Camera.location)
                        .join(Zone, Camera.zone_id == Zone.id)
                        .where(Camera.name == camera_id_or_name)
                    )
                result = await session.execute(stmt)
                row = result.first()
                if row:
                    return {"name": row.name, "zone": row.zone, "location": row.location}
        except Exception:
            logger.debug("Failed to resolve camera info for %s", camera_id_or_name)
        return None

    async def _send_tts(self, camera_id: str, track_id, text: str, priority: str):
        try:
            from aiokafka import AIOKafkaProducer
            if self._tts_producer is None:
                self._tts_producer = AIOKafkaProducer(
                    bootstrap_servers=self._bootstrap_servers,
                    value_serializer=lambda v: orjson.dumps(v),
                )
                await self._tts_producer.start()
            await self._tts_producer.send_and_wait(
                TOPIC_TTS,
                value={"camera_id": camera_id, "track_id": track_id, "text": text, "priority": priority},
            )
        except Exception:
            logger.exception("Failed to send TTS request (camera=%s)", camera_id)

    async def _handle_alert(self, payload: dict):
        camera_name = payload.get("camera_id", "")
        severity = payload.get("severity", "warning")
        title = payload.get("title", "Alert")
        message = payload.get("message", "")
        track_id = payload.get("track_id")
        tts_text = payload.get("tts_text", "")  # The exact text sent to TTS worker
        suppress_tts = payload.get("suppress_tts", False)
        snapshot_url = payload.get("snapshot_url")
        # detected_at (unix epoch, set on the GStreamer pad-probe thread at
        # the exact moment the pipeline fired -- pipeline/app/pipeline.py's
        # _fire_waste_alert_async) beats defaulting to utcnow() here: the
        # snapshot save + this consumer's own processing both happen after
        # the fact, on a different thread/process/topic hop, and any delay
        # in that chain previously showed up as the alert's timestamp
        # drifting later than when the event actually happened.
        _detected_at = payload.get("detected_at")

        # Resolve pipeline camera name to (DB UUID, tenant_id)
        camera_uuid, camera_tenant = await self._resolve_camera_uuid(camera_name)
        if not camera_uuid:
            logger.warning("AlertConsumer: unknown camera %s — broadcasting WS only", camera_name)

        # Zone/Location/Name instead of a bare camera UUID -- that's what
        # actually identifies a physical spot to an operator. Only the
        # DISPLAYED message gets this prefix; `message` itself stays raw
        # for the TTS request below (reading "Zone A, Main Entrance,
        # Entry" aloud before the actual alert text would be noise).
        camera_info = await self._resolve_camera_info(camera_uuid or camera_name)
        camera_zone = camera_info.get("zone") if camera_info else None
        if camera_info:
            _loc_parts = [p for p in (camera_info.get("zone"), camera_info.get("location"), camera_info.get("name")) if p]
            display_message = f"[{' / '.join(_loc_parts)}] {message}" if _loc_parts else message
        else:
            display_message = message

        # Templated pipeline alerts set suppress_tts=True and get no audio URL.
        #
        # tts_audio_url is a hash-derived path prediction -- the WAV file
        # at that path only exists if something actually asked the TTS
        # worker to render it. _handle_vlm_result does that (publishes to
        # TOPIC_TTS below); this path previously computed the URL and
        # stopped there, so every pipeline-originated alert (waste dump,
        # suspicious proximity, etc) advertised an audio URL that 404'd --
        # no request was ever sent to generate it. Fixed to publish the
        # same TOPIC_TTS request _handle_vlm_result does.
        tts_audio_url = None
        if not suppress_tts and severity == "critical":
            _tts_hash_source = tts_text or message
            if _tts_hash_source:
                # TTSEngine.generate() applies _apply_tone() to the text
                # BEFORE hashing for the saved filename -- hashing the raw
                # text here would predict a URL that never matches what
                # the worker actually writes to disk (same mismatch this
                # code already avoided in _handle_vlm_result below).
                from app.tts.engine import _apply_tone
                _shaped = _apply_tone(_tts_hash_source)
                text_hash = hashlib.sha256(_shaped.encode()).hexdigest()[:16]
                tts_audio_url = f"/tts/tts_{text_hash}.wav"
                await self._send_tts(camera_name, track_id, _tts_hash_source, "high" if severity == "critical" else "normal")

        alert_id = str(uuid.uuid4())
        now = (
            datetime.utcfromtimestamp(_detected_at)
            if isinstance(_detected_at, (int, float))
            else datetime.utcnow()
        )

        # Write to DB — triggers Supabase realtime subscription in frontend.
        # Prior active alerts on the same camera stay active so the panel
        # stacks live notifications; operator acknowledges or clears them.
        if camera_uuid:
            try:
                async with async_session() as session:
                    tbl = self._get_alert_table()
                    await session.execute(
                        insert(tbl).values(
                            id=alert_id,
                            tenant_id=camera_tenant,
                            camera_id=camera_uuid,
                            severity=severity,
                            title=title,
                            message=display_message,
                            tts_audio_url=tts_audio_url,
                            snapshot_url=snapshot_url,
                            status="active",
                            timestamp=now,
                            created_at=now,
                        )
                    )
                    await session.commit()
                logger.info("Alert saved to DB: %s [%s] %s", alert_id, severity, title[:60])
            except Exception:
                logger.exception("Failed to save alert to DB")

        # Broadcast via WebSocket (for clients not using Supabase realtime)
        ws_payload = {
            "type": "alert",
            "id": alert_id,
            "camera_id": camera_uuid or camera_name,
            "camera_name": camera_info.get("name") if camera_info else None,
            "zone": camera_zone,
            "location": camera_info.get("location") if camera_info else None,
            "severity": severity,
            "title": title,
            "message": display_message,
            "tts_audio_url": tts_audio_url,
            "snapshot_url": snapshot_url,
            "timestamp": now.isoformat(),
        }
        await broadcast_alert(ws_payload)
        logger.info("Alert broadcast via WebSocket: [%s] %s", severity, title[:60])

    async def _handle_vlm_result(self, payload: dict):
        """Handle VLM analysis result — broadcast to UI and trigger TTS with VLM reasoning."""
        logger.info("VLM result received: cam=%s track=%s is_dumping=%s waste=%s",
                     payload.get("camera_id"), payload.get("track_id"),
                     (payload.get("response") or {}).get("is_dumping"),
                     (payload.get("response") or {}).get("waste_type"))
        camera_id = payload.get("camera_id", "")
        track_id = payload.get("track_id")
        response = payload.get("response", {})

        reasoning = response.get("reasoning", "")
        action = response.get("action", "")
        is_dumping = response.get("is_dumping", False)
        waste_type = response.get("waste_type", "none")
        confidence = response.get("confidence", 0)
        person_desc = response.get("person_description", "")
        deterrent = response.get("deterrent_message", "")
        severity = "critical" if is_dumping else "warning"

        # --- Deterrent TTS — CRITICAL/dumping only ---
        # Warnings get text on UI but no spoken deterrent. Audio reserved
        # for confirmed dumping / red alerts so operators don't hear
        # constant barking on every suspicious stop.
        speaker_text = ""
        if is_dumping:
            speaker_text = deterrent or ""
            if not speaker_text and person_desc:
                speaker_text = f"{person_desc}. Illegal dumping detected. You are being recorded."
        if speaker_text:
            await self._send_tts(camera_id, track_id, speaker_text, "high")

        # --- Operator notification (UI) ---
        # Show deterrent if available (aggressive scolding), otherwise person + reasoning.
        # Prepend waste type when VLM identified one — answers "what was dumped?".
        if deterrent:
            operator_text = deterrent
        elif person_desc and reasoning:
            operator_text = f"{person_desc}. {reasoning}"
        else:
            operator_text = reasoning or action or "Activity detected"
        if waste_type and waste_type != "none":
            operator_text = f"[waste: {waste_type}] {operator_text}"

        # Compute TTS audio URL from the speaker text
        tts_audio_url = None
        if speaker_text:
            from app.tts.engine import _apply_tone
            shaped = _apply_tone(speaker_text)
            tts_audio_url = f"/tts/tts_{hashlib.sha256(shaped.encode()).hexdigest()[:16]}.wav"

        # VLM-gated escalation: only emit an alert when the model confirms
        # dumping AND the target was close + slow enough to be actionable.
        # Speed + bbox_area arrive in track_context.path_summary from the
        # pipeline ("enter;speed_px_s=X;bbox_area=Y"). Fast walkers and far
        # targets are filtered out so audio/alerts don't fire on passersby.
        if not is_dumping:
            logger.info("VLM non-violation — skipping alert for %s (waste=%s conf=%s)",
                        camera_id, waste_type, confidence)
            return
        _ctx = payload.get("track_context") or {}
        _ps = str(_ctx.get("path_summary") or "")
        _speed_norm = 0.0
        _area_norm = 0.0
        for _part in _ps.split(";"):
            if _part.startswith("speed_norm="):
                try: _speed_norm = float(_part.split("=", 1)[1])
                except ValueError: pass
            elif _part.startswith("area_norm="):
                try: _area_norm = float(_part.split("=", 1)[1])
                except ValueError: pass
        # Thresholds on normalized frame units (bbox coords are 0..1).
        # SPEED_NORM_MAX=0.25 ≈ target crossing 25% of frame per second
        # (brisk walk/jog). AREA_NORM_MIN=0.004 ≈ bbox smaller than 0.4%
        # of frame — target too far from camera to act on.
        SPEED_NORM_MAX = 0.25
        AREA_NORM_MIN = 0.004
        if _speed_norm > SPEED_NORM_MAX or (_area_norm > 0.0 and _area_norm < AREA_NORM_MIN):
            logger.info("VLM violation suppressed by motion gate for %s: speed=%.4f area=%.5f",
                        camera_id, _speed_norm, _area_norm)
            return
        _is_fallback = "could not be parsed" in reasoning
        if not person_desc and _is_fallback:
            logger.debug("Skipping VLM alert — no useful content for %s (fallback=%s)", camera_id, _is_fallback)
            return

        camera_uuid, camera_tenant = await self._resolve_camera_uuid(camera_id)
        # camera_uuid or camera_id (not gated on camera_uuid) -- matches
        # _handle_alert's fallback: a transient UUID-resolution miss should
        # still let _resolve_camera_info try the by-name path instead of
        # dropping the Zone/Location/Name prefix outright.
        camera_info = await self._resolve_camera_info(camera_uuid or camera_id)
        camera_zone = camera_info.get("zone") if camera_info else None
        if camera_info:
            _loc_parts = [p for p in (camera_info.get("zone"), camera_info.get("location"), camera_info.get("name")) if p]
            display_operator_text = f"[{' / '.join(_loc_parts)}] {operator_text}" if _loc_parts else operator_text
        else:
            display_operator_text = operator_text
        if camera_uuid:
            try:
                now = datetime.utcnow()
                vlm_title = f"VLM: {person_desc[:50]}" if person_desc else f"VLM: {action[:50]}"
                async with async_session() as session:
                    tbl = self._get_alert_table()
                    # If a pipeline-generated critical (VIOLATION) alert is
                    # still active on this camera, upgrade it in place with
                    # the VLM description — keep severity=critical so the UI
                    # retains the red state, but the message now names the
                    # actual subject (e.g. "Woman in saree with handbag").
                    critical_rows = (await session.execute(
                        select(tbl.c.id)
                        .where(tbl.c.camera_id == camera_uuid,
                               tbl.c.status == "active",
                               tbl.c.severity == "critical")
                    )).all()

                    if critical_rows:
                        critical_ids = [r.id for r in critical_rows]
                        await session.execute(
                            update(tbl)
                            .where(tbl.c.id.in_(critical_ids))
                            .values(title=vlm_title, message=display_operator_text,
                                    tts_audio_url=tts_audio_url,
                                    timestamp=now)  # refresh so UI treats as new
                        )
                        await session.commit()
                        logger.warning("VLM upgraded existing critical alert(s) on %s: %s", camera_id, person_desc[:40])
                        return

                    # No prior critical alert — insert new one. Keep prior
                    # non-critical active so panel stacks live alerts.
                    alert_id = str(uuid.uuid4())
                    await session.execute(
                        insert(tbl).values(
                            id=alert_id,
                            tenant_id=camera_tenant,
                            camera_id=camera_uuid,
                            severity=severity,
                            title=vlm_title,
                            message=display_operator_text,
                            tts_audio_url=tts_audio_url,
                            status="active",
                            timestamp=now,
                            created_at=now,
                        )
                    )
                    await session.commit()
                logger.warning("VLM alert saved to DB: %s [%s] %s",
                               alert_id if 'alert_id' in locals() else 'upgraded',
                               severity, person_desc[:40])
            except Exception:
                logger.exception("Failed to save VLM alert to DB")

        ws_payload = {
            "type": "vlm_result",
            "camera_id": camera_id,
            "camera_name": camera_info.get("name") if camera_info else None,
            "zone": camera_zone,
            "location": camera_info.get("location") if camera_info else None,
            "track_id": track_id,
            "is_dumping": is_dumping,
            "waste_type": waste_type,
            "confidence": confidence,
            "reasoning": reasoning,
            "person_description": person_desc,
            "deterrent_message": deterrent,
            "action": action,
            "tts_audio_url": tts_audio_url,
            "severity": severity,
            "title": f"VLM: {person_desc or action}" if (person_desc or action) else "VLM Analysis",
            "message": display_operator_text,
        }
        await broadcast_alert(ws_payload)
        logger.info("VLM result: cam=%s dumping=%s deterrent='%s' reasoning='%s'",
                     camera_id, is_dumping, deterrent[:60], reasoning[:60])

    @staticmethod
    def _get_alert_table():
        from app.db.models import Alert
        return Alert.__table__
