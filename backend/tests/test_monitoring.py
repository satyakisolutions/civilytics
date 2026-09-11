"""Tests for monitoring modules: degrade, gpu, containers, storage, model_health."""
import pytest
from unittest.mock import patch, MagicMock
from app.monitoring.degrade import DegradeManager
from app.monitoring.model_health import ModelHealthTracker


# ── DegradeManager ──────────────────────────────────────────────────

class TestDegradeManager:
    def test_initial_state(self):
        dm = DegradeManager()
        assert dm.level == 0
        assert dm.reason == "normal"

    def test_set_level_valid(self):
        dm = DegradeManager()
        dm.set_level(2, "gpu_overload")
        assert dm.level == 2
        assert dm.reason == "gpu_overload"

    def test_set_level_invalid_raises(self):
        dm = DegradeManager()
        with pytest.raises(ValueError):
            dm.set_level(-1)
        with pytest.raises(ValueError):
            dm.set_level(4)

    def test_feature_flags_level_0(self):
        dm = DegradeManager()
        assert dm.is_vlm_enabled()
        assert dm.is_lpr_enabled()
        assert dm.is_detection_enabled()

    def test_feature_flags_level_1(self):
        dm = DegradeManager()
        dm.set_level(1)
        assert not dm.is_vlm_enabled()
        assert dm.is_lpr_enabled()
        assert dm.is_detection_enabled()

    def test_feature_flags_level_2(self):
        dm = DegradeManager()
        dm.set_level(2)
        assert not dm.is_vlm_enabled()
        assert not dm.is_lpr_enabled()
        assert dm.is_detection_enabled()

    def test_feature_flags_level_3(self):
        dm = DegradeManager()
        dm.set_level(3)
        assert not dm.is_vlm_enabled()
        assert not dm.is_lpr_enabled()
        assert not dm.is_detection_enabled()

    def test_get_status(self):
        dm = DegradeManager()
        status = dm.get_status()
        assert status["level"] == 0
        assert status["name"] == "Full"
        assert status["vlm_enabled"] is True
        assert status["auto_degrade_enabled"] is True

    def test_get_status_all_levels(self):
        dm = DegradeManager()
        for level, name in [(0, "Full"), (1, "Reduced"), (2, "Minimal"), (3, "Passthrough")]:
            dm.set_level(level)
            assert dm.get_status()["name"] == name

    def test_auto_degrade_gpu_temp(self):
        dm = DegradeManager()
        dm.check_auto_degrade(gpu_temp=90)
        assert dm.level == 1
        assert "GPU temp" in dm.reason

    def test_auto_degrade_vlm_latency(self):
        dm = DegradeManager()
        dm.check_auto_degrade(vlm_latency_ms=15000)
        assert dm.level == 1
        assert "VLM latency" in dm.reason

    def test_auto_degrade_disabled(self):
        dm = DegradeManager()
        dm._auto_degrade_enabled = False
        dm.check_auto_degrade(gpu_temp=95)
        assert dm.level == 0

    def test_auto_degrade_no_downgrade(self):
        dm = DegradeManager()
        dm.set_level(2)
        dm.check_auto_degrade(gpu_temp=90)
        assert dm.level == 2  # Stays at 2, doesn't go back to 1

    def test_auto_degrade_none_values(self):
        dm = DegradeManager()
        dm.check_auto_degrade(gpu_temp=None, vlm_latency_ms=None)
        assert dm.level == 0

    def test_auto_degrade_below_threshold(self):
        dm = DegradeManager()
        dm.check_auto_degrade(gpu_temp=70, vlm_latency_ms=5000)
        assert dm.level == 0


# ── ModelHealthTracker ──────────────────────────────────────────────

class TestModelHealthTracker:
    def test_initial_models(self):
        mh = ModelHealthTracker()
        models = mh.get_all()
        assert "rfdetr" in models
        assert "qwen2.5-vl" in models
        assert models["rfdetr"]["status"] == "unknown"

    def test_record_latency(self):
        mh = ModelHealthTracker()
        mh.record_latency("rfdetr", 50.0)
        m = mh.get_all()["rfdetr"]
        assert m["last_latency_ms"] == 50.0
        assert m["call_count"] == 1
        assert m["avg_latency_ms"] == 50.0
        assert m["status"] == "healthy"

    def test_avg_latency_multiple(self):
        mh = ModelHealthTracker()
        mh.record_latency("rfdetr", 100.0)
        mh.record_latency("rfdetr", 200.0)
        m = mh.get_all()["rfdetr"]
        assert m["call_count"] == 2
        assert m["avg_latency_ms"] == 150.0

    def test_new_model_added_dynamically(self):
        mh = ModelHealthTracker()
        mh.record_latency("custom-model", 30.0)
        assert "custom-model" in mh.get_all()

    def test_set_status(self):
        mh = ModelHealthTracker()
        mh.set_status("rfdetr", "degraded")
        assert mh.get_all()["rfdetr"]["status"] == "degraded"

    def test_set_status_unknown_model(self):
        mh = ModelHealthTracker()
        mh.set_status("nonexistent", "error")  # Should not raise

    def test_get_all_returns_dict(self):
        mh = ModelHealthTracker()
        d = mh.get_all()
        assert isinstance(d, dict)
        assert "rfdetr" in d
        # Note: dict() creates a shallow copy; inner dicts are shared
        # This is the current implementation behavior


