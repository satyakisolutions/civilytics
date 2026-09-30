"""Tests for the violation-time face-crop buffer (pipeline/app/face_capture.py)."""
import threading
import time
from pathlib import Path

from app.face_capture import (
    FaceCropBuffer,
    TrackIncidents,
    alert_incident_fields,
    ensure_faces_dirs,
    flush_due,
    head_crop_box,
    run_flusher,
)


def _jpeg(n: int) -> bytes:
    return f"jpeg-{n}".encode()


# ── head_crop_box ───────────────────────────────────────────────────

def test_head_crop_box_pads_sides_and_takes_top_half():
    assert head_crop_box(100, 50, 200, 400, 1920, 1080) == (80, 50, 320, 250)


def test_head_crop_box_clamps_to_frame():
    assert head_crop_box(0, 0, 200, 400, 150, 1080) == (0, 0, 150, 200)


def test_head_crop_box_rejects_too_small():
    assert head_crop_box(10, 10, 30, 60, 1920, 1080) is None


# ── FaceCropBuffer ─────────────────────────────────────────────────

def test_ring_keeps_only_last_six_crops():
    b = FaceCropBuffer()
    for i in range(10):
        b.add("cam-01", 7, float(i), _jpeg(i))
    b.begin_incident("inc-1", "cam-01", 7, now=10.0, detected_at=1000.0)
    [inc] = b.pop_due(12.0)
    assert inc.crops == [_jpeg(i) for i in range(4, 10)]


def test_incident_collects_postroll_crops_until_closed():
    b = FaceCropBuffer(postroll_s=2.0)
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=1.0, detected_at=1000.0)
    b.add("cam-01", 7, 1.5, _jpeg(1))
    assert b.pop_due(2.9) == []
    b.add("cam-01", 7, 2.5, _jpeg(2))
    [inc] = b.pop_due(3.0)
    assert inc.crops == [_jpeg(0), _jpeg(1), _jpeg(2)]
    assert inc.detected_at == 1000.0
    assert inc.camera_id == "cam-01" and inc.track_id == 7


def test_incident_crop_cap():
    b = FaceCropBuffer(max_incident_crops=12)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    for i in range(20):
        b.add("cam-01", 7, 0.05 * i, _jpeg(i))
    [inc] = b.pop_due(5.0)
    assert len(inc.crops) == 12


def test_incident_for_unseen_track_has_no_crops():
    b = FaceCropBuffer()
    b.begin_incident("inc-1", "cam-01", 99, now=0.0, detected_at=1.0)
    [inc] = b.pop_due(5.0)
    assert inc.crops == []


def test_second_incident_on_same_track_becomes_alias():
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    b.begin_incident("inc-2", "cam-01", 7, now=0.5, detected_at=1.5)
    [inc] = b.pop_due(5.0)
    assert inc.incident_id == "inc-1"
    assert inc.alias_ids == [("inc-2", 1.5)]


def test_tracks_are_isolated_by_camera():
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(1))
    b.add("cam-02", 7, 0.0, _jpeg(2))
    b.begin_incident("inc-1", "cam-02", 7, now=0.0, detected_at=1.0)
    [inc] = b.pop_due(5.0)
    assert inc.crops == [_jpeg(2)]


def test_evict_stale_drops_idle_tracks_but_keeps_open_incidents():
    b = FaceCropBuffer(stale_after_s=5.0, postroll_s=10.0)
    b.add("cam-01", 1, 0.0, _jpeg(1))
    b.add("cam-01", 2, 0.0, _jpeg(2))
    b.begin_incident("inc-1", "cam-01", 2, now=0.0, detected_at=1.0)
    b.evict_stale(6.0)
    assert b.tracked_count() == 1


# ── flushing ───────────────────────────────────────────────────────

def test_flush_due_writes_each_incident_to_its_own_folder(tmp_path: Path):
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=100.0)
    b.begin_incident("inc-2", "cam-01", 7, now=0.1, detected_at=100.1)
    sent: list[tuple[str, dict]] = []
    n = flush_due(b, 5.0, tmp_path, lambda key, payload: sent.append((key, payload)))
    assert n == 2
    assert [p["incident_id"] for _, p in sent] == ["inc-1", "inc-2"]
    assert [p["detected_at"] for _, p in sent] == [100.0, 100.1]
    for key, p in sent:
        assert key == "cam-01"
        assert p["camera_id"] == "cam-01" and p["person_track_id"] == 7
        assert len(p["crops"]) == 1
        crop = Path(p["crops"][0])
        assert crop.read_bytes() == _jpeg(0)
        assert crop.parent == tmp_path / "incoming" / p["incident_id"]


