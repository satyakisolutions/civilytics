# pipeline/app/overlay.py
"""Pure, pyds-free overlay-decision logic for decorate_for_osd.

pipeline.py imports pyds/gi unconditionally and can only be imported inside
the real DeepStream container, so its object-decoration logic can't be
exercised by a plain unit test. The actual per-object color/label decision
that matters for the "attributed person stays yellow" bug is small and has
no DeepStream dependency at all -- extracted here so it's directly testable,
and to prove the fix is iteration-order independent: the decision only
reads a shared dict (person_overlay_causes), never anything set by visiting
the waste object first in the same frame's object loop.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PersonOverlayDecision:
    is_red: bool
    label_suffix: str  # e.g. "ATTRIBUTED | WASTE VIOLATION" or "" (no override)


def resolve_person_overlay(
    is_suspect: bool,
    overlay_causes: set | None,
) -> PersonOverlayDecision:
    """Decide whether a person's box/label should be forced red.

    `overlay_causes` is person_overlay_causes.get((source_id, "person",
    track_id)) -- a set of waste_keys currently attributing this person as
    a dumper, set at the exact moment force_violation(person) fires,
    independent of the generic FSM (`is_suspect`). Either signal can force
    red; the overlay-cause path takes priority for the label text since it
    names the incident explicitly.

    This function's only inputs are values already resolved by the caller
    for a SPECIFIC track_id -- it does not care what order objects were
    visited in during the frame's object loop, which is exactly what makes
    the fix immune to "person processed before waste" ordering: the waste
    object's own rendering (setting person_overlay_causes) happens in a
    completely separate function (extract_metadata, at force_violation
    time), not during this frame's OSD decoration pass at all.
    """
    has_cause = bool(overlay_causes)
    if has_cause:
        return PersonOverlayDecision(is_red=True, label_suffix="ATTRIBUTED | WASTE VIOLATION")
    if is_suspect:
        return PersonOverlayDecision(is_red=True, label_suffix="VIOLATED")
    return PersonOverlayDecision(is_red=False, label_suffix="")
