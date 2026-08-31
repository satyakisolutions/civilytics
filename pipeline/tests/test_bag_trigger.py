"""Tests for CameraTriggerCoordinator -- a fake detector stands in for
YOLO-World so this reproduces the reference clip's event sequence
(spec section 6 example: person carries 2 bags, releases the white one
near the tree, walks away with the dark one) without any GPU/model
dependency. This is the offline-replay-style test the spec's Phase 2
exit criterion calls for.
"""
from app.bag_trigger import CameraTriggerCoordinator, Detection, PersonTrack, TriggerConfig
from app.object_lifecycle import BoundingBox, ObjectLifecycleState


class ScriptedDetector:
    """Returns a pre-scripted list of detections per call, in order."""

    def __init__(self, script: list[list[Detection]]) -> None:
        self._script = script
        self._call_idx = 0

    def detect(self, jpeg_bytes: bytes, classes: list[str]) -> list[Detection]:
        result = self._script[min(self._call_idx, len(self._script) - 1)]
        self._call_idx += 1
        return result


def bag(x: float, label: str = "bag") -> Detection:
    return Detection(label=label, bbox=BoundingBox(x, 0, 10, 10), confidence=0.8)


def person_at(x: float) -> PersonTrack:
    return PersonTrack(track_id="person-104", bbox=BoundingBox(x, 0, 10, 10))


def test_reference_clip_reproduced_end_to_end():
    config = TriggerConfig(
        association_max_distance_px=15.0,
        retrieval_max_distance_px=5.0,  # tighter than the 8px gap right after separation
        warning_min_consecutive_samples=3,
        incident_persistence_s=1.0,
        occlusion_timeout_s=1.0,
    )
    coordinator = CameraTriggerCoordinator(camera_id="cam-01", roi_id="tree-side", config=config)

    # t=0: person carries a bag right beside them (bag stays associated)
    detector = ScriptedDetector([
        [bag(2)],    # t=0.0 still with person
        [bag(2)],    # t=0.5 still with person
        [],          # t=5.0 bag separates -- person moves on, no detection near old person pos
        [bag(2)],    # t=5.5 bag reappears stationary at its last known spot
        [bag(2)],    # t=5.6
        [bag(2)],    # t=5.8
        [bag(2)],    # t=6.0
        [bag(2)],    # t=6.5 -- past persistence window, distance kept increasing
    ])
    all_warnings = []
    all_verifications = []

    frames = [
        (0.0, person_at(2)),
        (0.5, person_at(2)),
        (5.0, person_at(6)),   # person has moved on, bag left behind at x=2
        (5.5, person_at(10)),
        (5.6, person_at(15)),
        (5.8, person_at(20)),
        (6.0, person_at(25)),
        (6.5, person_at(30)),
    ]
    for timestamp, person in frames:
        warnings, verifications = coordinator.on_frame(
            timestamp=timestamp, jpeg_bytes=b"frame", person_tracks=[person], detector=detector,
        )
        all_warnings.extend(warnings)
        all_verifications.extend(verifications)

    assert len(all_warnings) == 1, "exactly one warning should fire, not repeatedly"
    assert len(all_verifications) == 1, "exactly one verification request should be submitted"
    assert all_verifications[0].camera_id == "cam-01"
    assert all_verifications[0].person_track_id == "person-104"


def test_retrieval_before_incident_threshold_produces_no_verification():
    """Person sets bag down then picks it back up -- must NOT escalate."""
    config = TriggerConfig(association_max_distance_px=15.0, warning_min_consecutive_samples=3)
    coordinator = CameraTriggerCoordinator(camera_id="cam-01", roi_id="tree-side", config=config)

    detector = ScriptedDetector([
        [bag(2)],  # carried
        [],        # briefly set down / occluded
        [bag(2)],  # still there
        [bag(2)],  # picked back up -- association restored (person right next to it)
    ])
    frames = [
        (0.0, person_at(2)),
        (0.3, person_at(3)),
        (0.6, person_at(4)),
        (0.9, person_at(2)),  # returns and retrieves
    ]
    all_verifications = []
    for timestamp, person in frames:
        _, verifications = coordinator.on_frame(
            timestamp=timestamp, jpeg_bytes=b"frame", person_tracks=[person], detector=detector,
        )
        all_verifications.extend(verifications)

    assert all_verifications == []


