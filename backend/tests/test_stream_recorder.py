"""Tests for the smart stream recorder."""
import asyncio
import pytest
from unittest.mock import patch, MagicMock

from app.evidence.stream_recorder import StreamRecorder, ActiveRecording, RECORDING_DIR


@pytest.fixture
def recorder():
    return StreamRecorder(
        mediamtx_rtsp_url="rtsp://localhost:8554",
        pre_buffer_s=5,
        post_buffer_s=5,
        max_concurrent=2,
    )


@pytest.mark.asyncio
async def test_start_recording_builds_correct_rtsp_url(recorder):
    """Recording should target the inference output stream (cam-XX-out)."""
    with patch("app.evidence.stream_recorder.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        path = await recorder.start_recording("cam-01", 42)

        assert path is not None
        # Verify ffmpeg was called with the inference output stream URL
        call_args = mock_popen.call_args[0][0]
        assert "rtsp://localhost:8554/cam-01-out" in call_args
        assert "-c:v" in call_args
        assert "copy" in call_args  # No re-encoding


@pytest.mark.asyncio
async def test_duplicate_recording_returns_existing(recorder):
    """Starting recording for same camera+track should return existing path."""
    with patch("app.evidence.stream_recorder.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        path1 = await recorder.start_recording("cam-01", 42)
        path2 = await recorder.start_recording("cam-01", 42)

        assert path1 == path2
        assert mock_popen.call_count == 1  # Only one ffmpeg process


@pytest.mark.asyncio
async def test_max_concurrent_limit(recorder):
    """Should refuse recording when max_concurrent is reached."""
    with patch("app.evidence.stream_recorder.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        await recorder.start_recording("cam-01", 1)
        await recorder.start_recording("cam-02", 2)
        path3 = await recorder.start_recording("cam-03", 3)

        assert path3 is None  # Rejected: max_concurrent=2


@pytest.mark.asyncio
async def test_active_count(recorder):
    with patch("app.evidence.stream_recorder.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        assert recorder.active_count == 0
        await recorder.start_recording("cam-01", 1)
        assert recorder.active_count == 1


@pytest.mark.asyncio
async def test_ffmpeg_not_found(recorder):
    """Should handle missing ffmpeg gracefully."""
    with patch("app.evidence.stream_recorder.subprocess.Popen", side_effect=FileNotFoundError):
        path = await recorder.start_recording("cam-01", 42)
        assert path is None
