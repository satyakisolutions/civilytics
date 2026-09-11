# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""VLM video frame summarizer -- sends frame batches to Qwen2.5-VL-7B.

Takes a batch of decoded RGB frames from a single camera stream and
sends them to the vLLM backend for multi-frame video summarization.

The prompt instructs the model to describe:
1. What activity is happening
2. Number and type of people/vehicles
3. Any suspicious or illegal waste dumping behavior
4. Environmental context (time of day, weather, location type)
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time

import httpx
import torch
from PIL import Image

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
You are the WasteWatch video intelligence system analyzing surveillance footage \
from camera {camera_id}. These {n} frames span {duration}s of video.

Provide a concise JSON summary:
{{
  "camera_id": "{camera_id}",
  "activity_summary": "...",
  "people_count": 0,
  "vehicle_count": 0,
  "vehicle_types": [],
  "suspicious_activity": false,
  "waste_detected": false,
  "waste_description": "",
  "environment": "...",
  "risk_level": "none|low|medium|high|critical",
  "recommended_action": "..."
}}

Rules:
- risk_level must be one of: none, low, medium, high, critical.
- suspicious_activity=true only if there is clear visual evidence of unusual behavior.
- waste_detected=true only when waste material is clearly visible in the scene.
- vehicle_types should list observed types (e.g. ["auto-rickshaw", "truck"]).
- Respond with ONLY valid JSON. No markdown, no explanation.\
"""


def _tensor_to_base64(frame_tensor) -> str:
    """Convert a DeepStream frame tensor (RGB, HWC) to a base64 JPEG string."""
    torch_tensor = torch.utils.dlpack.from_dlpack(frame_tensor)
    numpy_frame = torch_tensor.cpu().numpy()
    img = Image.fromarray(numpy_frame)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class VLMSummarizer:
    """Synchronous VLM client for multi-frame video summarization.

    Uses httpx.Client (synchronous) because this is called from worker
    threads, not an async event loop.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        max_tokens: int = 512,
        temperature: float = 0.3,
        timeout_s: int = 60,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summarize(
        self,
        camera_id: str,
        frames: list,
        timestamps: list[int],
    ) -> dict:
        """Summarize a batch of frames from a single camera.

        Args:
            camera_id: Identifier for the source camera.
            frames: List of DeepStream tensor objects (RGB, HWC).
            timestamps: List of frame timestamps in nanoseconds.

        Returns:
            Dict with camera_id, summary fields, timestamps, and
            inference_time_ms.
        """
        start = time.monotonic()

        # Convert frame tensors to base64 JPEGs
        frames_b64: list[str] = []
        for frame_tensor in frames:
            try:
                frames_b64.append(_tensor_to_base64(frame_tensor))
            except Exception:
                logger.warning("%s: failed to encode a frame, skipping", camera_id)

        if not frames_b64:
            logger.error("%s: all frames failed to encode", camera_id)
            return self._fallback(camera_id, timestamps, start)

        # Build duration from first/last timestamp
        duration_s = (timestamps[-1] - timestamps[0]) / 1e9 if len(timestamps) > 1 else 0.0

        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(
            camera_id=camera_id,
            n=len(frames_b64),
            duration=f"{duration_s:.0f}",
        )

        # Build multi-image content array
        content: list[dict] = []
        for frame_b64 in frames_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{frame_b64}"},
                }
            )
        content.append({"type": "text", "text": "Analyze these surveillance frames."})

        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }

        try:
            resp = self._http.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("%s: VLM HTTP call failed: %s", camera_id, exc)
            return self._fallback(camera_id, timestamps, start)

        # Parse JSON from response (strip markdown fences if present)
        summary = self._parse_json(raw_text, camera_id)

        elapsed_ms = (time.monotonic() - start) * 1000.0
        summary["camera_id"] = camera_id
        summary["timestamps_ns"] = timestamps
        summary["inference_time_ms"] = round(elapsed_ms, 1)
        summary["frames_analyzed"] = len(frames_b64)
        return summary

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json(raw: str, camera_id: str) -> dict:
        """Extract a JSON object from VLM output, tolerating markdown fences."""
        text = raw.strip()
        # Strip ```json ... ``` fences
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1 :]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("%s: failed to parse VLM JSON, returning raw text", camera_id)
            return {
                "activity_summary": text[:500],
                "parse_error": True,
                "risk_level": "low",
            }

    @staticmethod
    def _fallback(camera_id: str, timestamps: list[int], start: float) -> dict:
        """Return a safe fallback summary when VLM inference fails."""
        elapsed_ms = (time.monotonic() - start) * 1000.0
        return {
            "camera_id": camera_id,
            "activity_summary": "VLM inference failed",
            "people_count": 0,
            "vehicle_count": 0,
            "vehicle_types": [],
            "suspicious_activity": False,
            "waste_detected": False,
            "waste_description": "",
            "environment": "unknown",
            "risk_level": "none",
            "recommended_action": "manual review",
            "timestamps_ns": timestamps,
            "inference_time_ms": round(elapsed_ms, 1),
            "frames_analyzed": 0,
            "error": True,
        }
