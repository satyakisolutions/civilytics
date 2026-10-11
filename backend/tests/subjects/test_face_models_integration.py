"""Real SCRFD + ArcFace. Skipped unless backend/models/face/*.onnx exist
(run scripts/download_face_models.py). Identity fixtures are optional: set
WW_FACE_FIXTURES_DIR to a folder with identity_a/*.jpg and identity_b/*.jpg
(photos of consenting people, >= 2 each)."""
import os
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.subjects.analyzer import FaceAnalyzer
from app.subjects.config import SubjectSettings
from app.subjects.detector import ScrfdDetector
from app.subjects.embedder import ArcFaceEmbedder

MODELS = Path(__file__).resolve().parents[2] / "models" / "face"
DET, REC = MODELS / "det_10g.onnx", MODELS / "w600k_r50.onnx"

pytestmark = pytest.mark.skipif(
    not (DET.exists() and REC.exists() and DET.stat().st_size > 1_000_000),
    reason="face models not downloaded (scripts/download_face_models.py)",
)


@pytest.fixture(scope="module")
def analyzer():
    return FaceAnalyzer(ScrfdDetector(str(DET)), ArcFaceEmbedder(str(REC)), SubjectSettings())


def test_blank_image_has_no_face(analyzer, tmp_path):
    p = tmp_path / "blank.jpg"
    cv2.imwrite(str(p), np.full((240, 240, 3), 127, dtype=np.uint8))
    assert analyzer([str(p)]) == []


@pytest.mark.skipif(not os.environ.get("WW_FACE_FIXTURES_DIR"), reason="WW_FACE_FIXTURES_DIR not set")
def test_same_identity_scores_higher_than_different(analyzer):
    root = Path(os.environ["WW_FACE_FIXTURES_DIR"])
    emb = {
        ident: [f.embedding for p in sorted((root / ident).glob("*.jpg")) for f in analyzer([str(p)])[:1]]
        for ident in ("identity_a", "identity_b")
    }
    assert all(len(v) >= 2 for v in emb.values()), "need >= 2 detectable faces per identity"
    same = [float(np.dot(x, y)) for v in emb.values() for x, y in combinations(v, 2)]
    cross = [float(np.dot(x, y)) for x in emb["identity_a"] for y in emb["identity_b"]]
    assert np.mean(same) > np.mean(cross) + 0.2
