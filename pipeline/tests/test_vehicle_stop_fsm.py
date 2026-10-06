"""Vehicle stop+dwell escalation: a vehicle must actually come to rest to
escalate, a crawling one must not."""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.event_fsm import EventFSMManager, FSMState


def _drive(mgr, cam, tid, cls, speed, dwells):
    """Feed increasing dwell times at a fixed speed, return final state."""
    for d in dwells:
        mgr.update(cam, tid, cls, in_roi=True, dwell_time_s=d, speed_px_s=speed)
    return mgr.get_fsm_state(cam, tid)


def test_stopped_vehicle_escalates():
    mgr = EventFSMManager(vehicle_stop_threshold_s=20.0, vehicle_stop_speed_px_s=3.0)
    state = _drive(mgr, "cam-01", 1, "vehicle", 0.5, [0.5, 2.0, 5.0, 12.0, 21.0])
    assert state == FSMState.VIOLATION, state


def test_crawling_vehicle_does_not_escalate():
    # 6 px/s is below the generic loitering gate (7.5) but above the
    # vehicle stop gate -- this is the traffic case that must stay quiet.
    mgr = EventFSMManager(vehicle_stop_threshold_s=20.0, vehicle_stop_speed_px_s=3.0)
    state = _drive(mgr, "cam-01", 2, "vehicle", 6.0, [0.5, 2.0, 5.0, 12.0, 21.0, 40.0])
    assert state != FSMState.VIOLATION, state


def test_person_path_unchanged():
    mgr = EventFSMManager(person_violation_threshold_s=10.0)
    state = _drive(mgr, "cam-01", 3, "person", 20.0, [0.5, 2.0, 5.0, 11.0])
    assert state == FSMState.VIOLATION, state


if __name__ == "__main__":
    test_stopped_vehicle_escalates()
    test_crawling_vehicle_does_not_escalate()
    test_person_path_unchanged()
    print("ok")
