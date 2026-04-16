"""VLM response schemas and validation utilities."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class WasteType(str, Enum):
    plastic_bag = "plastic_bag"
    construction_debris = "construction_debris"
    household_waste = "household_waste"
    organic = "organic"
    unknown = "unknown"
    none = "none"


class VLMSeverity(str, Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class VLMResponse(BaseModel):
    person_description: str = ""
    action: str = ""
    is_dumping: bool = False
    waste_type: WasteType = WasteType.none
    severity: VLMSeverity = VLMSeverity.info
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    command: str = ""


class VLMRequest(BaseModel):
    camera_id: str
    track_id: int
    track_session_id: str
    zone_name: str
    dwell_time_s: float
    class_name: str
    path_summary: str
    trigger_bbox: dict | None = None
    frame_timestamps: list[float]
    frames_base64: list[str]


class VLMResult(BaseModel):
    camera_id: str
    track_id: int
    track_session_id: str
    response: VLMResponse
    inference_time_ms: float
    model_version: str
    frames_analyzed: int


def parse_vlm_response(raw_text: str) -> Optional[VLMResponse]:
    """Extract JSON from raw LLM text and validate as VLMResponse.

    Handles plain JSON and markdown code fences (```json ... ```).
    Returns None on failure.
    """
    if not raw_text:
        return None

    # Strip markdown code fences
    text = raw_text.strip()
    fence_pattern = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        text = match.group(1).strip()

    # Attempt to find a JSON object in the text
    json_pattern = re.compile(r"\{.*\}", re.DOTALL)
    json_match = json_pattern.search(text)
    if json_match:
        text = json_match.group(0)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        return VLMResponse.model_validate(data)
    except Exception:
        return None
