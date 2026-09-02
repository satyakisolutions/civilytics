import asyncio
import logging
import os
import time
import orjson
import httpx
from aiokafka import AIOKafkaConsumer
from app.config.kafka import GROUP_ALERT_DISPATCHER, TOPIC_ALERTS, TOPIC_VLM_RESULTS
from app.config.settings import settings
from app.alerts.throttle import AlertThrottle
from app.alerts.telegram import TelegramAlert
from app.alerts.websocket import push_alert_ws
from app.alerts.ip_speaker import IPSpeakerManager
from app.alerts.pipeline_notifier import PipelineNotifier

logger = logging.getLogger(__name__)

# Severities that should always generate alerts when active dumping is detected
_ALWAYS_ALERT_SEVERITIES = {"critical", "high", "medium"}

# alert-svc is a standalone microservice with no DB session of its own
# (unlike app/services/alert_consumer.py, which runs inside the api
# process) -- camera Zone/Location/Name for Telegram messages is fetched
# over REST instead, with a short TTL cache so a burst of alerts on the
# same camera doesn't hit the API once per alert.
_API_BASE = os.environ.get("WW_API_BASE", "http://api:8080")
_CAMERA_INFO_TTL_S = 300.0
# A failed lookup (api not reachable yet, transient network blip) must NOT
# be cached for the full TTL -- that turns one bad request into 5 minutes
# of every alert on that camera silently falling back to the raw UUID,
# which is exactly the "sometimes shows Zone/Location, sometimes shows a
# UUID" inconsistency this was causing.
_CAMERA_INFO_NEGATIVE_TTL_S = 10.0
_camera_info_cache: dict[str, tuple[float, dict | None]] = {}


