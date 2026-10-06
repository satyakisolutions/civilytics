import enum
import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class FSMState(str, enum.Enum):
    IDLE = "idle"
    ENTER = "enter"
    OBSERVE = "observe"
    VIOLATION = "violation"
    ALERTING = "alerting"
    ESCALATED = "escalated"
    EXITED = "exited"


@dataclass
class FSMEvent:
    """Event produced by a state transition."""
    camera_id: str
    track_id: int
    state: FSMState
    prev_state: FSMState
    dwell_time_s: float
    class_name: str
    bbox: dict | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TrackFSM:
    camera_id: str
    track_id: int
    class_name: str
    state: FSMState = FSMState.IDLE
    entered_at: float | None = None
    dwell_time_s: float = 0.0
    last_bbox: dict | None = None
    last_update: float = field(default_factory=time.monotonic)
    enter_threshold_s: float = 3.0
    observe_threshold_s: float = 10.0
    violation_threshold_s: float = 15.0
    person_violation_threshold_s: float = 10.0
    vehicle_stop_threshold_s: float = 20.0
    exit_timeout_s: float = 300.0
    outside_roi_frames: int = 0
    # A paired waste attribution owns its lifecycle by person visibility,
    # not ROI membership. Track-loss cleanup clears the persistent marker.
    forced_by_waste: bool = False
    # One-shot guards: each trigger fires exactly once per track lifecycle
    observe_triggered: bool = False
    violation_triggered: bool = False
    vlm_fired: bool = False
    # Sticky: once a carried item has overlapped this person at all, a
    # momentary occlusion/detector miss on later frames shouldn't drop
    # the bag-violation trigger -- see _bag_violation in update().
    ever_carried_item: bool = False


