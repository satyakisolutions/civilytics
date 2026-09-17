# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Consumes wastewatch.camera.status and syncs Camera.status in the DB.

The pipeline's connection_monitor.py has published this topic since it
was written ("Kafka status publishing so backend/frontend track camera
health" per its own docstring), but no backend consumer ever existed for
it -- Camera.status stayed permanently stuck at its initial DB default
regardless of the camera's real connection state. Frontend's
useDetectionMetadata hook gates live metadata subscription on
`camera.status === 'online'`, so this gap silently broke camera-feed
alert-border highlighting for every camera that was never manually
PATCHed to 'online'.
"""
import logging

import orjson
from aiokafka import AIOKafkaConsumer
from sqlalchemy import update

from app.config.kafka import GROUP_HEALTH_MONITOR, TOPIC_CAMERA_STATUS
from app.db.engine import async_session
from app.db.models import Camera, CameraStatus

logger = logging.getLogger(__name__)

# connection_monitor.py's ConnectionState -> Camera.status
_STATE_MAP = {
    "connecting": CameraStatus.offline,
    "active": CameraStatus.online,
    "degraded": CameraStatus.warning,
    "down": CameraStatus.offline,
    "reconnecting": CameraStatus.offline,
    "failed": CameraStatus.offline,
    "removing": CameraStatus.offline,
}


class CameraStatusConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task = None

    async def start(self):
        import asyncio

        self._consumer = AIOKafkaConsumer(
            TOPIC_CAMERA_STATUS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_HEALTH_MONITOR,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("CameraStatusConsumer started")

    async def stop(self):
        import asyncio

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        logger.info("CameraStatusConsumer stopped")

    async def _consume_loop(self):
        import asyncio

        try:
            async for msg in self._consumer:
                await self._handle(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("CameraStatusConsumer error")

    async def _handle(self, payload: dict):
        camera_id = payload.get("camera_id")
        state = payload.get("state")
        if not camera_id or state not in _STATE_MAP:
            return

        new_status = _STATE_MAP[state]
        try:
            async with async_session() as session:
                await session.execute(
                    update(Camera)
                    .where(Camera.id == camera_id)
                    .values(status=new_status)
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Failed to update status for camera %s -> %s", camera_id, new_status
            )
