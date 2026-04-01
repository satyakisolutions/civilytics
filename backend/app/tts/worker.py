# backend/app/tts/worker.py
"""Kafka consumer: wastewatch.tts → generate audio files."""
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import TOPIC_TTS
from app.tts.engine import TTSEngine
from app.tts.cache import TTSCache

logger = logging.getLogger(__name__)


class TTSWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None
        self._engine = TTSEngine()
        self._cache = TTSCache()

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_TTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id="wastewatch.group.tts-worker",
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("TTSWorker started")

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
            logger.exception("TTSWorker error")

    async def _process(self, data: dict):
        text = data.get("text", "")
        if not text:
            return
        if self._cache.has(text):
            logger.debug("TTS cache hit for: %s", text[:50])
            return
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._engine.generate, text)
            self._cache.put(text, result["path"], result["duration_ms"])
            logger.info("TTS generated: %s (%dms)", text[:50], result["duration_ms"])
        except Exception:
            logger.exception("TTS generation failed")
