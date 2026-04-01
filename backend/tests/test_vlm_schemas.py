"""Tests for VLM response schemas, parsing, and prompt building."""
import pytest
from pydantic import ValidationError

from app.vlm.schemas import (
    VLMResponse,
    VLMSeverity,
    WasteType,
    parse_vlm_response,
)
from app.vlm.prompts import build_prompt


# ---------------------------------------------------------------------------
# test_parse_valid_json
# ---------------------------------------------------------------------------

def test_parse_valid_json():
    raw = """{
        "person_description": "man in blue jacket",
        "action": "dropping bags",
        "is_dumping": true,
        "waste_type": "plastic_bag",
        "severity": "critical",
        "confidence": 0.92,
        "reasoning": "Clear video evidence of bag being dropped.",
        "command": "dispatch patrol"
    }"""
    result = parse_vlm_response(raw)
    assert result is not None
    assert result.is_dumping is True
    assert result.waste_type == WasteType.plastic_bag
    assert result.severity == VLMSeverity.critical
    assert result.confidence == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# test_parse_json_in_markdown
# ---------------------------------------------------------------------------

def test_parse_json_in_markdown():
    raw = """Sure, here is the analysis:

```json
{
    "person_description": "woman in red coat",
    "action": "walking past",
    "is_dumping": false,
    "waste_type": "none",
    "severity": "info",
    "confidence": 0.1,
    "reasoning": "No dumping observed.",
    "command": "no action"
}
```
"""
    result = parse_vlm_response(raw)
    assert result is not None
    assert result.is_dumping is False
    assert result.waste_type == WasteType.none
    assert result.severity == VLMSeverity.info


# ---------------------------------------------------------------------------
# test_parse_invalid_json
# ---------------------------------------------------------------------------

def test_parse_invalid_json():
    raw = "This is not JSON at all."
    result = parse_vlm_response(raw)
    assert result is None


# ---------------------------------------------------------------------------
# test_parse_missing_fields
# ---------------------------------------------------------------------------

def test_parse_missing_fields():
    # Missing required enumerated fields — but VLMResponse has defaults for
    # all fields, so a nearly-empty object should succeed with defaults.
    raw = '{"is_dumping": true}'
    result = parse_vlm_response(raw)
    # Should succeed because all fields have defaults
    assert result is not None
    assert result.is_dumping is True
    assert result.waste_type == WasteType.none  # default
    assert result.severity == VLMSeverity.info  # default


# ---------------------------------------------------------------------------
# test_confidence_bounds
# ---------------------------------------------------------------------------

def test_confidence_bounds():
    with pytest.raises(ValidationError):
        VLMResponse(confidence=1.5)

    with pytest.raises(ValidationError):
        VLMResponse(confidence=-0.1)

    # Boundary values must succeed
    ok_high = VLMResponse(confidence=1.0)
    assert ok_high.confidence == 1.0

    ok_low = VLMResponse(confidence=0.0)
    assert ok_low.confidence == 0.0


# ---------------------------------------------------------------------------
# test_build_prompt
# ---------------------------------------------------------------------------

def test_build_prompt():
    prompt = build_prompt(
        camera_id="cam-01",
        zone_name="North Gate",
        track_id=42,
        dwell_time=12.5,
        class_name="person",
        path_summary="entered from left, stopped near bins",
    )
    assert "cam-01" in prompt
    assert "North Gate" in prompt
    assert "42" in prompt
    assert "12.5" in prompt
    assert "person" in prompt
    assert "entered from left" in prompt
    # Ensure literal braces in JSON example survived formatting
    assert "{" in prompt
