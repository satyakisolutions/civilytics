"""Tests for EvidenceWorker._process_evidence and _process_event."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.fixture
def worker():
    from app.evidence.worker import EvidenceWorker
    with patch("app.evidence.worker.AIOKafkaConsumer"):
        w = EvidenceWorker.__new__(EvidenceWorker)
        w._recorder = MagicMock()
        w._stream_recorder = AsyncMock()
        w._dropbox = AsyncMock()
        w._dropbox.upload = AsyncMock()
    return w


@pytest.mark.asyncio
async def test_process_evidence_metadata(worker):
    worker._recorder.save_metadata.return_value = "/tmp/meta.json"
    await worker._process_evidence({"camera_id": "cam-01", "type": "metadata", "metadata": {"key": "val"}})
    worker._recorder.save_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_process_evidence_snapshot(worker):
    import base64
    data = base64.b64encode(b"fake_jpeg").decode()
    worker._recorder.save_snapshot.return_value = "/tmp/snap.jpg"
    await worker._process_evidence({"camera_id": "cam-01", "type": "snapshot", "frame_data": data})
    worker._recorder.save_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_process_event_violation(worker):
    worker._stream_recorder.start_recording.return_value = "/tmp/clip.mp4"
    await worker._process_event({"state": "violation", "camera_id": "cam-01", "track_id": 5})
    worker._stream_recorder.start_recording.assert_called_once_with("cam-01", 5)


@pytest.mark.asyncio
async def test_process_event_exited(worker):
    worker._stream_recorder.stop_recording.return_value = "/tmp/clip.mp4"
    await worker._process_event({"state": "exited", "camera_id": "cam-01", "track_id": 5})
    worker._stream_recorder.stop_recording.assert_called_once_with("cam-01", 5)
    worker._dropbox.upload.assert_called_once()


@pytest.mark.asyncio
async def test_process_event_ignored_state(worker):
    await worker._process_event({"state": "observe", "camera_id": "cam-01", "track_id": 5})
    worker._stream_recorder.start_recording.assert_not_called()
    worker._stream_recorder.stop_recording.assert_not_called()


@pytest.mark.asyncio
async def test_process_evidence_exception(worker):
    worker._recorder.save_metadata.side_effect = Exception("disk full")
    await worker._process_evidence({"camera_id": "cam-01", "type": "metadata", "metadata": {}})
    # Should not raise


@pytest.mark.asyncio
async def test_process_event_exception(worker):
    worker._stream_recorder.start_recording.side_effect = Exception("ffmpeg crash")
    await worker._process_event({"state": "violation", "camera_id": "cam-01", "track_id": 1})
    # Should not raise
