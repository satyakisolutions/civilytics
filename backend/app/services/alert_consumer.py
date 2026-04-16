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
from sqlalchemy import insert, select

from app.config.kafka import GROUP_ALERT_DISPATCHER as GROUP_ALERT_DISPATCH
from app.db.engine import async_session
from app.db.models import Camera
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

    async def _resolve_camera_uuid(self, camera_name: str) -> str | None:
        """Resolve pipeline camera name (e.g. 'cam-01') to database UUID."""
        try:
            # Check if already a UUID
            uuid.UUID(camera_name)
            return camera_name
        except ValueError:
            pass
        # Look up by name
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Camera.id).where(Camera.name == camera_name)
                )
                row = result.scalar_one_or_none()
                if row:
                    return str(row)
        except Exception:
            logger.debug("Failed to resolve camera UUID for %s", camera_name)
        return None

    async def _handle_alert(self, payload: dict):
        camera_name = payload.get("camera_id", "")
        severity = payload.get("severity", "warning")
        title = payload.get("title", "Alert")
        message = payload.get("message", "")
        track_id = payload.get("track_id")
        tts_text = payload.get("tts_text", "")  # The exact text sent to TTS worker

        # Resolve pipeline camera name to DB UUID
        camera_uuid = await self._resolve_camera_uuid(camera_name)
        if not camera_uuid:
            logger.warning("AlertConsumer: unknown camera %s — broadcasting WS only", camera_name)

        # Compute TTS audio URL from the exact text sent to the TTS worker.
        # The TTS engine names files tts_{sha256(text)[:16]}.wav — use the
        # tts_text field (same string the pipeline sent to wastewatch.tts)
        # so the hash matches. The file may not exist yet (async generation)
        # but the URL is deterministic so the frontend can retry.
        tts_audio_url = None
        _tts_hash_source = tts_text or message
        if _tts_hash_source:
            text_hash = hashlib.sha256(_tts_hash_source.encode()).hexdigest()[:16]
            tts_audio_url = f"/tts/tts_{text_hash}.wav"

        alert_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Write to DB — triggers Supabase realtime subscription in frontend
        if camera_uuid:
            try:
                async with async_session() as session:
                    await session.execute(
                        insert(self._get_alert_table()).values(
                            id=alert_id,
                            camera_id=camera_uuid,
                            severity=severity,
                            title=title,
                            message=message,
                            tts_audio_url=tts_audio_url,
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
            "severity": severity,
            "title": title,
            "message": message,
            "tts_audio_url": tts_audio_url,
            "timestamp": now.isoformat(),
        }
        await broadcast_alert(ws_payload)
        logger.info("Alert broadcast via WebSocket: [%s] %s", severity, title[:60])

    async def _handle_vlm_result(self, payload: dict):
        """Handle VLM analysis result — broadcast to UI and trigger TTS with VLM reasoning."""
        camera_id = payload.get("camera_id", "")
        track_id = payload.get("track_id")
        response = payload.get("response", {})

        reasoning = response.get("reasoning", "")
        action = response.get("action", "")
        is_dumping = response.get("is_dumping", False)
        waste_type = response.get("waste_type", "none")
        confidence = response.get("confidence", 0)
        severity = "critical" if is_dumping else "info"

        # Build notification text from VLM analysis
        if is_dumping:
            vlm_text = f"VLM Analysis at camera {camera_id}. {reasoning}. Waste type: {waste_type}."
        elif reasoning:
            vlm_text = f"VLM Analysis at camera {camera_id}. {reasoning}."
        else:
            vlm_text = f"VLM Analysis at camera {camera_id}. {action}."

        # Compute TTS hash for the VLM text
        tts_hash = hashlib.sha256(vlm_text.encode()).hexdigest()[:16]
        tts_audio_url = f"/tts/tts_{tts_hash}.wav"

        # Send TTS request via Kafka so the TTS worker generates the audio
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
                value={"camera_id": camera_id, "track_id": track_id,
                       "text": vlm_text, "priority": "high" if is_dumping else "normal"},
            )
        except Exception:
            logger.exception("Failed to send TTS for VLM result")

        # Broadcast VLM result + TTS URL to frontend via WebSocket
        ws_payload = {
            "type": "vlm_result",
            "camera_id": camera_id,
            "track_id": track_id,
            "is_dumping": is_dumping,
            "waste_type": waste_type,
            "confidence": confidence,
            "reasoning": reasoning,
            "action": action,
            "tts_audio_url": tts_audio_url,
            "severity": severity,
            "title": f"VLM: {action}" if action else "VLM Analysis",
            "message": reasoning,
        }
        await broadcast_alert(ws_payload)
        logger.info("VLM result broadcast: cam=%s dumping=%s — %s", camera_id, is_dumping, reasoning[:80])

    @staticmethod
    def _get_alert_table():
        from app.db.models import Alert
        return Alert.__table__
