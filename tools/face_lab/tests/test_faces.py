import itertools

import numpy as np
import pytest

from facelab import DEFAULT_MODELS_DIR
from facelab.faces import gate_reason, nms
from app.subjects.config import SubjectSettings
from app.subjects.quality import passes_gate

S = SubjectSettings()


def test_nms_suppresses_overlaps_keeps_separate():
    boxes = np.array([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)
    assert nms(boxes, scores, 0.4) == [0, 2]
    assert nms(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), 0.4) == []


@pytest.mark.parametrize(
    "det,width,sharp,yaw",
    list(itertools.product([0.59, 0.6, 0.9], [39.0, 40.0, 90.0], [59.0, 60.0, 200.0], [0.0, 0.45, -0.46])),
)
def test_gate_reason_agrees_with_feature_gate(det, width, sharp, yaw):
    assert (gate_reason(det, width, sharp, yaw, S) is None) == passes_gate(det, width, sharp, yaw, S)


DET = DEFAULT_MODELS_DIR / "det_10g.onnx"
REC = DEFAULT_MODELS_DIR / "w600k_r50.onnx"
models_present = DET.exists() and REC.exists() and DET.stat().st_size > 1_000_000


@pytest.mark.skipif(not models_present, reason="face models not present (git lfs pull)")
def test_real_models_find_no_face_in_blank_frame():
    from facelab.faces import FaceAnalyzer, MultiFaceDetector
    from app.subjects.embedder import ArcFaceEmbedder

    analyzer = FaceAnalyzer(MultiFaceDetector(str(DET), input_size=(640, 640)), ArcFaceEmbedder(str(REC)), S)
    assert analyzer.analyze(np.full((720, 1280, 3), 127, np.uint8)) == []