def test_flush_due_publishes_empty_crops_without_writing(tmp_path: Path):
    b = FaceCropBuffer()
    b.begin_incident("inc-1", "cam-01", 99, now=0.0, detected_at=1.0)
    sent: list[dict] = []
    flush_due(b, 5.0, tmp_path, lambda key, payload: sent.append(payload))
    assert sent[0]["crops"] == []
    assert not (tmp_path / "incoming" / "inc-1").exists()


def test_nothing_is_written_without_an_incident(tmp_path: Path):
    b = FaceCropBuffer()
    for i in range(20):
        b.add("cam-01", 1, float(i), _jpeg(i))
    assert flush_due(b, 100.0, tmp_path, lambda k, p: None) == 0
    assert not (tmp_path / "incoming").exists()


def test_ensure_faces_dirs_creates_incoming_and_subjects(tmp_path: Path):
    ensure_faces_dirs(tmp_path)
    assert (tmp_path / "incoming").is_dir()
    assert (tmp_path / "subjects").is_dir()


def test_ensure_faces_dirs_tolerates_chmod_permission_error(tmp_path: Path, monkeypatch):
    def deny(*a, **k):
        raise PermissionError("not owner")

    monkeypatch.setattr("app.face_capture.os.chmod", deny)
    ensure_faces_dirs(tmp_path)
    assert (tmp_path / "incoming").is_dir()
    assert (tmp_path / "subjects").is_dir()


def test_run_flusher_publishes_and_stops(tmp_path: Path):
    b = FaceCropBuffer(postroll_s=0.0)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    stop = threading.Event()
    sent: list[dict] = []
    t = threading.Thread(
        target=run_flusher,
        args=(b, tmp_path, lambda k, p: sent.append(p), stop),
        kwargs={"interval_s": 0.01, "clock": lambda: 10.0},
    )
    t.start()
    deadline = time.monotonic() + 2.0
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(2.0)
    assert sent and sent[0]["incident_id"] == "inc-1"
    assert not t.is_alive()


def test_run_flusher_survives_publish_errors(tmp_path: Path):
    b = FaceCropBuffer(postroll_s=0.0)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    stop = threading.Event()
    errors: list[int] = []

    def boom(key, payload):
        raise RuntimeError("kafka down")

    t = threading.Thread(
        target=run_flusher,
        args=(b, tmp_path, boom, stop),
        kwargs={"interval_s": 0.01, "clock": lambda: 10.0, "on_error": lambda: errors.append(1)},
    )
    t.start()
    deadline = time.monotonic() + 2.0
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(2.0)
    assert errors and not t.is_alive()


# ── isolation & error handling ─────────────────────────────────────

def test_flush_due_isolates_ids_on_publish_error(tmp_path: Path):
    """If publish raises for one id, others are still published (with on_error callback)."""
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=100.0)
    b.begin_incident("inc-2", "cam-01", 7, now=0.1, detected_at=100.1)
    sent: list[dict] = []
    errors: list[int] = []

    def publish_with_first_fail(key, payload):
        if payload["incident_id"] == "inc-1":
            raise RuntimeError("kafka down")
        sent.append(payload)

    n = flush_due(b, 5.0, tmp_path, publish_with_first_fail, on_error=lambda: errors.append(1))
    assert n == 1  # only inc-2 successfully published
    assert len(sent) == 1 and sent[0]["incident_id"] == "inc-2"
    assert len(errors) == 1  # on_error called once for inc-1


def test_alias_extends_postroll():
    """An alias alert extends the post-roll deadline by its own postroll duration."""
    b = FaceCropBuffer(postroll_s=2.0)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1000.0)
    b.begin_incident("inc-2", "cam-01", 7, now=1.5, detected_at=1000.5)
    # Without the fix, close_at would still be 0.0 + 2.0 = 2.0 for both
    # With the fix, inc-2 extends close_at to max(2.0, 1.5 + 2.0) = 3.5
    assert b.pop_due(2.5) == []  # Not due yet because of alias extension
    b.add("cam-01", 7, 3.0, _jpeg(1))
    [inc] = b.pop_due(3.5)
    assert _jpeg(1) in inc.crops  # Crop added after first close_at is captured
    assert inc.alias_ids == [("inc-2", 1000.5)]


# ── TrackIncidents (one incident per person-track episode) ──────────


def test_track_incidents_mints_then_reuses_within_ttl():
    reg = TrackIncidents(ttl_s=600.0)
    first, is_new = reg.get_or_create("cam1", 7, now=100.0)
    assert is_new is True
    assert isinstance(first, str) and len(first) == 36
    again, is_new2 = reg.get_or_create("cam1", 7, now=650.0)
    assert (again, is_new2) == (first, False)


