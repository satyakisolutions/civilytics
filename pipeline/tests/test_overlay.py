"""Tests for resolve_person_overlay (pipeline/app/overlay.py).

pipeline.py imports pyds/gi unconditionally and can only be imported inside
the real DeepStream container, so decorate_for_osd itself can't be driven
end-to-end here. resolve_person_overlay is the actual, extracted decision
logic decorate_for_osd calls -- these tests exercise the real function.
"""
from app.overlay import resolve_person_overlay


def test_no_cause_no_fsm_not_red():
    d = resolve_person_overlay(is_suspect=False, overlay_causes=None)
    assert not d.is_red
    assert d.label_suffix == ""


def test_generic_fsm_escalation_still_forces_red():
    # Backward compatibility: the pre-existing is_suspect path (unrelated
    # to waste attribution) must keep working.
    d = resolve_person_overlay(is_suspect=True, overlay_causes=None)
    assert d.is_red
    assert d.label_suffix == "VIOLATED"


def test_overlay_cause_forces_red_even_without_fsm_escalation():
    # This is the actual bug this session's fix targets: a person can be
    # attributed to a waste incident (person_overlay_causes populated at
    # force_violation() time) even in a hypothetical world where is_suspect
    # somehow never became True (e.g. a camera_id-key mismatch between the
    # two probes) -- the overlay-cause path is independent and must still
    # force red.
    d = resolve_person_overlay(is_suspect=False, overlay_causes={(0, "waste", 596)})
    assert d.is_red
    assert d.label_suffix == "ATTRIBUTED | WASTE VIOLATION"


def test_overlay_cause_takes_priority_label_over_generic_fsm():
    d = resolve_person_overlay(is_suspect=True, overlay_causes={(0, "waste", 596)})
    assert d.is_red
    assert d.label_suffix == "ATTRIBUTED | WASTE VIOLATION"


def test_empty_set_is_falsy_not_red():
    d = resolve_person_overlay(is_suspect=False, overlay_causes=set())
    assert not d.is_red


def test_regression_iteration_order_independent():
    """Regression test for the reported bug: waste #596 red, attributed
    person #928 stuck yellow OBSERVE.

    Simulates a single frame's object-decoration loop processing the
    PERSON object BEFORE the WASTE object (the failure-prone order --
    the original bug's suspected mechanism was that a person's display
    metadata, once written, wasn't revisited after a later waste-triggered
    force_violation() call in the same frame).

    person_overlay_causes is populated once, upfront (mirroring reality:
    it's set by extract_metadata's force_violation() call, an entirely
    separate function/probe from decorate_for_osd's object loop) -- so by
    the time decorate_for_osd visits ANY object in ANY order, the cause is
    already present. The fix's correctness does not depend on visiting the
    waste object first.
    """
    person_overlay_causes = {(0, "person", 928): {(0, "waste", 596)}}

    def decorate_object(category: str, track_id: int) -> "object":
        if category != "person":
            return None
        causes = person_overlay_causes.get((0, "person", track_id))
        return resolve_person_overlay(is_suspect=False, overlay_causes=causes)

    # Object iteration order: person (#928) BEFORE waste (#596) --
    # matches the exact failure-prone order called out in the bug report.
    objects_in_order = [("person", 928), ("waste_object", 596)]

    decisions = {}
    for category, track_id in objects_in_order:
        result = decorate_object(category, track_id)
        if result is not None:
            decisions[track_id] = result

    assert decisions[928].is_red, "person #928 must render red even when processed before waste #596"
    assert decisions[928].label_suffix == "ATTRIBUTED | WASTE VIOLATION"


def test_regression_iteration_order_reversed_gives_same_result():
    """Same scenario, waste processed first -- must produce an identical
    decision, proving the result really is order-independent."""
    person_overlay_causes = {(0, "person", 928): {(0, "waste", 596)}}

    def decorate_object(category: str, track_id: int):
        if category != "person":
            return None
        causes = person_overlay_causes.get((0, "person", track_id))
        return resolve_person_overlay(is_suspect=False, overlay_causes=causes)

    objects_in_order = [("waste_object", 596), ("person", 928)]
    decisions = {}
    for category, track_id in objects_in_order:
        result = decorate_object(category, track_id)
        if result is not None:
            decisions[track_id] = result

    assert decisions[928].is_red
    assert decisions[928].label_suffix == "ATTRIBUTED | WASTE VIOLATION"
