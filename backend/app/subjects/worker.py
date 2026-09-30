"""Kafka worker: wastewatch.faces.requests -> FaceAnalyzer -> SubjectService.

Offsets are committed manually after each message. Redelivery is harmless
(face_incidents makes processing idempotent); malformed messages are
logged and skipped; transient failures are retried a few times, then
skipped so one poison message cannot stall the queue. Infrastructure
errors (DB/connection outages: OperationalError, InterfaceError,
ConnectionError) are retried indefinitely with capped exponential backoff
instead of consuming that bounded retry budget, so a sustained outage
stalls the consumer rather than silently dropping incidents. Data errors
(IntegrityError, DataError, ...) take the bounded path.

A failed offset commit (e.g. after a long outage exceeded aiokafka's
max_poll_interval_ms) is logged and ignored. If either background task
dies, `failed` is set so the entrypoint can exit non-zero and be restarted.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import orjson
from aiokafka import AIOKafkaConsumer
from sqlalchemy.exc import InterfaceError, OperationalError

from app.config.kafka import GROUP_SUBJECT_WORKER, TOPIC_FACES_REQUESTS
from app.subjects.config import SubjectSettings
from app.subjects.service import ProcessResult, SubjectService
from app.subjects.types import FaceRequest

logger = logging.getLogger(__name__)

# Errors meaning "the DB/broker/Qdrant is unreachable", retried until it is back.
INFRA_ERRORS = (OperationalError, InterfaceError, ConnectionError)
# 2**16 * backoff_s is far past any sane max_backoff_s; capping the exponent
# avoids float OverflowError after ~1024 consecutive infra failures.
_MAX_BACKOFF_EXPONENT = 16


def _loads(raw: bytes):
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None


def _backoff(backoff_s: float, max_backoff_s: float, n: int) -> float:
    """Capped exponential backoff for the n-th consecutive infra failure."""
    return min(max_backoff_s, backoff_s * 2 ** min(n, _MAX_BACKOFF_EXPONENT))


class SubjectWorker:
    def __init__(
        self,
        bootstrap_servers: str,
        service: SubjectService,
        analyzer,
        settings: SubjectSettings,
        consumer_factory=AIOKafkaConsumer,
        resync_every_s: float = 600.0,
        sweep_every_s: float = 86400.0,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._service = service
        self._analyzer = analyzer
        self._settings = settings
        self._consumer_factory = consumer_factory
        self._resync_every_s = resync_every_s
        self._sweep_every_s = sweep_every_s
        self._consumer = None
        self._tasks: list[asyncio.Task] = []
        self._failed = asyncio.Event()

    @property
    def failed(self) -> bool:
        """True once a background task has died with an exception."""
        return self._failed.is_set()

    async def wait_failed(self) -> None:
        await self._failed.wait()

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.error("SubjectWorker task %s crashed", task.get_name(), exc_info=exc)
        self._failed.set()

    async def handle(self, payload: dict) -> ProcessResult:
        req = FaceRequest.from_payload(payload, Path(self._settings.faces_dir) / "incoming")
        faces = await asyncio.to_thread(self._analyzer, list(req.crops))
        result = await self._service.process_request(req, faces)
        logger.info(
            "Face incident %s cam=%s -> %s %s (%s, sim=%s, faces=%d)",
            req.incident_id, req.camera_id, result.status, result.subject_code,
            result.match_type, None if result.similarity is None else round(result.similarity, 3), len(faces),
        )
        return result

    async def handle_with_retry(
        self, payload, attempts: int = 3, backoff_s: float = 1.0, max_backoff_s: float = 30.0
    ) -> ProcessResult | None:
        attempt = 0
        infra_failures = 0
        while True:
            try:
                return await self.handle(payload)
            except (KeyError, ValueError, TypeError):
                logger.exception("Malformed face request skipped: %s", str(payload)[:200])
                return None
            except INFRA_ERRORS:
                infra_failures += 1
                logger.warning(
                    "Face request failed due to an infrastructure error; retrying until it "
                    "recovers (infra failure #%d)", infra_failures, exc_info=True,
                )
                await asyncio.sleep(_backoff(backoff_s, max_backoff_s, infra_failures))
            except Exception:
                attempt += 1
                logger.exception("Face request failed (attempt %d/%d)", attempt, attempts)
                if attempt >= attempts:
                    return None
                await asyncio.sleep(backoff_s * attempt)

    async def start(self) -> None:
        self._consumer = self._consumer_factory(
            TOPIC_FACES_REQUESTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_SUBJECT_WORKER,
            value_deserializer=_loads,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="subject-consume"),
            asyncio.create_task(self._maintenance_loop(), name="subject-maintenance"),
        ]
        for task in self._tasks:
            task.add_done_callback(self._on_task_done)
        logger.info("SubjectWorker consuming %s", TOPIC_FACES_REQUESTS)

    async def _consume_loop(self) -> None:
        async for msg in self._consumer:
            await self.handle_with_retry(msg.value)
            try:
                await self._consumer.commit()
            except Exception:
                # Redelivery is harmless (processing is idempotent).
                logger.warning("Offset commit failed; message may be redelivered", exc_info=True)

    async def _maintenance_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_sweep: float | None = None
        while True:
            try:
                await self._service.resync_index()
                if last_sweep is None or loop.time() - last_sweep >= self._sweep_every_s:
                    await self._service.sweep()
                    last_sweep = loop.time()
            except Exception:
                logger.exception("Subject maintenance tick failed")
            await asyncio.sleep(self._resync_every_s)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass  # already logged by _on_task_done
        if self._consumer is not None:
            await self._consumer.stop()
