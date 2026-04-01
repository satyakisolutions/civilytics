"""VLM Kafka worker — consumes vlm.requests, publishes vlm.results."""
from __future__ import annotations

import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import ValidationError

from app.config.kafka import GROUP_VLM_WORKER, TOPIC_VLM_REQUESTS, TOPIC_VLM_RESULTS
from app.config.settings import settings
from app.vlm.client import VLMClient
from app.vlm.schemas import VLMRequest

logger = logging.getLogger(__name__)


class VLMWorker:
    """Consumes VLMRequest messages from Kafka, runs VLM inference, and
    publishes VLMResult messages back to Kafka.

    Args:
        bootstrap_servers: Kafka bootstrap servers string.
        max_concurrent: Maximum number of inflight VLM requests (semaphore).
    """

    def __init__(
        self,
        bootstrap_servers: str | None = None,
        max_concurrent: int = 2,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers or settings.kafka_bootstrap_servers
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._client: VLMClient | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._client = VLMClient()

        self._consumer = AIOKafkaConsumer(
            TOPIC_VLM_REQUESTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_VLM_WORKER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="earliest",
        )
        await self._consumer.start()

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
        )
        await self._producer.start()

        self._task = asyncio.create_task(self._consume_loop())
        logger.info("VLMWorker started — consuming %s", TOPIC_VLM_REQUESTS)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._consumer:
            await self._consumer.stop()

        if self._producer:
            await self._producer.stop()

        if self._client:
            await self._client.close()

        logger.info("VLMWorker stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        try:
            async for msg in self._consumer:
                asyncio.create_task(self._handle_message(msg.value))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("VLMWorker consume loop error")

    async def _handle_message(self, payload: dict) -> None:
        async with self._semaphore:
            try:
                request = VLMRequest.model_validate(payload)
            except ValidationError as exc:
                logger.warning("Invalid VLMRequest payload: %s", exc)
                return

            logger.debug(
                "VLMWorker processing track %s (camera=%s)",
                request.track_id,
                request.camera_id,
            )

            result = await self._client.analyze(request)
            if result is None:
                logger.error(
                    "VLMClient returned None for track %s", request.track_id
                )
                return

            await self._producer.send_and_wait(
                TOPIC_VLM_RESULTS,
                value=result.model_dump(),
            )
            logger.info(
                "VLMWorker published result for track %s — is_dumping=%s severity=%s",
                result.track_id,
                result.response.is_dumping,
                result.response.severity,
            )