def test_track_incidents_refreshes_last_used_on_every_call():
    reg = TrackIncidents(ttl_s=600.0)
    first, _ = reg.get_or_create("cam1", 7, now=0.0)
    reg.get_or_create("cam1", 7, now=500.0)
    # 1000 s after mint but only 500 s after last use -> still the same id.
    assert reg.get_or_create("cam1", 7, now=1000.0) == (first, False)
    assert reg.lookup("cam1", 7, now=1500.0) == first
    # lookup refreshed too
    assert reg.lookup("cam1", 7, now=2050.0) == first


def test_track_incidents_expires_after_ttl_and_mints_new():
    reg = TrackIncidents(ttl_s=600.0)
    first, _ = reg.get_or_create("cam1", 7, now=0.0)
    assert reg.lookup("cam1", 7, now=601.0) is None
    second, is_new = reg.get_or_create("cam1", 7, now=602.0)
    assert is_new is True and second != first


def test_track_incidents_keys_by_camera_and_track():
    reg = TrackIncidents()
    a, _ = reg.get_or_create("cam1", 7, now=0.0)
    b, new_b = reg.get_or_create("cam2", 7, now=0.0)
    c, new_c = reg.get_or_create("cam1", 8, now=0.0)
    assert new_b and new_c and len({a, b, c}) == 3


def test_track_incidents_lookup_never_creates():
    reg = TrackIncidents()
    assert reg.lookup("cam1", 7, now=0.0) is None
    assert len(reg) == 0


def test_track_incidents_evicts_expired_entries_lazily():
    reg = TrackIncidents(ttl_s=10.0)
    reg.get_or_create("cam1", 1, now=0.0)
    reg.get_or_create("cam1", 2, now=0.0)
    reg.get_or_create("cam1", 3, now=20.0)
    assert len(reg) == 1


def test_track_incidents_is_thread_safe():
    reg = TrackIncidents()
    results: list = []

    def worker() -> None:
        for _ in range(200):
            results.append(reg.get_or_create("cam1", 1, now=time.monotonic()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({incident_id for incident_id, _ in results}) == 1
    assert sum(1 for _, is_new in results if is_new) == 1


# ── alert_incident_fields (the _fire_waste_alert_async hook) ────────

def test_alert_fields_kill_switch_adds_nothing():
    reg = TrackIncidents()
    fields = alert_incident_fields(
        None, reg, "cam1", track_id=5, dumped_by_track_id=9,
        capture_face=True, now=0.0, detected_at=1.0,
    )
    assert fields == {}
    assert len(reg) == 0


def test_alert_fields_violation_begins_incident_for_dumper():
    buf = FaceCropBuffer(postroll_s=2.0)
    buf.add("cam1", 9, 0.0, _jpeg(1))
    reg = TrackIncidents()
    fields = alert_incident_fields(
        buf, reg, "cam1", track_id=5, dumped_by_track_id=9,
        capture_face=True, now=10.0, detected_at=1000.0,
    )
    assert fields["person_track_id"] == 9
    assert fields["incident_id"] == reg.lookup("cam1", 9, now=10.0)
    [incident] = buf.pop_due(12.0)
    assert incident.incident_id == fields["incident_id"]
    assert incident.track_id == 9
    assert incident.detected_at == 1000.0


def test_alert_fields_second_violation_same_track_reuses_incident_without_new_capture():
    buf = FaceCropBuffer(postroll_s=2.0)
    reg = TrackIncidents()
    first = alert_incident_fields(buf, reg, "cam1", 5, 9, capture_face=True, now=10.0, detected_at=1.0)
    assert len(buf.pop_due(12.0)) == 1
    second = alert_incident_fields(buf, reg, "cam1", 6, 9, capture_face=True, now=40.0, detected_at=2.0)
    assert second == first
    assert buf.pop_due(100.0) == []


def test_alert_fields_non_violation_without_incident_adds_nothing():
    buf = FaceCropBuffer()
    reg = TrackIncidents()
    fields = alert_incident_fields(buf, reg, "cam1", 5, None, capture_face=False, now=0.0, detected_at=1.0)
    assert fields == {}
    assert buf.pop_due(100.0) == []
    assert len(reg) == 0


def test_alert_fields_reminder_carries_existing_incident_without_capture():
    buf = FaceCropBuffer(postroll_s=2.0)
    reg = TrackIncidents()
    first = alert_incident_fields(buf, reg, "cam1", 5, 9, capture_face=True, now=10.0, detected_at=1.0)
    buf.pop_due(12.0)
    reminder = alert_incident_fields(buf, reg, "cam1", 5, 9, capture_face=False, now=47.0, detected_at=2.0)
    assert reminder == first
    assert buf.pop_due(100.0) == []


def test_alert_fields_person_track_falls_back_to_track_id():
    buf = FaceCropBuffer()
    reg = TrackIncidents()
    fields = alert_incident_fields(buf, reg, "cam1", 5, None, capture_face=True, now=0.0, detected_at=1.0)
    assert fields["person_track_id"] == 5
