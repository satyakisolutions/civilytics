"""VLM Kafka worker — consumes vlm.requests, publishes vlm.results.

Runs a persistent per-camera ffmpeg grabber that keeps
/tmp/vlm_frames/{cam}.jpg fresh (1fps, -update 1). Trigger-time latency
drops from ~5s (spawn-per-request) to <1s (disk read).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
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

FRAME_CACHE_DIR = Path(os.environ.get("WW_VLM_FRAME_CACHE", "/tmp/vlm_frames"))
FRAME_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
        self._grabbers: dict[str, subprocess.Popen] = {}

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

        for cam_id, proc in self._grabbers.items():
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._grabbers.clear()

        logger.info("VLMWorker stopped")

    def _ensure_grabber(self, camera_id: str) -> None:
        """Spawn (or respawn) a persistent ffmpeg writing latest JPEG to disk."""
        proc = self._grabbers.get(camera_id)
        if proc is not None and proc.poll() is None:
            return
        rtsp_url = f"rtsp://mediamtx:8554/{camera_id}-out"
        out_path = FRAME_CACHE_DIR / f"{camera_id}.jpg"
        cmd = [
            "ffmpeg", "-y",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-vf", "fps=2,scale=960:-1",
            "-q:v", "6",
            "-update", "1",
            str(out_path),
        ]
        try:
            new_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            self._grabbers[camera_id] = new_proc
            logger.info("Spawned persistent ffmpeg grabber for %s → %s", camera_id, out_path)
        except Exception:
            logger.exception("Failed to spawn grabber for %s", camera_id)

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
    def _crop_to_bbox(jpeg_b64: str, bbox: dict, padding: float = 0.30) -> str:
        """Crop a base64 JPEG to the trigger bbox with padding.

        This ensures the VLM only sees the object that triggered the alert,
        not bystanders elsewhere in the frame.
        """
        try:
            from PIL import Image
            import io
            img_bytes = base64.b64decode(jpeg_b64)
            img = Image.open(io.BytesIO(img_bytes))
            w, h = img.size
            # bbox is normalized 0-1
            x1 = max(0, bbox.get("x", 0) - padding) * w
            y1 = max(0, bbox.get("y", 0) - padding) * h
            x2 = min(1, bbox.get("x", 0) + bbox.get("w", 0) + padding) * w
            y2 = min(1, bbox.get("y", 0) + bbox.get("h", 0) + padding) * h
            cropped = img.crop((int(x1), int(y1), int(x2), int(y2)))
            buf = io.BytesIO()
            cropped.save(buf, format="JPEG", quality=80)
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:
            logger.debug("Crop failed: %s — using full frame", exc)
            return jpeg_b64

    def _grab_rtsp_frame(self, camera_id: str) -> str | None:
        """Return base64 JPEG of the latest cached frame for camera_id.

        Persistent ffmpeg keeps /tmp/vlm_frames/{cam}.jpg fresh at 2fps.
        Falls back to one-shot ffmpeg capture if cache missing/stale.
        """
        self._ensure_grabber(camera_id)
        cache_path = FRAME_CACHE_DIR / f"{camera_id}.jpg"
        # Wait briefly for grabber's first write (cold start) or stale read
        import time as _t
        for _ in range(8):
            if cache_path.exists() and cache_path.stat().st_size > 1000:
                age = _t.time() - cache_path.stat().st_mtime
                if age < 3.0:
                    try:
                        return base64.b64encode(cache_path.read_bytes()).decode("ascii")
                    except Exception:
                        break
            _t.sleep(0.25)
        # Fallback: one-shot grab
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            result = subprocess.run(
                ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-t", "5",
                 "-i", f"rtsp://mediamtx:8554/{camera_id}-out",
                 "-frames:v", "1", "-vf", "scale=640:-1", "-q:v", "8", tmp_path],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                data = Path(tmp_path).read_bytes()
                Path(tmp_path).unlink(missing_ok=True)
                if len(data) > 1000:
                    return base64.b64encode(data).decode("ascii")
        except Exception:
            pass
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
                    if request.trigger_bbox:
                        cropped = self._crop_to_bbox(frame, request.trigger_bbox)
                        # Send cropped (focus) + full (context)
                        request.frames_base64 = [cropped, frame]
                        request.frame_timestamps = [0.0, 0.0]
                        logger.info("Cropped+full frames for %s (crop %d KB, full %d KB)",
                                    request.camera_id, len(cropped)*3//4//1024, len(frame)*3//4//1024)
                    else:
                        request.frames_base64 = [frame]
                        request.frame_timestamps = [0.0]
                        logger.info("Full frame for %s (%d KB)", request.camera_id, len(frame)*3//4//1024)
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
