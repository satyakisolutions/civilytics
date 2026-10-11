"""scripts/download_face_models.py must not mistake Git LFS pointer files for models."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "download_face_models.py"


@pytest.fixture
def dl(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("download_face_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DEST", tmp_path)
    return module


def _lfs_pointer(path: Path) -> None:
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 16923827\n")


def test_model_present_requires_real_size(dl, tmp_path):
    assert dl.model_present(tmp_path / "missing.onnx") is False
    _lfs_pointer(tmp_path / "pointer.onnx")
    assert dl.model_present(tmp_path / "pointer.onnx") is False
    (tmp_path / "real.onnx").write_bytes(b"\0" * 1_000_001)
    assert dl.model_present(tmp_path / "real.onnx") is True


def test_lfs_pointers_trigger_download(dl, tmp_path, monkeypatch):
    for name in dl.WANTED:
        _lfs_pointer(tmp_path / name)
    attempted = []

    def fake_urlopen(url, timeout=None):
        attempted.append(url)
        raise OSError("offline")

    monkeypatch.setattr(dl.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OSError):
        dl.main()
    assert attempted == [dl.URL]


def test_real_models_skip_download(dl, tmp_path, monkeypatch):
    for name in dl.WANTED:
        (tmp_path / name).write_bytes(b"\0" * 1_000_001)
    monkeypatch.setattr(dl.urllib.request, "urlopen", lambda *a, **k: pytest.fail("should not download"))
    assert dl.main() == 0