class EventFSMManager:
    def __init__(self, enter_threshold_s: float = 3.0, observe_threshold_s: float = 10.0, violation_threshold_s: float = 15.0, person_violation_threshold_s: float = 10.0, vehicle_stop_threshold_s: float = 20.0, exit_timeout_s: float = 300.0, violation_exit_hysteresis_frames: int = 10, suspicious_speed_px_s: float = 7.5, vehicle_stop_speed_px_s: float = 3.0):
        self._fsms: dict[tuple[str, int], TrackFSM] = {}
        self._enter_threshold = enter_threshold_s
        self._observe_threshold = observe_threshold_s
        self._violation_threshold = violation_threshold_s
        self._person_violation_threshold = person_violation_threshold_s
        self._vehicle_stop_threshold = vehicle_stop_threshold_s
        # A vehicle only counts as "stopped" well below the generic
        # loitering speed -- a person shuffling at 7px/s is suspicious,
        # a vehicle creeping at that speed is just driving slowly.
        self._vehicle_stop_speed_px_s = vehicle_stop_speed_px_s
        self._exit_timeout = exit_timeout_s
        # "Suspicious" also covers barely-moving, not just fully stationary
        # (dwell_time_s only accrues while genuinely still) -- a person
        # loitering at a slow shuffle wouldn't otherwise reach ENTER's
        # dwell threshold. Centroid speed below this (px/s) counts as
        # loitering once the track has enough history to be meaningful.
        self._suspicious_speed_px_s = suspicious_speed_px_s
        self._violation_exit_hysteresis_frames = max(1, violation_exit_hysteresis_frames)
        # Track IDs that reached VIOLATION — persists after FSM cleanup
        # so the OSD/frontend can highlight the suspect even outside ROI
        self._escalated_tracks: set[tuple[str, int]] = set()
        # update() runs on the GStreamer streaming thread (pad-probe
        # callback); prune_stale()/clear_escalated()/get_active_fsms() run
        # on the GLib main-loop thread (health_check_cb timer). Without
        # this lock the two threads mutate/iterate _fsms/_escalated_tracks
        # concurrently — same failure class fixed in frame_batcher.py.
        self._lock = threading.Lock()

    def is_escalated(self, camera_id: str, track_id: int) -> bool:
        """Check if a track was ever escalated to VIOLATION in this session."""
        with self._lock:
            return (camera_id, track_id) in self._escalated_tracks

    def is_forced_by_waste(self, camera_id: str, track_id: int) -> bool:
        """Check if this track's VIOLATION came from actual evidence (bag
        proximity+dwell or waste-attribution force_violation), not generic
        dwell time alone. OSD rendering uses this to decide the red border,
        same evidence bar as waste_dumper_map, without depending on that
        dict being populated (bag fast-track never touches it)."""
        with self._lock:
            fsm = self._fsms.get((camera_id, track_id))
            return bool(fsm and fsm.forced_by_waste)

    def mark_escalated(self, camera_id: str, track_id: int) -> None:
        """Persist a violation/suspect marker independently of ROI state."""
        with self._lock:
            self._escalated_tracks.add((camera_id, track_id))

    def force_violation(
        self,
        camera_id: str,
        track_id: int,
        class_name: str,
        dwell_time_s: float = 0.0,
        bbox: dict | None = None,
    ) -> None:
        """Override a track into VIOLATION after paired waste confirmation.

        Unlike ``mark_escalated()``, this updates both the persistent OSD
        marker and the track's real FSM state. It is used when the spatial
        waste/person rule provides stronger evidence than the normal person
        dwell timer.
        """
        with self._lock:
            key = (camera_id, track_id)
            now = time.monotonic()
            fsm = self._fsms.get(key)
            if fsm is None:
                fsm = TrackFSM(
                    camera_id=camera_id,
                    track_id=track_id,
                    class_name=class_name,
                    enter_threshold_s=self._enter_threshold,
                    observe_threshold_s=self._observe_threshold,
                    violation_threshold_s=self._violation_threshold,
                    person_violation_threshold_s=self._person_violation_threshold,
                    vehicle_stop_threshold_s=self._vehicle_stop_threshold,
                    exit_timeout_s=self._exit_timeout,
                )
                self._fsms[key] = fsm
            previous = fsm.state
            fsm.state = FSMState.VIOLATION
            fsm.outside_roi_frames = 0
            fsm.forced_by_waste = True
            fsm.last_update = now
            fsm.dwell_time_s = dwell_time_s
            if bbox is not None:
                fsm.last_bbox = bbox
            self._escalated_tracks.add(key)
            if previous != FSMState.VIOLATION:
                logger.info(
                    "FSM %s track=%d: %s → violation (paired waste override)",
                    camera_id, track_id, previous.value,
                )

    def update(self, camera_id: str, track_id: int, class_name: str, in_roi: bool, dwell_time_s: float, bbox: dict | None = None, has_carried_item: bool = False, violation_threshold_s: float | None = None, enter_threshold_s: float | None = None, speed_px_s: float | None = None) -> FSMEvent | None:
        with self._lock:
            key = (camera_id, track_id)
            now = time.monotonic()

            if key not in self._fsms:
                self._fsms[key] = TrackFSM(
                    camera_id=camera_id, track_id=track_id, class_name=class_name,
                    enter_threshold_s=self._enter_threshold,
                    observe_threshold_s=self._observe_threshold,
                    violation_threshold_s=self._violation_threshold,
                    person_violation_threshold_s=self._person_violation_threshold,
                    vehicle_stop_threshold_s=self._vehicle_stop_threshold,
                    exit_timeout_s=self._exit_timeout,
                )

            fsm = self._fsms[key]
            fsm.last_update = now
            fsm.last_bbox = bbox
            fsm.dwell_time_s = dwell_time_s
            # Per-camera override (cameras.detection_config), re-applied
            # every call so a config change takes effect on the next frame
            # rather than only at track creation.
            if violation_threshold_s is not None:
                fsm.violation_threshold_s = violation_threshold_s
            if enter_threshold_s is not None:
                fsm.enter_threshold_s = enter_threshold_s
            prev_state = fsm.state

            # A carried bag/item on a person in the ROI, held for a real
            # dwell (not just a one-frame pass-through) -- skip OBSERVE
            # entirely and go straight to VIOLATION (same as a waste-
            # attribution force_violation).
            _bag_violation_now = has_carried_item and class_name == "person"
            if _bag_violation_now:
                fsm.ever_carried_item = True
            # Sticky: a bag seen with them earlier still counts even if
            # this exact frame missed the overlap (occlusion/detector
            # flicker) -- dwell requirement is the same >=4s either way.
            _bag_violation = (
                (_bag_violation_now or fsm.ever_carried_item)
                and class_name == "person"
                and dwell_time_s >= 4.0
            )
            _is_slow = (
                speed_px_s is not None
                and 0.0 < speed_px_s <= self._suspicious_speed_px_s
                and dwell_time_s >= 1.0  # ignore the first ~1s: too little
                                          # bbox history for speed_px_s to
                                          # mean anything, reads as 0/slow
                                          # regardless of real motion
            )

            if fsm.state == FSMState.IDLE:
                if in_roi:
                    fsm.state = FSMState.ENTER
                    fsm.entered_at = now
                    if _bag_violation:
                        fsm.state = FSMState.VIOLATION
                        fsm.forced_by_waste = True
                        self._escalated_tracks.add(key)
                        logger.info("FSM %s track=%d: fast-track IDLE→VIOLATION (carried item in ROI)", camera_id, track_id)
            elif fsm.state == FSMState.ENTER:
                if not in_roi:
                    fsm.state = FSMState.EXITED
                    self._cleanup(key)
                    return None  # Silent exit
                elif _bag_violation:
                    fsm.state = FSMState.VIOLATION
                    fsm.forced_by_waste = True
                    self._escalated_tracks.add(key)
                    logger.info("FSM %s track=%d: fast-track ENTER→VIOLATION (carried item)", camera_id, track_id)
                elif dwell_time_s >= fsm.enter_threshold_s or _is_slow:
                    fsm.state = FSMState.OBSERVE
            elif fsm.state == FSMState.OBSERVE:
                if not in_roi:
                    fsm.state = FSMState.EXITED
                elif _bag_violation:
                    fsm.state = FSMState.VIOLATION
                    fsm.forced_by_waste = True
                    self._escalated_tracks.add(key)
                    logger.info("FSM %s track=%d: fast-track OBSERVE→VIOLATION (carried item)", camera_id, track_id)
                elif class_name == "vehicle":
                    # Vehicle suspicious activity is "stopped AND lingering",
                    # not dwell alone: a vehicle queued or crawling through
                    # the ROI for 15s is traffic, not a dumping run. Require
                    # it to actually come to rest (speed below the vehicle
                    # stop gate) and hold that for the stop threshold.
                    _vehicle_stopped = (
                        speed_px_s is not None
                        and speed_px_s <= self._vehicle_stop_speed_px_s
                        and dwell_time_s >= 1.0  # same speed-history warmup as _is_slow
                    )
                    if _vehicle_stopped and dwell_time_s >= fsm.vehicle_stop_threshold_s:
                        fsm.state = FSMState.VIOLATION
                        self._escalated_tracks.add(key)
                        logger.info(
                            "FSM %s track=%d: vehicle stopped %.1fs (speed=%.1f px/s) → VIOLATION",
                            camera_id, track_id, dwell_time_s, speed_px_s or 0.0,
                        )
                elif (
                    dwell_time_s >= (
                        fsm.person_violation_threshold_s if class_name == "person"
                        else fsm.violation_threshold_s
                    )
                ):
                    # Person gets its own (longer) dwell-violation threshold
                    # -- the generic one is tuned for fast-moving classes
                    # (vehicles) that don't linger long, and applying it
                    # to person flashed yellow->red in ~2s. Bag proximity+
                    # dwell or waste proximity (force_violation) can still
                    # escalate a person faster than this threshold.
                    fsm.state = FSMState.VIOLATION
                    self._escalated_tracks.add(key)
            elif fsm.state == FSMState.VIOLATION:
                if not in_roi:
                    fsm.outside_roi_frames += 1
                    if fsm.outside_roi_frames >= self._violation_exit_hysteresis_frames:
                        fsm.state = FSMState.EXITED
                elif fsm.entered_at and (now - fsm.entered_at) > self._exit_timeout:
                    fsm.state = FSMState.EXITED
                else:
                    fsm.outside_roi_frames = 0
            elif fsm.state in (FSMState.ALERTING, FSMState.ESCALATED):
                if not in_roi:
                    fsm.outside_roi_frames += 1
                    if fsm.outside_roi_frames >= self._violation_exit_hysteresis_frames:
                        fsm.state = FSMState.EXITED
                else:
                    fsm.outside_roi_frames = 0
            elif fsm.state == FSMState.EXITED:
                self._cleanup(key)

            if fsm.state != prev_state:
                event = FSMEvent(
                    camera_id=camera_id, track_id=track_id, state=fsm.state,
                    prev_state=prev_state, dwell_time_s=dwell_time_s, class_name=class_name, bbox=bbox,
                )
                logger.info("FSM %s track=%d: %s → %s (dwell=%.1fs)", camera_id, track_id, prev_state.value, fsm.state.value, dwell_time_s)
                if fsm.state == FSMState.EXITED:
                    self._cleanup(key)
                return event
            return None

    def prune_stale(self) -> list[FSMEvent]:
        now = time.monotonic()
        events = []
        to_remove = []
        with self._lock:
            for key, fsm in self._fsms.items():
                if now - fsm.last_update > fsm.exit_timeout_s:
                    events.append(FSMEvent(
                        camera_id=fsm.camera_id, track_id=fsm.track_id, state=FSMState.EXITED,
                        prev_state=fsm.state, dwell_time_s=fsm.dwell_time_s, class_name=fsm.class_name, bbox=fsm.last_bbox,
                    ))
                    to_remove.append(key)
            for key in to_remove:
                del self._fsms[key]
        return events

    def _cleanup(self, key: tuple[str, int]):
        # Private — only ever called from within update()'s `with self._lock`
        # block, so it must NOT acquire the lock itself (non-reentrant).
        self._fsms.pop(key, None)

    def get_fsm_state(self, camera_id: str, track_id: int) -> str | None:
        """Get the current FSM state for a camera/track pair, or None if not tracked."""
        with self._lock:
            fsm = self._fsms.get((camera_id, track_id))
            return fsm.state.value if fsm else None

    def clear_escalated(self, camera_id: str, track_id: int) -> None:
        """Clear escalated flag when tracker fully loses the track (object left video)."""
        with self._lock:
            self._escalated_tracks.discard((camera_id, track_id))

    def reset_camera(self, camera_id: str) -> None:
        """Drop all FSM/escalation state for a camera -- used when a
        scene-cut (looping test video source) is detected, since track_ids
        (and therefore FSM/escalation entries keyed by them) persist
        unchanged across the cut and would otherwise carry a stale
        VIOLATION/escalated state into the new footage forever."""
        with self._lock:
            for key in [k for k in self._fsms if k[0] == camera_id]:
                del self._fsms[key]
            for key in [k for k in self._escalated_tracks if k[0] == camera_id]:
                self._escalated_tracks.discard(key)

    def get_active_fsms(self, camera_id: str | None = None) -> list[TrackFSM]:
        with self._lock:
            if camera_id:
                return [f for f in self._fsms.values() if f.camera_id == camera_id]
            return list(self._fsms.values())
