"""Tests for the object-release lifecycle state machine."""
from app.object_lifecycle import (
    BoundingBox,
    ObjectLifecycleState,
    ObjectTrack,
    bbox_distance,
    is_increasing,
)


def make_track(**overrides) -> ObjectTrack:
    defaults = dict(camera_id="cam-01", roi_id="tree-side", person_track_id="person-104")
    defaults.update(overrides)
    return ObjectTrack(**defaults)


def test_bbox_distance_center_to_center():
    a = BoundingBox(x=0, y=0, w=10, h=10)  # center (5, 5)
    b = BoundingBox(x=10, y=0, w=10, h=10)  # center (15, 5)
    assert bbox_distance(a, b) == 10.0


def test_is_increasing_true_for_clear_uptrend():
    assert is_increasing([1.0, 2.0, 3.0, 4.0]) is True


def test_is_increasing_false_for_flat_or_decreasing():
    assert is_increasing([5.0, 5.0, 5.0]) is False
    assert is_increasing([5.0, 4.0, 3.0]) is False


def test_is_increasing_false_with_too_few_samples():
    assert is_increasing([1.0]) is False


def test_observe_sets_first_and_last_seen():
    track = make_track()
    bbox = BoundingBox(0, 0, 5, 5)
    track.observe(timestamp=1.0, bbox=bbox, confidence=0.9)
    assert track.first_seen_at == 1.0
    assert track.last_seen_at == 1.0
    track.observe(timestamp=2.0, bbox=bbox, confidence=0.8)
    assert track.first_seen_at == 1.0
    assert track.last_seen_at == 2.0


def test_full_happy_path_release_to_incident():
    """Mirrors the reference clip: object associated with a person,
    separates, reappears stationary, distance keeps increasing."""
    track = make_track()
    bbox = BoundingBox(0, 0, 5, 5)
    track.observe(timestamp=0.0, bbox=bbox, confidence=0.9)
    track.associate(timestamp=0.5)
    assert track.state == ObjectLifecycleState.ASSOCIATED

    track.mark_separated(timestamp=5.0)
    assert track.state == ObjectLifecycleState.RELEASE_CANDIDATE
    assert track.release_at == 5.0

    track.mark_reappeared_stationary(timestamp=5.5, bbox=bbox)
    assert track.state == ObjectLifecycleState.PLACED_PENDING

    subject_positions = [
        BoundingBox(x, 0, 5, 5) for x in (6, 10, 15, 20)
    ]
    for t, subj in zip((5.6, 5.8, 6.0, 6.2), subject_positions):
        track.record_distance_sample(timestamp=t, subject_bbox=subj)

    assert track.crossed_warning_threshold(min_consecutive_samples=3) is True
    assert track.crossed_incident_threshold(persistence_s=1.0, now=6.5) is True

    track.promote_to_abandoned_candidate(timestamp=6.5)
    assert track.state == ObjectLifecycleState.ABANDONED_CANDIDATE

    track.confirm_incident(timestamp=7.0)
    assert track.state == ObjectLifecycleState.CONFIRMED_INCIDENT
    assert track.escalated_at == 7.0


def test_retrieval_closes_as_non_incident_from_release_candidate():
    track = make_track()
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.mark_retrieved(timestamp=1.5)
    assert track.state == ObjectLifecycleState.RETRIEVED


def test_retrieval_closes_as_non_incident_from_placed_pending():
    track = make_track()
    bbox = BoundingBox(0, 0, 5, 5)
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.mark_reappeared_stationary(timestamp=1.5, bbox=bbox)
    track.mark_retrieved(timestamp=2.0)
    assert track.state == ObjectLifecycleState.RETRIEVED


def test_occlusion_does_not_delete_the_track():
    """The core bug fixed vs. the original draft: a brief detector miss
    must not be treated as the object vanishing."""
    track = make_track()
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.mark_occluded(timestamp=1.2)
    assert track.state == ObjectLifecycleState.OCCLUDED_PENDING
    assert track.occluded_since == 1.2

    bbox = BoundingBox(0, 0, 5, 5)
    track.mark_reappeared_stationary(timestamp=1.8, bbox=bbox)
    assert track.state == ObjectLifecycleState.PLACED_PENDING
    assert track.occluded_since is None


def test_occlusion_timeout_signals_dismissal_candidate():
    track = make_track()
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.mark_occluded(timestamp=1.2)
    assert track.occlusion_timed_out(now=1.5, timeout_s=1.0) is False
    assert track.occlusion_timed_out(now=3.0, timeout_s=1.0) is True

    track.dismiss()
    assert track.state == ObjectLifecycleState.DISMISSED


def test_warning_threshold_false_without_enough_samples():
    track = make_track()
    bbox = BoundingBox(0, 0, 5, 5)
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.mark_reappeared_stationary(timestamp=1.5, bbox=bbox)
    track.record_distance_sample(timestamp=1.6, subject_bbox=BoundingBox(10, 0, 5, 5))
    assert track.crossed_warning_threshold(min_consecutive_samples=3) is False


def test_incident_threshold_false_before_persistence_window_elapses():
    track = make_track()
    bbox = BoundingBox(0, 0, 5, 5)
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=5.0)
    track.mark_reappeared_stationary(timestamp=5.1, bbox=bbox)
    for t, x in zip((5.2, 5.3, 5.4), (6, 10, 15)):
        track.record_distance_sample(timestamp=t, subject_bbox=BoundingBox(x, 0, 5, 5))
    # only 0.4s elapsed since release_at=5.0, persistence_s=1.0 not met
    assert track.crossed_incident_threshold(persistence_s=1.0, now=5.4) is False


def test_confirm_incident_requires_abandoned_candidate_state():
    """The state machine must never self-confirm -- only a state reached
    via promote_to_abandoned_candidate (i.e. after MiniCPM-o verification
    is requested) can be confirmed."""
    track = make_track()
    track.associate(timestamp=0.0)
    track.mark_separated(timestamp=1.0)
    track.confirm_incident(timestamp=2.0)
    assert track.state == ObjectLifecycleState.RELEASE_CANDIDATE  # unchanged, no-op
