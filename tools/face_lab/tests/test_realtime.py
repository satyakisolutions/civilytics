"""Tests for the 30 fps pieces: device selection, on-demand embeddings, shared frame source."""
import threading

import numpy as np
import pytest

from facelab import faces as faces_mod
from facelab.faces import FaceObs, resolve_providers
from facelab.gallery import Gallery
from facelab.session import Session
from facelab.source import FrameSource


def test_cpu_device_uses_cpu_only():
    assert resolve_providers("cpu") == ["CPUExecutionProvider"]


def test_auto_prefers_gpu_and_keeps_cpu_fallback(monkeypatch):
    monkeypatch.setattr(faces_mod.ort, "get_available_providers",
                        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"])
    assert resolve_providers("auto") == ["DmlExecutionProvider", "CPUExecutionProvider"]


def test_auto_falls_back_to_cpu_and_gpu_errors_without_gpu(monkeypatch):
    monkeypatch.setattr(faces_mod.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    assert resolve_providers("auto") == ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError, match="onnxruntime-directml"):
        resolve_providers("gpu")


def _obs(x: float, quality: float = 0.8) -> FaceObs:
    return FaceObs(
        bbox=np.array([x, 100.0, x + 80.0, 180.0]), det_score=0.9, width=80.0, sharpness=150.0, yaw=0.0,
        reason=None, quality=quality, embedding=None, aligned=np.zeros((112, 112, 3), np.uint8),
    )


def test_embeddings_are_computed_only_while_a_visit_collects():
    calls = []
    vec = np.ones(512, np.float32) / np.sqrt(512)

    def embed(obs):
        calls.append(obs)
        obs.embedding = vec
        return vec

    s = Session(Gallery(), need_samples=3, embed=embed)
    for i in range(10):  # 10 frames of the same person; matched after 3 good faces
        s.step([_obs(100 + i)], i * 0.1)
    assert len(calls) == 3
    assert [v.status for v in s.active.values()] == ["new"]


def test_face_that_would_not_be_kept_is_not_embedded():
    calls = []

    def embed(obs):
        calls.append(obs)
        obs.embedding = np.ones(512, np.float32) / np.sqrt(512)
        return obs.embedding

    s = Session(Gallery(), need_samples=99, collect_s=99.0, embed=embed)
    for i in range(5):
        s.step([_obs(100, quality=0.9)], i * 0.1)   # fills the 5 kept samples
    s.step([_obs(100, quality=0.1)], 0.6)           # worse than all kept -> skipped
    assert len(calls) == 5


def test_two_consumers_both_get_the_newest_frame():
    src = FrameSource("rtsp://example.invalid/stream")   # not started: frames injected below
    got = {}

    def consumer(name):
        frame, _, seq = src.wait_new(-1, timeout=2.0)
        got[name] = (int(frame[0, 0, 0]), seq)

    threads = [threading.Thread(target=consumer, args=(n,)) for n in ("display", "processor")]
    for t in threads:
        t.start()
    with src._cond:
        src._frame, src._ts, src._seq = np.full((2, 2, 3), 7, np.uint8), 1.0, 1
        src._cond.notify_all()
    for t in threads:
        t.join(3)
    assert got == {"display": (7, 1), "processor": (7, 1)}
    frame, _, seq = src.wait_new(1, timeout=0.05)   # nothing newer than seq 1
    assert frame is None and seq == 1
