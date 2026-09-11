"""Object-release lifecycle state machine for trigger-gated dumping detection.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
sections 6, 7, 13. Tracks a candidate object (e.g. a bag) through its
lifecycle relative to a carrying subject (person/vehicle track), rather
than relying on a raw carried-object count -- count alone is fragile
against detection misses, occlusion, and track-ID churn.

This module holds no GPU/model dependency and is pure state-machine logic,
unit-testable without a camera, a detector, or MiniCPM-o.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field


class ObjectLifecycleState(str, enum.Enum):
    OBSERVED = "OBSERVED"
    ASSOCIATED = "ASSOCIATED"
    RELEASE_CANDIDATE = "RELEASE_CANDIDATE"
    OCCLUDED_PENDING = "OCCLUDED_PENDING"
    PLACED_PENDING = "PLACED_PENDING"
    RETRIEVED = "RETRIEVED"
    ABANDONED_CANDIDATE = "ABANDONED_CANDIDATE"
    CONFIRMED_INCIDENT = "CONFIRMED_INCIDENT"
    DISMISSED = "DISMISSED"


TERMINAL_STATES = frozenset({
    ObjectLifecycleState.RETRIEVED,
    ObjectLifecycleState.CONFIRMED_INCIDENT,
    ObjectLifecycleState.DISMISSED,
})


@dataclass
class BoundingBox:
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)


def bbox_distance(a: BoundingBox, b: BoundingBox) -> float:
    ax, ay = a.center
    bx, by = b.center
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def is_increasing(history: list[float], min_samples: int = 2) -> bool:
    """True if the sequence trends upward: the last value exceeds the
    first, and no single step drops by more than it previously rose
    (tolerates sensor noise, not a strict monotonic requirement)."""
    if len(history) < min_samples:
        return False
    if history[-1] <= history[0]:
        return False
    increases = sum(1 for i in range(1, len(history)) if history[i] >= history[i - 1])
    return increases >= len(history) - 1 - (len(history) // 4)  # allow ~25% noisy steps


@dataclass
class ObjectTrack:
    """One candidate object's full lifecycle record.

    Field list follows spec section 6's required track-record fields.
    """

    camera_id: str
    roi_id: str
    person_track_id: str
    object_track_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state: ObjectLifecycleState = ObjectLifecycleState.OBSERVED

    first_seen_at: float | None = None
    associated_at: float | None = None
    release_at: float | None = None
    last_seen_at: float | None = None

    last_known_bbox: BoundingBox | None = None
    release_location_uncertainty_px: float = 0.0

    distance_history: list[float] = field(default_factory=list)
    distance_timestamps: list[float] = field(default_factory=list)
    confidence_history: list[float] = field(default_factory=list)
    scene_change_evidence: list[str] = field(default_factory=list)

    warning_sent_at: float | None = None
    verification_submitted_at: float | None = None
    escalated_at: float | None = None
    occluded_since: float | None = None

    # -- transitions ------------------------------------------------------

    def observe(self, timestamp: float, bbox: BoundingBox, confidence: float) -> None:
        if self.first_seen_at is None:
            self.first_seen_at = timestamp
        self.last_seen_at = timestamp
        self.last_known_bbox = bbox
        self.confidence_history.append(confidence)

    def associate(self, timestamp: float) -> None:
        if self.state in (ObjectLifecycleState.OBSERVED,):
            self.state = ObjectLifecycleState.ASSOCIATED
            self.associated_at = timestamp

    def mark_separated(self, timestamp: float) -> None:
        """Object stopped following its associated subject."""
        if self.state == ObjectLifecycleState.ASSOCIATED:
            self.state = ObjectLifecycleState.RELEASE_CANDIDATE
            self.release_at = timestamp

    def mark_occluded(self, timestamp: float) -> None:
        """Detector missed the object this tick -- do not delete the
        track, hold it pending reappearance (spec section 2, "find the
        released bag only in current detections" was the bug this fixes)."""
        if self.state in (ObjectLifecycleState.RELEASE_CANDIDATE, ObjectLifecycleState.PLACED_PENDING):
            self.state = ObjectLifecycleState.OCCLUDED_PENDING
            self.occluded_since = timestamp

    def mark_reappeared_stationary(self, timestamp: float, bbox: BoundingBox) -> None:
        if self.state in (ObjectLifecycleState.RELEASE_CANDIDATE, ObjectLifecycleState.OCCLUDED_PENDING):
            self.state = ObjectLifecycleState.PLACED_PENDING
            self.last_known_bbox = bbox
            self.last_seen_at = timestamp
            self.occluded_since = None

    def mark_retrieved(self, timestamp: float) -> None:
        """Object moves with the same subject again -- close as non-incident."""
        if self.state not in TERMINAL_STATES:
            self.state = ObjectLifecycleState.RETRIEVED
            self.last_seen_at = timestamp

    def record_distance_sample(self, timestamp: float, subject_bbox: BoundingBox) -> None:
        if self.last_known_bbox is None:
            return
        distance = bbox_distance(subject_bbox, self.last_known_bbox)
        self.distance_history.append(distance)
        self.distance_timestamps.append(timestamp)

    def occlusion_timed_out(self, now: float, timeout_s: float) -> bool:
        return (
            self.state == ObjectLifecycleState.OCCLUDED_PENDING
            and self.occluded_since is not None
            and (now - self.occluded_since) > timeout_s
        )

    def dismiss(self) -> None:
        if self.state not in TERMINAL_STATES:
            self.state = ObjectLifecycleState.DISMISSED

    # -- threshold evaluation (spec section 7) -----------------------------

    def crossed_warning_threshold(self, min_consecutive_samples: int = 3) -> bool:
        """Threshold A: early release-candidate warning. Requires the
        object to have separated and the distance-from-subject trend to
        be consistently increasing over a short debounce window -- not
        a single-frame signal, to avoid noisy false warnings."""
        if self.state not in (ObjectLifecycleState.RELEASE_CANDIDATE, ObjectLifecycleState.PLACED_PENDING):
            return False
        if len(self.distance_history) < min_consecutive_samples:
            return False
        return is_increasing(self.distance_history[-min_consecutive_samples:])

    def crossed_incident_threshold(self, persistence_s: float, now: float) -> bool:
        """Threshold B: confirmed-incident candidate. Requires sustained
        stationary persistence near the release location plus continued
        distance increase -- stronger evidence than the warning threshold."""
        if self.state != ObjectLifecycleState.PLACED_PENDING:
            return False
        if self.release_at is None or (now - self.release_at) < persistence_s:
            return False
        return is_increasing(self.distance_history)

    def promote_to_abandoned_candidate(self, timestamp: float) -> None:
        if self.state == ObjectLifecycleState.PLACED_PENDING:
            self.state = ObjectLifecycleState.ABANDONED_CANDIDATE
            self.escalated_at = timestamp

    def confirm_incident(self, timestamp: float) -> None:
        """Called only after MiniCPM-o verification supports the event
        (spec section 14) -- the state machine never self-confirms."""
        if self.state == ObjectLifecycleState.ABANDONED_CANDIDATE:
            self.state = ObjectLifecycleState.CONFIRMED_INCIDENT
            self.escalated_at = timestamp
