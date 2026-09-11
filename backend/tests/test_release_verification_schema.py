"""Tests for the MiniCPM-o release-verification structured output schema."""
from app.vlm.release_verification_schema import (
    ReleaseVerdict,
    parse_release_verification_response,
)


def test_parses_well_formed_json():
    raw = """{
        "event_type": "possible_dumping",
        "verdict": "confirmed",
        "confidence": 0.86,
        "subject_type": "person",
        "object_description": "light-colored bag",
        "location_description": "ground beside the tree",
        "actions": ["subject approached", "item appeared on the ground"],
        "retrieved": false,
        "uncertainty": "brief body occlusion at the release moment",
        "admin_summary": "A person appeared to leave a bag beside the tree."
    }"""
    result = parse_release_verification_response(raw)
    assert result is not None
    assert result.verdict == ReleaseVerdict.confirmed
    assert result.confidence == 0.86
    assert result.retrieved is False


def test_parses_json_inside_markdown_fence():
    raw = '```json\n{"verdict": "retrieved", "confidence": 0.3}\n```'
    result = parse_release_verification_response(raw)
    assert result is not None
    assert result.verdict == ReleaseVerdict.retrieved


def test_returns_none_on_garbage_text():
    assert parse_release_verification_response("not json at all") is None


def test_returns_none_on_empty_text():
    assert parse_release_verification_response("") is None


def test_confidence_out_of_range_fails_validation():
    raw = '{"verdict": "confirmed", "confidence": 1.5}'
    assert parse_release_verification_response(raw) is None


def test_defaults_to_insufficient_evidence_verdict():
    result = parse_release_verification_response("{}")
    assert result is not None
    assert result.verdict == ReleaseVerdict.insufficient_evidence
