"""Tests for CameraRingBuffer."""
from app.ring_buffer import CameraRingBuffer


def test_append_and_length():
    buf = CameraRingBuffer(max_age_s=10.0)
    buf.append(timestamp=1.0, jpeg_bytes=b"a")
    buf.append(timestamp=2.0, jpeg_bytes=b"b")
    assert len(buf) == 2


def test_old_frames_are_evicted():
    buf = CameraRingBuffer(max_age_s=5.0)
    buf.append(timestamp=0.0, jpeg_bytes=b"old")
    buf.append(timestamp=3.0, jpeg_bytes=b"mid")
    buf.append(timestamp=6.0, jpeg_bytes=b"new")  # evicts anything older than 1.0
    assert len(buf) == 2
    assert buf.oldest_timestamp == 3.0
    assert buf.newest_timestamp == 6.0


def test_frames_in_window_is_inclusive_and_ordered():
    buf = CameraRingBuffer(max_age_s=100.0)
    for t in (1.0, 2.0, 3.0, 4.0, 5.0):
        buf.append(timestamp=t, jpeg_bytes=str(t).encode())
    window = buf.frames_in_window(2.0, 4.0)
    assert [f.timestamp for f in window] == [2.0, 3.0, 4.0]


def test_pre_roll_returns_frames_before_event():
    buf = CameraRingBuffer(max_age_s=100.0)
    for t in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0):
        buf.append(timestamp=t, jpeg_bytes=str(t).encode())
    pre_roll = buf.pre_roll(event_timestamp=5.0, pre_roll_s=3.0)
    assert [f.timestamp for f in pre_roll] == [2.0, 3.0, 4.0, 5.0]


def test_empty_buffer_has_no_bounds():
    buf = CameraRingBuffer()
    assert buf.oldest_timestamp is None
    assert buf.newest_timestamp is None
    assert len(buf) == 0
