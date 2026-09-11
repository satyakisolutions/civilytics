# backend/app/logs/worker.py
"""Kafka consumer: wastewatch.logs → embed → Qdrant + DB."""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_LOGS, GROUP_LOG_INGESTION
from app.logs.qdrant_store import QdrantLogStore

logger = logging.getLogger(__name__)


class LogWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._qdrant = QdrantLogStore()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_LOGS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_LOG_INGESTION,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("LogWorker started")

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
                await self._qdrant.store(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("LogWorker error")
