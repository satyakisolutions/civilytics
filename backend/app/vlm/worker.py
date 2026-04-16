"""VLM Kafka worker — consumes vlm.requests, publishes vlm.results."""
from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
from pathlib import Path

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

    @staticmethod
    def _grab_rtsp_frame(camera_id: str) -> str | None:
        """Grab a single JPEG frame from the camera's RTSP input stream.

        Uses ffmpeg to capture 1 frame from the raw RTSP input (not the
        annotated output — we want clean frames for VLM analysis).
        Returns base64-encoded JPEG or None on failure.
        """
        # Try annotated output first (has bboxes), fall back to raw input
        rtsp_urls = [
            f"rtsp://mediamtx:8554/{camera_id}-out",
            f"rtsp://mediamtx:8554/{camera_id}",
        ]
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            for rtsp_url in rtsp_urls:
                try:
                    result = subprocess.run(
                        [
                            "ffmpeg", "-y", "-rtsp_transport", "tcp",
                            "-t", "5",
                            "-i", rtsp_url,
                            "-frames:v", "1", "-q:v", "5",
                            tmp_path,
                        ],
                        capture_output=True, timeout=12,
                    )
                    if result.returncode == 0:
                        data = Path(tmp_path).read_bytes()
                        if len(data) > 1000:
                            Path(tmp_path).unlink(missing_ok=True)
                            return base64.b64encode(data).decode("ascii")
                except subprocess.TimeoutExpired:
                    continue
            Path(tmp_path).unlink(missing_ok=True)
            return None
        except Exception:
            logger.debug("Frame grab failed for %s", camera_id, exc_info=True)
            return None

    async def _handle_message(self, payload: dict) -> None:
        async with self._semaphore:
            try:
                request = VLMRequest.model_validate(payload)
            except ValidationError as exc:
                logger.warning("Invalid VLMRequest payload: %s", exc)
                return

            # If no frames were provided, grab one from RTSP stream
            if not request.frames_base64:
                logger.info("No frames in VLM request for %s — grabbing from RTSP", request.camera_id)
                frame = await asyncio.get_event_loop().run_in_executor(
                    None, self._grab_rtsp_frame, request.camera_id
                )
                if frame:
                    request.frames_base64 = [frame]
                    request.frame_timestamps = [0.0]
                    logger.info("Grabbed RTSP frame for %s (%d KB)", request.camera_id, len(frame) * 3 // 4 // 1024)
                else:
                    logger.warning("Could not grab frame for %s — VLM will analyze without image", request.camera_id)

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