# ── GPU Stats ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gpu_stats_success():
    from app.monitoring.gpu import get_gpu_stats
    csv = "0, NVIDIA RTX 3090, 45, 32, 24576, 7800, 16776, 62, 250.0\n"
    mock_result = MagicMock(returncode=0, stdout=csv, stderr="")
    with patch("app.monitoring.gpu.subprocess.run", return_value=mock_result):
        gpus = await get_gpu_stats()
    assert len(gpus) == 1
    assert gpus[0]["name"] == "NVIDIA RTX 3090"
    assert gpus[0]["gpu_utilization_pct"] == 45.0
    assert gpus[0]["temperature_c"] == 62.0
    assert gpus[0]["power_draw_w"] == 250.0

@pytest.mark.asyncio
async def test_gpu_stats_power_na():
    from app.monitoring.gpu import get_gpu_stats
    csv = "0, GPU, 10, 10, 1000, 500, 500, 50, [N/A]\n"
    mock_result = MagicMock(returncode=0, stdout=csv, stderr="")
    with patch("app.monitoring.gpu.subprocess.run", return_value=mock_result):
        gpus = await get_gpu_stats()
    assert gpus[0]["power_draw_w"] is None

@pytest.mark.asyncio
async def test_gpu_stats_not_found():
    from app.monitoring.gpu import get_gpu_stats
    with patch("app.monitoring.gpu.subprocess.run", side_effect=FileNotFoundError):
        gpus = await get_gpu_stats()
    assert gpus[0]["error"] == "nvidia-smi not found"

@pytest.mark.asyncio
async def test_gpu_stats_failure():
    from app.monitoring.gpu import get_gpu_stats
    mock_result = MagicMock(returncode=1, stdout="", stderr="error")
    with patch("app.monitoring.gpu.subprocess.run", return_value=mock_result):
        gpus = await get_gpu_stats()
    assert "error" in gpus[0]


# ── Container Stats ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_container_stats_success():
    from app.monitoring.containers import get_container_stats
    output = '{"name":"api","cpu":"1.5%","mem":"256MiB","net":"1kB","status":"running"}\n{"name":"kafka","cpu":"2%","mem":"512MiB","net":"5kB","status":"running"}\n'
    mock_result = MagicMock(returncode=0, stdout=output, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        containers = await get_container_stats()
    assert len(containers) == 2
    assert containers[0]["name"] == "api"

@pytest.mark.asyncio
async def test_container_stats_not_found():
    from app.monitoring.containers import get_container_stats
    with patch("subprocess.run", side_effect=FileNotFoundError):
        containers = await get_container_stats()
    assert containers[0]["error"] == "docker not found"

@pytest.mark.asyncio
async def test_container_stats_invalid_json():
    from app.monitoring.containers import get_container_stats
    output = '{"name":"api"}\nnot json\n{"name":"redis"}\n'
    mock_result = MagicMock(returncode=0, stdout=output, stderr="")
    with patch("subprocess.run", return_value=mock_result):
        containers = await get_container_stats()
    assert len(containers) == 2  # Skips invalid line


# ── Storage Stats ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_storage_stats(tmp_path):
    from app.monitoring.storage import get_storage_stats
    mock_statvfs = MagicMock()
    mock_statvfs.f_blocks = 1000000
    mock_statvfs.f_frsize = 4096
    mock_statvfs.f_bfree = 500000
    with patch("app.monitoring.storage.os.statvfs", return_value=mock_statvfs), \
         patch("app.monitoring.storage.EVIDENCE_DIR", tmp_path):
        (tmp_path / "test.jpg").write_bytes(b"x" * 1024)
        stats = await get_storage_stats()
    assert "disk_total_gb" in stats
    assert "disk_usage_pct" in stats
    assert stats["evidence_files"] == 1

@pytest.mark.asyncio
async def test_storage_stats_no_evidence_dir(tmp_path):
    from app.monitoring.storage import get_storage_stats
    mock_statvfs = MagicMock()
    mock_statvfs.f_blocks = 1000000
    mock_statvfs.f_frsize = 4096
    mock_statvfs.f_bfree = 500000
    nonexistent = tmp_path / "nonexistent"
    with patch("app.monitoring.storage.os.statvfs", return_value=mock_statvfs), \
         patch("app.monitoring.storage.EVIDENCE_DIR", nonexistent):
        stats = await get_storage_stats()
    assert stats["evidence_files"] == 0