class AlertDispatcher:
    def __init__(
        self,
        bootstrap_servers: str | None = None,
        ip_speaker_manager: IPSpeakerManager | None = None,
        pipeline_notifier: PipelineNotifier | None = None,
    ):
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._throttle = AlertThrottle()
        self._telegram = TelegramAlert()
        self._ip_speaker = ip_speaker_manager or IPSpeakerManager()
        self._pipeline = pipeline_notifier or PipelineNotifier(settings.redis_url)

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_VLM_RESULTS,
            TOPIC_ALERTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ALERT_DISPATCHER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertDispatcher started — consuming %s, %s", TOPIC_VLM_RESULTS, TOPIC_ALERTS)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        await self._throttle.close()
        await self._telegram.close()
        await self._ip_speaker.close()
        logger.info("AlertDispatcher stopped")

    async def _resolve_camera_info(self, camera_id: str) -> dict | None:
        # Stale-while-revalidate: an expired-but-present cache entry is
        # returned immediately and refreshed in the background, so the
        # alert fan-out (Telegram/WS/speaker) never blocks on the httpx
        # round-trip -- only a true first-ever lookup for a camera_id
        # pays that latency. This was adding up to 3s to every alert on
        # each ~5min cache-expiry window, showing up as "alerting is
        # slightly late".
        now = time.monotonic()
        cached = _camera_info_cache.get(camera_id)
        if cached:
            cached_at, cached_info = cached
            ttl = _CAMERA_INFO_TTL_S if cached_info is not None else _CAMERA_INFO_NEGATIVE_TTL_S
            if now - cached_at < ttl:
                return cached_info
            asyncio.create_task(self._refresh_camera_info(camera_id))
            return cached_info
        return await self._refresh_camera_info(camera_id)

    async def _refresh_camera_info(self, camera_id: str) -> dict | None:
        info: dict | None = None
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{_API_BASE}/api/cameras/{camera_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    info = {"name": data.get("name"), "zone": data.get("zone"), "location": data.get("location")}
        except Exception:
            logger.debug("Failed to resolve camera info for %s", camera_id)
        if info is None:
            logger.warning("Camera info lookup failed for %s — will retry in %.0fs", camera_id, _CAMERA_INFO_NEGATIVE_TTL_S)
        _camera_info_cache[camera_id] = (time.monotonic(), info)
        return info

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                try:
                    if msg.topic == TOPIC_ALERTS:
                        await self._handle_direct_alert(msg.value)
                    else:
                        await self._handle_message(msg.value)
                except Exception:
                    logger.exception("Error handling %s message", msg.topic)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertDispatcher consumer loop error")

    async def _handle_direct_alert(self, payload: dict):
        """Handle a raw alert published directly to wastewatch.alerts by the
        detection pipeline (e.g. kafka_producer.send_alert in pipeline.py's
        waste-dwell/dumper-attribution logic) -- no VLM enrichment involved,
        just camera/track/severity/message/dwell fields as sent.
        """
        camera_id: str = payload.get("camera_id", "unknown")
        severity: str = str(payload.get("severity", "warning")).lower()
        track_id = payload.get("track_id")
        message: str = payload.get("message") or f"Alert at camera {camera_id}"
        dwell_time_s: float | None = payload.get("dwell_time_s")
        snapshot_url: str | None = payload.get("snapshot_url")
        detected_at: float | None = payload.get("detected_at") or payload.get("timestamp")

        # Skip throttle for alerts that already carry their own saved
        # evidence snapshot -- same reasoning as _handle_message: those are
        # already paced by the pipeline's own FSM/repeat-alert logic
        # (pipeline/app/pipeline.py), and this WAS the actual code path
        # silently dropping almost every Telegram send (wrong method
        # edited the first time around -- this file has two separate
        # handlers, one per topic, each with its own throttle check).
        if not snapshot_url:
            cooldown_s: int = payload.get("cooldown_s", 60)
            max_per_hour: int = payload.get("max_per_hour", 10)
            if not await self._throttle.should_alert(
                camera_id, track_id=track_id, cooldown_s=cooldown_s, max_per_hour=max_per_hour
            ):
                logger.info("Direct alert throttled for camera=%s", camera_id)
                return
            await self._throttle.record_alert(camera_id, cooldown_s=cooldown_s)

        logger.info("Dispatching direct alert: camera=%s severity=%s message=%s", camera_id, severity, message)

        camera_info = await self._resolve_camera_info(camera_id)

        await asyncio.gather(
            self._telegram.send_alert(
                camera_id=camera_id,
                severity=severity,
                message=message,
                snapshot_url=snapshot_url,
                dwell_time_s=dwell_time_s,
                camera_name=camera_info.get("name") if camera_info else None,
                zone=camera_info.get("zone") if camera_info else None,
                location=camera_info.get("location") if camera_info else None,
                detected_at=detected_at,
            ),
            push_alert_ws(
                camera_id=camera_id,
                severity=severity,
                message=message,
                track_id=track_id,
                dwell_time_s=dwell_time_s,
                snapshot_url=snapshot_url,
            ),
            self._ip_speaker.play_alert(
                camera_id=camera_id,
                text=message,
                severity=severity,
            ),
            return_exceptions=True,
        )

    async def _handle_message(self, payload: dict):
        # VLM result payload nests detection fields under `response` —
        # read from there first, fall back to top-level for legacy senders.
        _resp = payload.get("response") or {}
        camera_id: str = payload.get("camera_id", "unknown")
        severity: str = str(_resp.get("severity") or payload.get("severity", "info")).lower()
        is_dumping: bool = bool(_resp.get("is_dumping", payload.get("is_dumping", False)))
        track_id: int | None = payload.get("track_id")
        dwell_time_s: float | None = (payload.get("track_context") or {}).get("dwell_time_s") or payload.get("dwell_time_s")
        vlm_summary: str | None = _resp.get("reasoning") or payload.get("vlm_summary")
        plate_number: str | None = payload.get("plate_number")
        snapshot_url: str | None = payload.get("snapshot_url")
        message: str = _resp.get("deterrent_message") or payload.get("message") or f"Suspicious activity detected at camera {camera_id}"
        detected_at: float | None = payload.get("detected_at") or payload.get("timestamp")

        # Skip low-signal "info" events unless active dumping is confirmed
        if severity == "info" and not is_dumping:
            logger.debug("Skipping info-level non-dumping event: camera=%s", camera_id)
            return

        # Throttle check — SKIPPED for alerts that already carry their own
        # saved evidence snapshot_url. Those come from the pipeline's own
        # FSM-driven firing (one-shot on VIOLATION, then a deliberate
        # periodic re-alert every waste_repeat_alert_interval_s while
        # unresolved -- see pipeline/app/pipeline.py) -- that's already the
        # intended pacing. An additional blanket 60s Telegram throttle on
        # top of it just made Telegram silently drop most of them and show
        # a stale photo from whichever alert happened to be first through
        # in a given window, while the DB/timeline moved on. Non-evidence
        # events (VLM results with no snapshot of their own) still throttle
        # to avoid literal spam from that path.
        if not snapshot_url:
            cooldown_s: int = payload.get("cooldown_s", 60)
            max_per_hour: int = payload.get("max_per_hour", 10)
            if not await self._throttle.should_alert(
                camera_id, track_id=track_id, cooldown_s=cooldown_s, max_per_hour=max_per_hour
            ):
                logger.info("Alert throttled for camera=%s", camera_id)
                return
            # Record the alert before fan-out so concurrent messages are also throttled
            await self._throttle.record_alert(camera_id, cooldown_s=cooldown_s)

        logger.info(
            "Dispatching alert: camera=%s severity=%s is_dumping=%s",
            camera_id,
            severity,
            is_dumping,
        )

        camera_info = await self._resolve_camera_info(camera_id)

        # --- Stage 3 enrichment: push VLM result to WebSocket metadata stream ---
        # The pipeline (stage 1+2) fires recording, VLM request, SIP, etc.
        # The backend (stage 3) receives VLM results HERE and pushes enrichment
        # to the frontend so the bbox overlay shows VLM confirmation + waste type.
        vlm_enrichment = {
            "type": "vlm_enrichment",
            "camera_id": camera_id,
            "track_id": track_id,
            "is_dumping": is_dumping,
            "severity": severity,
            "waste_type": payload.get("waste_type", "unknown"),
            "vlm_confidence": payload.get("confidence", 0.0),
            "vlm_summary": vlm_summary,
            "plate_number": plate_number,
            "reasoning": payload.get("reasoning", ""),
        }

        # Fan-out: Telegram + WebSocket alert + WebSocket enrichment + IP Speaker
        # SIP calls are NOT in the immediate fan-out — handled by EscalationWorker
        await asyncio.gather(
            self._telegram.send_alert(
                camera_id=camera_id,
                severity=severity,
                message=message,
                snapshot_url=snapshot_url,
                dwell_time_s=dwell_time_s,
                vlm_summary=vlm_summary,
                plate_number=plate_number,
                camera_name=camera_info.get("name") if camera_info else None,
                zone=camera_info.get("zone") if camera_info else None,
                location=camera_info.get("location") if camera_info else None,
                detected_at=detected_at,
            ),
            push_alert_ws(
                camera_id=camera_id,
                severity=severity,
                message=message,
                track_id=track_id,
                dwell_time_s=dwell_time_s,
                vlm_summary=vlm_summary,
                plate_number=plate_number,
                snapshot_url=snapshot_url,
            ),
            # Push VLM enrichment to the metadata WebSocket so the frontend
            # bbox overlay can show VLM result per detection (stage 3)
            push_alert_ws(
                camera_id=camera_id,
                severity=severity,
                message="vlm_enrichment",
                track_id=track_id,
                **vlm_enrichment,
            ),
            self._ip_speaker.play_alert(
                camera_id=camera_id,
                text=message,
                severity=severity,
            ),
            return_exceptions=True,
        )

        # --- Notify pipeline to stop recording if VLM says NOT dumping ---
        # This is the feedback loop: backend → Redis pub/sub → pipeline
        # Pipeline receives the command and: stops recording, resets FSM,
        # saves evidence metadata, fades bbox to black
        if not is_dumping and track_id is not None:
            self._pipeline.clear_by_vlm(
                camera_id=camera_id,
                track_id=track_id,
                vlm_confidence=payload.get("confidence", 0.0),
                waste_type=payload.get("waste_type", "unknown"),
            )
            logger.info(
                "VLM cleared: %s track=%s — pipeline notified to stop recording",
                camera_id, track_id,
            )
