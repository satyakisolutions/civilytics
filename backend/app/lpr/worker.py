# backend/app/lpr/worker.py
"""Kafka consumer: wastewatch.lpr → plate recognition."""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_LPR
from app.lpr.ocr import LPREngine

logger = logging.getLogger(__name__)


class LPRWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._engine = LPREngine()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_LPR,
            bootstrap_servers=self._bootstrap_servers,
            group_id="wastewatch.group.lpr-worker",
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("LPRWorker started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await self._process(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("LPRWorker error")

    async def _process(self, data: dict):
        image_path = data.get("image_path")
        if not image_path:
            return
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._engine.read_plate, image_path)
            if result:
                logger.info("Plate detected: %s (%.1f%%)", result["plate_number"], result["confidence"] * 100)
        except Exception:
            logger.exception("LPR processing failed")
