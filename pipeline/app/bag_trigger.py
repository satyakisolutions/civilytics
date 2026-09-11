"""Per-camera coordinator wiring detector output into the object-release
lifecycle state machine.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
section 13. This module has no I/O side effects of its own -- warning and
verification actions are reported back as return values, not fired
directly, so:

  - shadow mode (Phase 3) can log them without touching real TTS/SIP/VLM
  - live mode (Phase 4/5) can wire them to the real adapters
  - unit tests can assert on them without mocking hardware

using the SAME coordinator code in every phase, per the spec's principle
that the detector/state-machine controls timing, not ad-hoc per-phase code.

Tracks are keyed by object identity, NOT by person identity -- a person
can carry more than one object at once (the reference clip this design
is built around is exactly this: a person carrying two bags releases
one and keeps the other).

Every detection matched to an existing track in a given frame is marked
"claimed" in that frame's shared claimed-index set, threaded through
retrieval, reappearance, and new-track-spawn checks in that order. This
matters: without a single shared claimed set, a detection that resolves
an existing track (e.g. a bag reappearing after occlusion) could also be
treated as "unclaimed" by a later step and spawn a spurious duplicate
track for the same physical object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.object_lifecycle import BoundingBox, ObjectLifecycleState, ObjectTrack, bbox_distance
from app.ring_buffer import CameraRingBuffer


@dataclass(frozen=True)
class Detection:
    label: str
    bbox: BoundingBox
    confidence: float


@dataclass(frozen=True)
class PersonTrack:
    track_id: str
    bbox: BoundingBox


class Detector(Protocol):
    def detect(self, jpeg_bytes: bytes, classes: list[str]) -> list[Detection]: ...


@dataclass
class TriggerConfig:
    """All thresholds configurable per camera/ROI, per spec section 6/7/9."""
    bag_classes: list[str] = field(default_factory=lambda: [
        "garbage bag", "plastic bag", "sack", "container", "cardboard box", "waste bundle",
    ])
    association_max_distance_px: float = 80.0
    # Deliberately tighter than association_max_distance_px: immediately
    # after separation the person is naturally still standing close to
    # whatever they just released. If retrieval used the same (larger)
    # association threshold, that normal moment would misread as
    # retrieval instead of release. This threshold answers "did the
    # object come back into the person's hands," which needs a much
    # closer match than "is there a candidate object anywhere near this
    # person." Still a proximity-only heuristic (spec section 11 defers
    # true hand/motion correlation), just a narrower one for this check.
    retrieval_max_distance_px: float = 20.0
    warning_min_consecutive_samples: int = 3
    incident_persistence_s: float = 1.5
    occlusion_timeout_s: float = 2.0
    max_ring_buffer_age_s: float = 12.0
    pre_roll_s: float = 5.0
    post_roll_s: float = 4.0


@dataclass(frozen=True)
class WarningAction:
    camera_id: str
    object_track_id: str
    person_track_id: str
    timestamp: float


@dataclass(frozen=True)
class VerificationRequest:
    camera_id: str
    object_track_id: str
    person_track_id: str
    release_timestamp: float
    pre_roll_frames: list[bytes]


_CARRIED_STATES = (ObjectLifecycleState.OBSERVED, ObjectLifecycleState.ASSOCIATED)
_AWAY_STATES = (
    ObjectLifecycleState.RELEASE_CANDIDATE,
    ObjectLifecycleState.OCCLUDED_PENDING,
    ObjectLifecycleState.PLACED_PENDING,
)


def _nearest_unclaimed(
    detections: list[Detection], ref_bbox: BoundingBox | None, claimed: set[int], max_distance_px: float,
) -> int | None:
    if ref_bbox is None:
        return None
    best_idx, best_distance = None, max_distance_px
    for idx, det in enumerate(detections):
        if idx in claimed:
            continue
        distance = bbox_distance(ref_bbox, det.bbox)
        if distance <= best_distance:
            best_distance, best_idx = distance, idx
    return best_idx


class CameraTriggerCoordinator:
    """Owns one camera's ring buffer and object lifecycle tracks.

    Multiple ObjectTrack instances may share the same person_track_id --
    that is the normal case for a person carrying more than one object.
    """

    def __init__(self, camera_id: str, roi_id: str, config: TriggerConfig | None = None) -> None:
        self.camera_id = camera_id
        self.roi_id = roi_id
        self.config = config or TriggerConfig()
        self.ring_buffer = CameraRingBuffer(max_age_s=self.config.max_ring_buffer_age_s)
        self.tracks: dict[str, ObjectTrack] = {}  # keyed by object_track_id
        self._next_object_id = 0

    def _new_object_id(self) -> str:
        self._next_object_id += 1
        return f"{self.camera_id}-obj-{self._next_object_id}"

    def on_frame(
        self,
        timestamp: float,
        jpeg_bytes: bytes,
        person_tracks: list[PersonTrack],
        detector: Detector,
    ) -> tuple[list[WarningAction], list[VerificationRequest]]:
        """Process one sampled frame. Returns any warning/verification
        actions the caller should carry out (or log, in shadow mode)."""
        self.ring_buffer.append(timestamp, jpeg_bytes)

        if not person_tracks:
            return [], []

        detections = detector.detect(jpeg_bytes, classes=self.config.bag_classes)
        person_by_id = {p.track_id: p for p in person_tracks}
        claimed: set[int] = set()
        max_dist = self.config.association_max_distance_px

        warnings: list[WarningAction] = []
        verifications: list[VerificationRequest] = []

        # Step 1: advance every existing track against this frame's
        # detections, claiming whichever detection index resolves it.
        for track in list(self.tracks.values()):
            person = person_by_id.get(track.person_track_id)

            if track.state in _CARRIED_STATES:
                # Two-part check, deliberately not collapsed into one:
                #
                # 1. Re-identify THIS object by matching to its own last
                #    known position, not the person's -- with more than
                #    one object carried by the same person, anchoring to
                #    the person would make every carried track compete
                #    for the same nearby detections with no way to tell
                #    which detection belongs to which object.
                # 2. Separately confirm the matched detection is still
                #    close to the person -- an object that keeps
                #    re-matching its own (unmoving) history while the
                #    person moves away has been released, not carried,
                #    even though it's still being detected in place.
                #
                # NOTE (matches spec section 11 / assumption A4): this is
                # still proximity-only frame-to-frame re-identification,
                # not a real tracker's persistent ID. It is only reliable
                # when per-tick displacement is small relative to
                # max_dist (i.e. sampled at the spec's 5-10fps, not at
                # large multi-second gaps) -- under large jumps, two
                # close-together carried objects can still swap identity.
                # Real per-object track IDs from an extended DeepStream
                # tracker would remove this limitation entirely.
                ref_bbox = track.last_known_bbox or (person.bbox if person else None)
                idx = _nearest_unclaimed(detections, ref_bbox, claimed, max_dist)
                still_associated = False
                if idx is not None:
                    det = detections[idx]
                    moving_with_person = person is not None and bbox_distance(person.bbox, det.bbox) <= max_dist
                    if moving_with_person:
                        claimed.add(idx)
                        track.observe(timestamp, det.bbox, det.confidence)
                        track.associate(timestamp)
                        still_associated = True
                if not still_associated and track.state == ObjectLifecycleState.ASSOCIATED:
                    track.mark_separated(timestamp)

            elif track.state in _AWAY_STATES:
                # Retrieval first: does the object now move with its
                # person again? Checked against the PERSON's position,
                # not the track's last known spot -- it may have been
                # picked up and carried somewhere new.
                retrieved_idx = _nearest_unclaimed(
                    detections, person.bbox if person else None, claimed, self.config.retrieval_max_distance_px,
                )
                if retrieved_idx is not None:
                    claimed.add(retrieved_idx)
                    track.mark_retrieved(timestamp)
                else:
                    reappeared_idx = _nearest_unclaimed(detections, track.last_known_bbox, claimed, max_dist)
                    if reappeared_idx is not None:
                        claimed.add(reappeared_idx)
                        track.mark_reappeared_stationary(timestamp, detections[reappeared_idx].bbox)
                    elif track.state == ObjectLifecycleState.RELEASE_CANDIDATE:
                        track.mark_occluded(timestamp)
                    elif track.state == ObjectLifecycleState.OCCLUDED_PENDING and track.occlusion_timed_out(
                        timestamp, self.config.occlusion_timeout_s
                    ):
                        track.dismiss()

                if person is not None and track.state in (
                    ObjectLifecycleState.RELEASE_CANDIDATE, ObjectLifecycleState.PLACED_PENDING,
                ):
                    track.record_distance_sample(timestamp, person.bbox)

            self._check_thresholds(track, timestamp, warnings, verifications)

            if track.state in (ObjectLifecycleState.RETRIEVED, ObjectLifecycleState.DISMISSED):
                del self.tracks[track.object_track_id]

        # Step 2: any detection near a person that no existing track
        # claimed this frame is a newly observed candidate object.
        for person in person_tracks:
            for idx, det in enumerate(detections):
                if idx in claimed:
                    continue
                if bbox_distance(person.bbox, det.bbox) > max_dist:
                    continue
                new_track = ObjectTrack(
                    camera_id=self.camera_id, roi_id=self.roi_id, person_track_id=person.track_id,
                    object_track_id=self._new_object_id(),
                )
                new_track.observe(timestamp, det.bbox, det.confidence)
                new_track.associate(timestamp)
                self.tracks[new_track.object_track_id] = new_track
                claimed.add(idx)

        return warnings, verifications

    def _check_thresholds(
        self,
        track: ObjectTrack,
        timestamp: float,
        warnings: list[WarningAction],
        verifications: list[VerificationRequest],
    ) -> None:
        if track.crossed_warning_threshold(self.config.warning_min_consecutive_samples) and track.warning_sent_at is None:
            track.warning_sent_at = timestamp
            warnings.append(WarningAction(
                camera_id=self.camera_id, object_track_id=track.object_track_id,
                person_track_id=track.person_track_id, timestamp=timestamp,
            ))

        if (
            track.crossed_incident_threshold(self.config.incident_persistence_s, timestamp)
            and track.verification_submitted_at is None
        ):
            track.promote_to_abandoned_candidate(timestamp)
            track.verification_submitted_at = timestamp
            pre_roll = self.ring_buffer.pre_roll(timestamp, self.config.pre_roll_s)
            verifications.append(VerificationRequest(
                camera_id=self.camera_id, object_track_id=track.object_track_id,
                person_track_id=track.person_track_id, release_timestamp=track.release_at or timestamp,
                pre_roll_frames=[f.jpeg_bytes for f in pre_roll],
            ))
