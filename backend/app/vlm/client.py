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

_FRAME_PROMPT = (
    "List the key objects and actions visible: people (clothing, what they carry), "
    "vehicles, and any items on the ground. Two sentences max."
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
        # ponytail: sample up to 3 frames (first, middle, last) for temporal coverage
        n = len(frames_base64)
        if n <= 3:
            indices = list(range(n))
        else:
            indices = [0, n // 2, n - 1]

        images = []
        for idx in indices:
            img = Image.open(io.BytesIO(base64.b64decode(frames_base64[idx]))).convert("RGB")
            img.thumbnail((max_px, max_px))
            images.append(img)

        try:
            return await asyncio.to_thread(self._query_all_sync, images)
        except Exception as exc:
            logger.error("VLM inference failed: %s", exc)
            return None

    def _query_all_sync(self, images: list[Image.Image]) -> str:
        if len(images) == 1:
            return self._model.query(images[0], _FRAME_PROMPT)["answer"]

        # Query earlier frames for short observations
        observations = []
        labels = ["Start", "Middle", "End"] if len(images) == 3 else [f"Frame {i+1}" for i in range(len(images))]
        for i, img in enumerate(images[:-1]):
            obs = self._model.query(img, _FRAME_PROMPT)["answer"]
            observations.append(f"{labels[i]}: {obs}")

        # Final frame gets a synthesis prompt that includes earlier observations
        context = "\n".join(observations)
        synthesis_prompt = (
            f"Earlier frames from this surveillance clip showed:\n{context}\n\n"
            "Now describe this final frame and summarize the full sequence: "
            "who arrived, what they did, what changed, did anyone dump or remove objects? "
            "Be specific about the complete event. Under 150 words."
        )
        summary = self._model.query(images[-1], synthesis_prompt)["answer"]
        return summary
