"""Engine init: present-but-corrupt must be rebuilt, not trusted.

`ensure_engines()` previously accepted any engine file with size > 0, so a
truncated engine (full disk, killed trtexec, interrupted container build)
passed the check and DeepStream died later at nvinfer load with no useful
message.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import engine_builder  # noqa: E402


def _fake_trt(deserialise_result):
    """Build a stand-in `tensorrt` module whose Runtime returns *result*."""
    class _Logger:
        ERROR = 0
        def __init__(self, _level): pass

    class _Runtime:
        def __init__(self, _logger): pass
        def deserialize_cuda_engine(self, _blob): return deserialise_result

    return type("trt", (), {"Logger": _Logger, "Runtime": _Runtime})


def test_accepts_engine_the_runtime_loads(tmp_path, monkeypatch):
    engine = tmp_path / "x.engine"
    engine.write_bytes(b"valid-blob")
    monkeypatch.setitem(sys.modules, "tensorrt", _fake_trt(object()))
    assert engine_builder._engine_is_loadable(engine) is True


def test_rejects_engine_the_runtime_returns_none_for(tmp_path, monkeypatch):
    """deserialize_cuda_engine() -> None is TensorRT's failure signal."""
    engine = tmp_path / "x.engine"
    engine.write_bytes(b"truncated")
    monkeypatch.setitem(sys.modules, "tensorrt", _fake_trt(None))
    assert engine_builder._engine_is_loadable(engine) is False


def test_rejects_engine_when_deserialise_raises(tmp_path, monkeypatch):
    engine = tmp_path / "x.engine"
    engine.write_bytes(b"garbage")

    class _Logger:
        ERROR = 0
        def __init__(self, _level): pass

    class _Runtime:
        def __init__(self, _logger): pass
        def deserialize_cuda_engine(self, _blob):
            raise RuntimeError("serialization version mismatch")

    monkeypatch.setitem(sys.modules, "tensorrt", type("trt", (), {
        "Logger": _Logger, "Runtime": _Runtime,
    }))
    assert engine_builder._engine_is_loadable(engine) is False


def test_passes_when_tensorrt_unavailable(tmp_path, monkeypatch):
    """Outside the container we cannot judge, so don't block startup.

    `None` in sys.modules makes `import tensorrt` raise ImportError, which
    is the same path as tensorrt genuinely not being installed.
    """
    engine = tmp_path / "x.engine"
    engine.write_bytes(b"cannot-inspect")
    monkeypatch.setitem(sys.modules, "tensorrt", None)
    assert engine_builder._engine_is_loadable(engine) is True
