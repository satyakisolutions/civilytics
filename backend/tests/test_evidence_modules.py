"""Tests for evidence: snapshot, annotator, recorder, dropbox_sync."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime


# ── snapshot.py ─────────────────────────────────────────────────────

from app.evidence.snapshot import compute_sharpness, score_frame, select_best_frame


def test_compute_sharpness_empty():
    assert compute_sharpness(b"") == 0.0

def test_compute_sharpness_uniform():
    """Uniform bytes have low variance → low sharpness."""
    data = bytes([128] * 1000)
    assert compute_sharpness(data) < 0.01

def test_compute_sharpness_varied():
    """Varied bytes have high variance → higher sharpness."""
    data = bytes(range(256)) * 40  # 10240 bytes
    s = compute_sharpness(data)
    assert 0.0 < s <= 1.0

def test_compute_sharpness_capped_at_1():
    """Extremely varied data should cap at 1.0."""
    data = bytes([0, 255] * 5000)
    assert compute_sharpness(data) <= 1.0

def test_score_frame_weights():
    s = score_frame(confidence=1.0, sharpness=1.0, framing_score=1.0)
    assert abs(s - 1.0) < 0.001

def test_score_frame_default_framing():
    s = score_frame(confidence=0.8, sharpness=0.6)
    expected = 0.8 * 0.4 + 0.6 * 0.3 + 0.5 * 0.3
    assert abs(s - expected) < 0.001

def test_select_best_frame_empty():
    assert select_best_frame([]) is None

def test_select_best_frame_single():
    frames = [{"data": b"x" * 100, "confidence": 0.9}]
    assert select_best_frame(frames) == frames[0]

def test_select_best_frame_picks_highest():
    f1 = {"data": bytes([128] * 100), "confidence": 0.5}
    f2 = {"data": bytes(range(256))[:100], "confidence": 0.95}
    best = select_best_frame([f1, f2])
    assert best == f2


# ── annotator.py ────────────────────────────────────────────────────

def test_annotate_frame_with_pillow(tmp_path):
    """Test annotator with real or mocked Pillow."""
    from app.evidence.annotator import annotate_frame
    img_path = tmp_path / "test.jpg"
    out_path = tmp_path / "out.jpg"

    try:
        from PIL import Image
        # Create a real test image
        img = Image.new("RGB", (100, 100), color="black")
        img.save(str(img_path))
        result = annotate_frame(
            str(img_path),
            [{"class_name": "person", "confidence": 0.9, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}, "track_id": 5, "dwell_time_s": 12.5}],
            str(out_path),
        )
        assert result == str(out_path)
        assert out_path.exists()
    except ImportError:
        # Pillow not available — test fallback path
        img_path.write_bytes(b"fake_image")
        result = annotate_frame(str(img_path), [], str(out_path))
        assert result == str(out_path)

def test_annotate_frame_empty_detections(tmp_path):
    from app.evidence.annotator import annotate_frame
    img_path = tmp_path / "test.jpg"
    out_path = tmp_path / "out.jpg"
    try:
        from PIL import Image
        img = Image.new("RGB", (100, 100), color="white")
        img.save(str(img_path))
        result = annotate_frame(str(img_path), [], str(out_path))
        assert result == str(out_path)
    except ImportError:
        img_path.write_bytes(b"fake")
        result = annotate_frame(str(img_path), [], str(out_path))
        assert result is not None


# ── recorder.py ─────────────────────────────────────────────────────

def test_evidence_recorder_save_snapshot(tmp_path):
    from app.evidence.recorder import EvidenceRecorder
    with patch("app.evidence.recorder.EVIDENCE_DIR", tmp_path):
        rec = EvidenceRecorder()
        ts = datetime(2026, 1, 15, 10, 30, 45)
        path = rec.save_snapshot("cam-01", b"jpeg_data", timestamp=ts)
    assert "cam-01" in path
    assert "2026-01-15" in path
    assert "10-30-45_snapshot.jpg" in path

def test_evidence_recorder_save_metadata(tmp_path):
    from app.evidence.recorder import EvidenceRecorder
    with patch("app.evidence.recorder.EVIDENCE_DIR", tmp_path):
        rec = EvidenceRecorder()
        ts = datetime(2026, 3, 1, 8, 0, 0)
        path = rec.save_metadata("cam-02", {"event": "violation"}, timestamp=ts)
    assert "metadata" in path
    assert "cam-02" in path
    assert path.endswith(".json")

def test_evidence_recorder_save_annotated(tmp_path):
    from app.evidence.recorder import EvidenceRecorder
    with patch("app.evidence.recorder.EVIDENCE_DIR", tmp_path):
        rec = EvidenceRecorder()
        path = rec.save_annotated_snapshot("cam-01", b"annotated_data")
    assert "_annotated.jpg" in path

def test_evidence_recorder_get_path(tmp_path):
    from app.evidence.recorder import EvidenceRecorder
    with patch("app.evidence.recorder.EVIDENCE_DIR", tmp_path):
        rec = EvidenceRecorder()
        path = rec.get_evidence_path("cam-01", "snapshots")
    assert "snapshots" in str(path)


# ── dropbox_sync.py ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dropbox_upload_no_token():
    from app.evidence.dropbox_sync import DropboxSync
    ds = DropboxSync(access_token="")
    result = await ds.upload_file("/tmp/test.jpg", "/WasteWatch/test.jpg")
    assert result["status"] == "skipped"
    await ds.close()

@pytest.mark.asyncio
async def test_dropbox_upload_success(tmp_path):
    from app.evidence.dropbox_sync import DropboxSync
    ds = DropboxSync(access_token="test_token")
    test_file = tmp_path / "test.jpg"
    test_file.write_bytes(b"data")

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"name": "test.jpg"}

    with patch.object(ds._client, "post", new_callable=AsyncMock, return_value=mock_resp):
        result = await ds.upload_file(str(test_file), "/WasteWatch/test.jpg")
    assert result["status"] == "synced"
    await ds.close()

@pytest.mark.asyncio
async def test_dropbox_upload_retry_exhausted(tmp_path):
    from app.evidence.dropbox_sync import DropboxSync
    ds = DropboxSync(access_token="test_token")
    test_file = tmp_path / "test.jpg"
    test_file.write_bytes(b"data")

    with patch.object(ds._client, "post", new_callable=AsyncMock, side_effect=Exception("network")), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        result = await ds.upload_file(str(test_file), "/path", max_retries=2)
    assert result["status"] == "failed"
    await ds.close()

def test_dropbox_build_path():
    from app.evidence.dropbox_sync import DropboxSync
    ds = DropboxSync()
    path = ds.build_dropbox_path("cam-01", "Zone A", "snap.jpg", "2026-01-15")
    assert path == "/WasteWatch/Zone-A/cam-01/2026-01-15/snap.jpg"
