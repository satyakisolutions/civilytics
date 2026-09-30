"""Tests for the violation-time face-crop buffer (pipeline/app/face_capture.py)."""
import threading
import time
from pathlib import Path

from app.face_capture import (
    FaceCropBuffer,
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
