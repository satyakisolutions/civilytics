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
from sqlalchemy import insert

from app.config.kafka import GROUP_ALERT_DISPATCHER as GROUP_ALERT_DISPATCH
from app.db.engine import async_session
from app.ws.alerts import broadcast_alert

logger = logging.getLogger(__name__)

TOPIC_ALERTS = "wastewatch.alerts"
TTS_DIR = Path("/tmp/wastewatch/tts")


class AlertConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_ALERTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_ALERT_DISPATCH,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertConsumer started — consuming %s", TOPIC_ALERTS)

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
                    await self._handle_alert(msg.value)
                except Exception:
                    logger.exception("Failed to handle alert: %s", msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertConsumer loop error")

    async def _handle_alert(self, payload: dict):
        camera_id = payload.get("camera_id", "")
        severity = payload.get("severity", "warning")
        title = payload.get("title", "Alert")
        message = payload.get("message", "")
        track_id = payload.get("track_id")

        # Check for TTS audio file
        tts_audio_url = None
        if message:
            text_hash = hashlib.sha256(message.encode()).hexdigest()[:16]
            tts_path = TTS_DIR / f"tts_{text_hash}.wav"
            if tts_path.exists():
                tts_audio_url = f"/tts/tts_{text_hash}.wav"

        alert_id = str(uuid.uuid4())
        now = datetime.utcnow()

        # Write to DB — triggers Supabase realtime subscription in frontend
        try:
            async with async_session() as session:
                await session.execute(
                    insert(self._get_alert_table()).values(
                        id=alert_id,
                        camera_id=camera_id,
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
            "camera_id": camera_id,
            "severity": severity,
            "title": title,
            "message": message,
            "tts_audio_url": tts_audio_url,
            "timestamp": now.isoformat(),
        }
        await broadcast_alert(ws_payload)
        logger.info("Alert broadcast via WebSocket: [%s] %s", severity, title[:60])

    @staticmethod
    def _get_alert_table():
        from app.db.models import Alert
        return Alert.__table__
