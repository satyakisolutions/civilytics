"""Pre-approved neutral public-warning templates.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
sections 16-17. This is deliberately a SEPARATE module from the existing
tts/engine.py, which hardcodes an aggressive, all-caps, shaming tone
("YOU ARE BEING RECORDED" etc.) and speaks arbitrary VLM-generated text
verbatim -- exactly what section 16 says must NOT be used for public
warnings. This module never accepts free-form text: only a template_id
from a fixed whitelist plus a safe_zone_name, so there is no code path
by which a model's raw output can reach the public speaker.

engine.py's rewrite is tracked separately (see the Approach 2 findings in
docs/wastewatch-approaches.pdf, section on TTS conflict) and is
intentionally out of scope here -- this module does not replace or call
into engine.py; it exists so the bag-trigger feature has a safe path
available without depending on that unresolved rewrite.
"""
from __future__ import annotations

import re

_SAFE_ZONE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 \-]{1,40}$")

# Fixed whitelist, per spec section 7/16. No template may contain a
# placeholder other than {zone_name}; no caller may pass raw text here.
TEMPLATES: dict[str, str] = {
    "possible_item_left": (
        "Attention. An item may have been left {zone_name}. "
        "Please take it with you and use the designated waste bin."
    ),
    "generic_dumping_prohibited": (
        "Attention. Dumping is prohibited in this monitored area. "
        "Please do not leave or empty any material here."
    ),
    "please_remove_item": (
        "Please use the designated waste bin and remove the item {zone_name}."
    ),
}


class UnsafeZoneNameError(ValueError):
    """Raised when a zone name fails the safety pattern -- e.g. contains
    anything that looks like a personal descriptor rather than a fixed
    location label, per section 16's explicit prohibition on describing
    gender, race, age, clothing, disability, or other personal traits."""


class UnknownTemplateError(ValueError):
    pass


def render_public_warning(template_id: str, zone_name: str | None = None) -> str:
    """Render a whitelisted template. zone_name, if the template needs
    one, must match a simple safe pattern (letters/digits/spaces/hyphens,
    max 40 chars) -- e.g. "near the tree", "at the entrance". This is a
    location label, never a personal description.
    """
    if template_id not in TEMPLATES:
        raise UnknownTemplateError(f"Unknown public warning template: {template_id!r}")

    template = TEMPLATES[template_id]
    if "{zone_name}" not in template:
        return template

    if not zone_name or not _SAFE_ZONE_NAME_PATTERN.match(zone_name):
        raise UnsafeZoneNameError(
            f"zone_name {zone_name!r} failed the safe-pattern check "
            "(letters/digits/spaces/hyphens only, max 40 chars)"
        )
    return template.format(zone_name=zone_name)
