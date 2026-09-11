"""Structured output schema for MiniCPM-o object-release verification.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
section 15. This is a SEPARATE schema from VLMResponse (schemas.py) --
that one is the existing single-crop trigger-path response; this one is
for the temporal, multi-keyframe release-verification call (spec
section 14), which asks different questions and must never be conflated
with the existing trigger path's severity/deterrent_message fields.

Same parse-with-fallback pattern as parse_vlm_response, reused
deliberately for consistency with the existing trigger path.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ReleaseVerdict(str, Enum):
    confirmed = "confirmed"
    retrieved = "retrieved"
    insufficient_evidence = "insufficient_evidence"


class ReleaseVerificationResponse(BaseModel):
    """Spec section 15's JSON contract, field-for-field."""
    event_type: str = "possible_dumping"
    verdict: ReleaseVerdict = ReleaseVerdict.insufficient_evidence
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    subject_type: str = ""
    object_description: str = ""
    location_description: str = ""
    actions: list[str] = Field(default_factory=list)
    retrieved: bool = False
    uncertainty: str = ""
    admin_summary: str = ""


def parse_release_verification_response(raw_text: str) -> ReleaseVerificationResponse | None:
    """Extract and validate MiniCPM-o's release-verification JSON.

    Deliberately does NOT reuse parse_vlm_response's model_validate target
    (VLMResponse is a different shape) -- but reuses its markdown-fence
    and JSON-object extraction logic via the same regex approach, kept
    separate here to avoid coupling the two response contracts together.
    """
    import json
    import re

    if not raw_text:
        return None

    text = raw_text.strip()
    fence_pattern = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
    match = fence_pattern.search(text)
    if match:
        text = match.group(1).strip()

    json_pattern = re.compile(r"\{.*\}", re.DOTALL)
    json_match = json_pattern.search(text)
    if json_match:
        text = json_match.group(0)

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    try:
        return ReleaseVerificationResponse.model_validate(data)
    except Exception:
        return None
