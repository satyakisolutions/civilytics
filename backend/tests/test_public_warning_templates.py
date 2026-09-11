"""Tests for the safe public-warning template renderer."""
import pytest

from app.tts.public_warning_templates import (
    UnknownTemplateError,
    UnsafeZoneNameError,
    render_public_warning,
)


def test_renders_template_with_safe_zone_name():
    result = render_public_warning("possible_item_left", zone_name="near the tree")
    assert "near the tree" in result
    assert result.startswith("Attention.")


def test_generic_template_needs_no_zone_name():
    result = render_public_warning("generic_dumping_prohibited")
    assert "Dumping is prohibited" in result


def test_unknown_template_id_raises():
    with pytest.raises(UnknownTemplateError):
        render_public_warning("does_not_exist")


def test_missing_zone_name_for_template_that_needs_one_raises():
    with pytest.raises(UnsafeZoneNameError):
        render_public_warning("possible_item_left", zone_name=None)


@pytest.mark.parametrize("unsafe_zone_name", [
    "near the man in the blue jacket",  # personal descriptor, not a location
    "<script>alert(1)</script>",
    "a" * 41,  # too long
    "",
])
def test_unsafe_zone_names_are_rejected(unsafe_zone_name):
    with pytest.raises(UnsafeZoneNameError):
        render_public_warning("possible_item_left", zone_name=unsafe_zone_name)


def test_no_template_contains_a_placeholder_other_than_zone_name():
    from app.tts.public_warning_templates import TEMPLATES
    import re

    for template_id, template in TEMPLATES.items():
        placeholders = re.findall(r"\{(\w+)\}", template)
        assert all(p == "zone_name" for p in placeholders), (
            f"template {template_id!r} has an unexpected placeholder: {placeholders}"
        )
