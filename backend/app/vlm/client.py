"""VLM client — Moondream2 in-process inference."""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from typing import Optional

from PIL import Image

from app.config.settings import settings
from app.vlm.schemas import VLMRequest, VLMResponse, VLMResult, VLMSeverity, WasteType, parse_vlm_response

logger = logging.getLogger(__name__)

_PROMPT = (
    "Describe what is happening in this surveillance footage. "
    "Focus on: people (clothing, age, gender, what they carry), "
    "vehicles (type, color), and any objects being placed or removed. "
    "Be specific and factual. Keep under 100 words."
)


class VLMClient:
    """Runs Moondream2 in-process for frame analysis."""

    def __init__(self) -> None:
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import moondream as md
        logger.info("Loading VLM model: %s", settings.vlm_model)
        self._model = md.vl(local=True, model=settings.vlm_model)
        logger.info("VLM loaded (Moondream2)")

    async def analyze(self, request: VLMRequest) -> Optional[VLMResult]:
        start = time.monotonic()

        raw = await self._call_vlm(request.frames_base64)
        logger.warning("VLM raw output for track %s: %.500s", request.track_id, raw or "")
        response = parse_vlm_response(raw) if raw else None

        if response is None:
            desc = raw.strip() if raw else "VLM returned no output."
            response = VLMResponse(
                person_description=desc,
                action="unknown",
                is_dumping=False,
                waste_type=WasteType.unknown,
                severity=VLMSeverity.warning,
                confidence=0.0,
                reasoning=desc,
                command="monitor",
            )

        elapsed_ms = (time.monotonic() - start) * 1000.0

        return VLMResult(
            camera_id=request.camera_id,
            track_id=request.track_id,
            track_session_id=request.track_session_id,
            response=response,
            inference_time_ms=elapsed_ms,
            model_version=settings.vlm_model,
            frames_analyzed=len(request.frames_base64),
        )

    async def close(self) -> None:
        self._model = None

    async def _call_vlm(self, frames_base64: list[str]) -> Optional[str]:
        self._ensure_loaded()

        max_px = settings.vlm_max_image_px
        # ponytail: single best frame — moondream2 is single-image, pick middle frame
        idx = len(frames_base64) // 2
        b64 = frames_base64[idx]
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        img.thumbnail((max_px, max_px))

        try:
            result = await asyncio.to_thread(self._query_sync, img)
            return result
        except Exception as exc:
            logger.error("VLM inference failed: %s", exc)
            return None

    def _query_sync(self, img: Image.Image) -> str:
        return self._model.query(img, _PROMPT)["answer"]
