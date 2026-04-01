"""VLM client for vLLM OpenAI-compatible API."""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.config.settings import settings
from app.vlm.prompts import build_prompt
from app.vlm.schemas import VLMRequest, VLMResponse, VLMResult, VLMSeverity, WasteType, parse_vlm_response

logger = logging.getLogger(__name__)

_STRICT_SUFFIX = "\n\nRespond with ONLY valid JSON. No markdown, no explanation."


class VLMClient:
    """Async client that sends image frames to a vLLM endpoint for analysis."""

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.vlm_base_url,
            timeout=settings.vlm_timeout_s,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze(self, request: VLMRequest) -> Optional[VLMResult]:
        """Analyse frames from a VLMRequest and return a VLMResult.

        Retries once with a stricter prompt if the initial response cannot be
        parsed. Returns a fallback VLMResult if both attempts fail.
        """
        start = time.monotonic()

        system_prompt = build_prompt(
            camera_id=request.camera_id,
            zone_name=request.zone_name,
            track_id=request.track_id,
            dwell_time=request.dwell_time_s,
            class_name=request.class_name,
            path_summary=request.path_summary,
        )

        # First attempt
        raw = await self._call_vlm(system_prompt, request.frames_base64)
        response = parse_vlm_response(raw) if raw is not None else None

        # Retry with stricter prompt if parsing failed
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
        """Close the underlying HTTP client."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_vlm(
        self, system_prompt: str, frames_base64: list[str]
    ) -> Optional[str]:
        """Send one chat completion request and return the raw assistant text."""
        content: list[dict] = []
        for frame in frames_base64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame}"},
                }
            )
        content.append({"type": "text", "text": "Analyse the frames above."})

        payload = {
            "model": settings.vlm_model,
            "max_tokens": settings.vlm_max_tokens,
            "temperature": settings.vlm_temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }

        try:
            resp = await self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("VLM HTTP call failed: %s", exc)
            return None