def test_retrieval_detected_even_when_bag_moves_with_person_to_new_location():
    """Regression test: retrieval must be detected even when the
    reappeared-bag detection is nowhere near the object's last known
    position (because the person picked it up and walked off with it),
    not only when it re-detects stationary in the original spot."""
    config = TriggerConfig(association_max_distance_px=15.0)
    coordinator = CameraTriggerCoordinator(camera_id="cam-01", roi_id="tree-side", config=config)

    detector = ScriptedDetector([
        [bag(2)],   # carried, associated
        [],         # separates -- person walks off without it
        [bag(50)],  # bag reappears far from its last known spot (x=2),
                    # right next to the person again -- picked back up
    ])
    frames = [
        (0.0, person_at(2)),
        (0.5, person_at(30)),  # far from bag's last position -- not a re-detection nearby
        (1.0, person_at(50)),  # person now co-located with the bag's new detection
    ]
    all_verifications = []
    for timestamp, person in frames:
        _, verifications = coordinator.on_frame(
            timestamp=timestamp, jpeg_bytes=b"frame", person_tracks=[person], detector=detector,
        )
        all_verifications.extend(verifications)

    assert all_verifications == []
    # track was retrieved and removed, not stuck cycling through
    # OCCLUDED_PENDING toward a timeout dismissal
    remaining_for_person = [t for t in coordinator.tracks.values() if t.person_track_id == "person-104"]
    assert remaining_for_person == []


def test_two_bags_carried_release_one_keep_other():
    """The actual reference scenario this whole redesign exists for:
    a person carries two bags simultaneously, releases one, and keeps
    carrying the other -- both detected every frame (no missing-detection
    tricks), with white and dark kept clearly separated in position so
    nearest-unclaimed matching can't confuse one object's detection for
    the other's (see bag_trigger.py's CARRIED_STATES comment on why that
    matters: this is proximity-only re-identification, not a real
    tracker's persistent ID, and needs enough separation between
    simultaneously-carried objects to stay unambiguous).
    """
    config = TriggerConfig(
        association_max_distance_px=5.0,
        retrieval_max_distance_px=2.0,
        warning_min_consecutive_samples=2,
        incident_persistence_s=0.6,
        occlusion_timeout_s=1.0,
    )
    coordinator = CameraTriggerCoordinator(camera_id="cam-01", roi_id="tree-side", config=config)

    # White stays fixed at x=0 the whole time (it gets released early and
    # never picked back up). Dark stays glued 3 units ahead of the person
    # throughout (always genuinely carried). Person walks from x=0 to x=8.
    # White separates once the person walks far enough away from x=0 that
    # it falls outside association_max_distance_px (at person_x=6).
    all_warnings, all_verifications = [], []
    for person_x in range(9):
        detector_frame = [bag(0, "white"), bag(person_x + 3, "dark")]
        detector = ScriptedDetector([detector_frame])
        warnings, verifications = coordinator.on_frame(
            timestamp=float(person_x), jpeg_bytes=b"frame",
            person_tracks=[person_at(person_x)], detector=detector,
        )
        all_warnings.extend(warnings)
        all_verifications.extend(verifications)

    assert len(all_verifications) == 1, "exactly one bag (white) should trigger verification"
    assert len(all_warnings) == 1, "the early warning threshold should also fire for the released bag"

    remaining = list(coordinator.tracks.values())
    states = {t.state for t in remaining}
    assert ObjectLifecycleState.ABANDONED_CANDIDATE in states, "white bag reached verification, stays tracked"
    assert ObjectLifecycleState.ASSOCIATED in states, "dark bag must still be tracked as carried, not lost"
    assert len(remaining) == 2, "both objects remain tracked -- dark was never mistaken for released"


def test_no_person_tracks_is_a_no_op():
    coordinator = CameraTriggerCoordinator(camera_id="cam-01", roi_id="tree-side")
    detector = ScriptedDetector([[bag(2)]])
    warnings, verifications = coordinator.on_frame(
        timestamp=0.0, jpeg_bytes=b"frame", person_tracks=[], detector=detector,
    )
    assert warnings == []
    assert verifications == []
