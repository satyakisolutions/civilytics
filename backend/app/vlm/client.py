"""VLM client — in-process SmolVLM2 inference (no external server)."""
from __future__ import annotations

import base64
import io
import logging
import time
from typing import Optional

import torch
from PIL import Image
from transformers import AutoModelForVision2Seq, AutoProcessor

from app.config.settings import settings
from app.vlm.prompts import build_prompt
from app.vlm.schemas import VLMRequest, VLMResponse, VLMResult, VLMSeverity, WasteType, parse_vlm_response

logger = logging.getLogger(__name__)

_STRICT_SUFFIX = "\n\nRespond with ONLY valid JSON. No markdown, no explanation."


class VLMClient:
    """Runs SmolVLM2 in-process for frame analysis."""

    def __init__(self) -> None:
        self._model = None
        self._processor = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        logger.info("Loading SmolVLM2 model: %s", settings.vlm_model)
        self._processor = AutoProcessor.from_pretrained(settings.vlm_model)
        self._model = AutoModelForVision2Seq.from_pretrained(
            settings.vlm_model,
            torch_dtype=torch.float16,
            device_map="auto",
        )
        logger.info("SmolVLM2 loaded")

    async def analyze(self, request: VLMRequest) -> Optional[VLMResult]:
        start = time.monotonic()

        system_prompt = build_prompt(
            camera_id=request.camera_id,
            zone_name=request.zone_name,
            track_id=request.track_id,
            dwell_time=request.dwell_time_s,
            class_name=request.class_name,
            path_summary=request.path_summary,
            trigger_bbox=getattr(request, 'trigger_bbox', None),
        )

        raw = await self._call_vlm(system_prompt, request.frames_base64)
        response = parse_vlm_response(raw) if raw is not None else None

        if response is None:
            logger.warning(
                "VLM parse failed on first attempt for track %s — retrying with strict prompt",
                request.track_id,
            )
            strict_prompt = system_prompt + _STRICT_SUFFIX
            raw = await self._call_vlm(strict_prompt, request.frames_base64)
            response = parse_vlm_response(raw) if raw is not None else None

        if response is None:
            logger.error(
                "VLM parse failed after retry for track %s — using fallback response",
                request.track_id,
            )
            response = VLMResponse(
                person_description="",
                action="unknown",
                is_dumping=False,
                waste_type=WasteType.unknown,
                severity=VLMSeverity.warning,
                confidence=0.0,
                reasoning="VLM response could not be parsed after retry.",
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
        del self._model
        del self._processor
        self._model = None
        self._processor = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    async def _call_vlm(
        self, system_prompt: str, frames_base64: list[str]
    ) -> Optional[str]:
        """Run SmolVLM2 inference in-process."""
        self._ensure_loaded()

        images = []
        for b64 in frames_base64:
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
            images.append(img)

        if len(frames_base64) >= 2:
            user_text = "Image 1 is a CROPPED close-up of the suspect object that triggered the alert. Image 2 is the full scene for context. Describe the object/person in Image 1 — that is the one in the restricted zone. Generate the deterrent_message addressing them specifically."
        else:
            user_text = "Analyse this frame. Focus on the object at the trigger location mentioned in the system prompt. Describe it and generate a deterrent_message addressing it specifically."

        # Build chat messages for SmolVLM2
        image_tokens = [{"type": "image"} for _ in images]
        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": image_tokens + [{"type": "text", "text": user_text}]},
        ]

        prompt = self._processor.apply_chat_template(messages, add_generation_prompt=True)
        inputs = self._processor(text=prompt, images=images, return_tensors="pt").to(self._model.device)

        try:
            with torch.inference_mode():
                output_ids = self._model.generate(
                    **inputs,
                    max_new_tokens=settings.vlm_max_tokens,
                    temperature=settings.vlm_temperature,
                    do_sample=settings.vlm_temperature > 0,
                )
            # Decode only new tokens
            generated = output_ids[0, inputs["input_ids"].shape[1]:]
            return self._processor.decode(generated, skip_special_tokens=True)
        except Exception as exc:
            logger.error("SmolVLM2 inference failed: %s", exc)
            return None
