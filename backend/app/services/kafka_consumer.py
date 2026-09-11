import asyncio
import logging
import orjson
from aiokafka import AIOKafkaConsumer
from app.config.kafka import GROUP_API_METADATA, TOPIC_METADATA
from app.ws.metadata import broadcast_to_all

logger = logging.getLogger(__name__)


class MetadataConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_METADATA,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_API_METADATA,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("MetadataConsumer started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        logger.info("MetadataConsumer stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await broadcast_to_all(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MetadataConsumer error")
