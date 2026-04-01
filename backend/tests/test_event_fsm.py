import importlib.util
import os

# Import from pipeline/app/event_fsm.py directly to avoid conflict with backend's 'app' package
_fsm_path = os.path.join(os.path.dirname(__file__), "..", "..", "pipeline", "app", "event_fsm.py")
_spec = importlib.util.spec_from_file_location("pipeline_event_fsm", _fsm_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
EventFSMManager = _mod.EventFSMManager
FSMState = _mod.FSMState


def test_idle_to_enter_on_roi_entry():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    assert event is not None
    assert event.state == FSMState.ENTER
    assert event.prev_state == FSMState.IDLE


def test_enter_to_observe_after_threshold():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    assert event is not None
    assert event.state == FSMState.OBSERVE


def test_observe_to_violation_after_threshold():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=16.0)
    assert event is not None
    assert event.state == FSMState.VIOLATION


def test_enter_exits_silently_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=2.0)
    assert event is None  # Silent exit


def test_observe_exits_with_event_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=8.0)
    assert event is not None
    assert event.state == FSMState.EXITED
    assert event.prev_state == FSMState.OBSERVE


def test_no_transition_when_state_unchanged():
    mgr = EventFSMManager(enter_threshold_s=5.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=3.0)
    assert event is None


def test_multiple_tracks_independent():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 2, "car", in_roi=True, dwell_time_s=0.0)
    event1 = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event2 = mgr.update("cam-01", 2, "car", in_roi=True, dwell_time_s=3.0)
    assert event1 is not None and event1.state == FSMState.OBSERVE
    assert event2 is None


def test_violation_exits_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=16.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=20.0)
    assert event is not None
    assert event.state == FSMState.EXITED
    assert event.prev_state == FSMState.VIOLATION
