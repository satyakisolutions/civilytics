# backend/app/logs/worker.py
"""Kafka consumer: wastewatch.logs -> Postgres.

Postgres is the only sink. This previously embedded each line into Qdrant,
but Qdrant was dropped from the test and production stacks, so the write
target did not exist in either -- messages were consumed and lost. The
docstring also claimed "+ DB" while no database write was ever performed.
"""
import asyncio
import logging
from datetime import datetime, timezone

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_LOGS, GROUP_LOG_INGESTION
from app.logs.client_ingest import persist

logger = logging.getLogger(__name__)


class LogWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

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
                await self._store(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("LogWorker error")

    async def _store(self, payload: dict) -> None:
        """Write one log message as a row.

        A malformed message must not break the consumer loop: a single bad
        producer would otherwise stall ingestion for every service.
        """
        try:
            await persist([
                {
                    "service": str(payload.get("service") or "unknown")[:100],
                    "level": str(payload.get("level") or "info").lower()[:20],
                    "message": str(payload.get("message") or "")[:8000],
                    "meta": payload.get("metadata") or payload.get("meta"),
                    "timestamp": payload.get("timestamp") or datetime.now(timezone.utc),
                }
            ])
        except Exception:
            logger.exception("Dropped a log message that could not be stored")
