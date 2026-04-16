import enum
import logging
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
    exit_timeout_s: float = 300.0
    # One-shot guards: each trigger fires exactly once per track lifecycle
    observe_triggered: bool = False
    violation_triggered: bool = False


class EventFSMManager:
    def __init__(self, enter_threshold_s: float = 3.0, observe_threshold_s: float = 10.0, violation_threshold_s: float = 15.0, exit_timeout_s: float = 300.0):
        self._fsms: dict[tuple[str, int], TrackFSM] = {}
        self._enter_threshold = enter_threshold_s
        self._observe_threshold = observe_threshold_s
        self._violation_threshold = violation_threshold_s
        self._exit_timeout = exit_timeout_s
        # Track IDs that reached VIOLATION — persists after FSM cleanup
        # so the OSD/frontend can highlight the suspect even outside ROI
        self._escalated_tracks: set[tuple[str, int]] = set()

    def is_escalated(self, camera_id: str, track_id: int) -> bool:
        """Check if a track was ever escalated to VIOLATION in this session."""
        return (camera_id, track_id) in self._escalated_tracks

    def update(self, camera_id: str, track_id: int, class_name: str, in_roi: bool, dwell_time_s: float, bbox: dict | None = None, has_carried_item: bool = False) -> FSMEvent | None:
        key = (camera_id, track_id)
        now = time.monotonic()

        if key not in self._fsms:
            self._fsms[key] = TrackFSM(
                camera_id=camera_id, track_id=track_id, class_name=class_name,
                enter_threshold_s=self._enter_threshold,
                observe_threshold_s=self._observe_threshold,
                violation_threshold_s=self._violation_threshold,
                exit_timeout_s=self._exit_timeout,
            )

        fsm = self._fsms[key]
        fsm.last_update = now
        fsm.last_bbox = bbox
        fsm.dwell_time_s = dwell_time_s
        prev_state = fsm.state

        if fsm.state == FSMState.IDLE:
            if in_roi:
                fsm.state = FSMState.ENTER
                fsm.entered_at = now
                # Fast-track: person carrying item in ROI → immediate OBSERVE
                # Catches walk-by dumping where person never dwells long enough
                if has_carried_item and class_name == "person":
                    fsm.state = FSMState.OBSERVE
                    logger.info("FSM %s track=%d: fast-track IDLE→OBSERVE (carried item in ROI)", camera_id, track_id)
        elif fsm.state == FSMState.ENTER:
            if not in_roi:
                fsm.state = FSMState.EXITED
                self._cleanup(key)
                return None  # Silent exit
            elif has_carried_item and class_name == "person":
                # Fast-track: person picked up/still carrying item → OBSERVE
                fsm.state = FSMState.OBSERVE
                logger.info("FSM %s track=%d: fast-track ENTER→OBSERVE (carried item)", camera_id, track_id)
            elif dwell_time_s >= self._enter_threshold:
                fsm.state = FSMState.OBSERVE
        elif fsm.state == FSMState.OBSERVE:
            if not in_roi:
                fsm.state = FSMState.EXITED
            elif dwell_time_s >= self._violation_threshold:
                fsm.state = FSMState.VIOLATION
                self._escalated_tracks.add(key)
        elif fsm.state == FSMState.VIOLATION:
            if not in_roi:
                fsm.state = FSMState.EXITED
            elif fsm.entered_at and (now - fsm.entered_at) > self._exit_timeout:
                fsm.state = FSMState.EXITED
        elif fsm.state in (FSMState.ALERTING, FSMState.ESCALATED):
            if not in_roi:
                fsm.state = FSMState.EXITED
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
        self._fsms.pop(key, None)

    def get_fsm_state(self, camera_id: str, track_id: int) -> str | None:
        """Get the current FSM state for a camera/track pair, or None if not tracked."""
        fsm = self._fsms.get((camera_id, track_id))
        return fsm.state.value if fsm else None

    def clear_escalated(self, camera_id: str, track_id: int) -> None:
        """Clear escalated flag when tracker fully loses the track (object left video)."""
        self._escalated_tracks.discard((camera_id, track_id))

    def get_active_fsms(self, camera_id: str | None = None) -> list[TrackFSM]:
        if camera_id:
            return [f for f in self._fsms.values() if f.camera_id == camera_id]
        return list(self._fsms.values())
