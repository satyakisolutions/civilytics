from pathlib import Path

import cv2
import numpy as np

from app.subjects.analyzer import FaceAnalyzer, face_thumbnail_jpeg
from app.subjects.config import SubjectSettings
from app.subjects.detector import FaceDetection
from app.subjects.embedder import ARCFACE_TEMPLATE

FRONTAL = ARCFACE_TEMPLATE * 2.0 + np.array([40.0, 40.0], dtype=np.float32)


def _write_noise(path: Path, seed: int) -> str:
    img = np.random.default_rng(seed).integers(0, 255, (320, 320, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _key(marker: int) -> int:
    # markers are 50 apart, so JPEG drift of a few levels still maps back
    return int(round(marker / 50.0))


class FakeDetector:
    """Identifies each crop by the flat 16x16 marker block written by _crop()."""

    def __init__(self, by_marker: dict[int, FaceDetection | None]):
        self.by_key = {_key(m): det for m, det in by_marker.items()}

    def detect_best(self, img):
        return self.by_key.get(_key(float(img[4, 4, 0])))


class FakeEmbedder:
    def embed(self, aligned):
        v = np.ones(512, dtype=np.float32)
        return v / np.linalg.norm(v)


def _crop(tmp_path: Path, marker: int) -> str:
    path = tmp_path / f"{marker:02d}.jpg"
    img = np.random.default_rng(marker).integers(0, 255, (320, 320, 3), dtype=np.uint8)
    img[:16, :16] = marker  # one full JPEG MCU, so it survives chroma subsampling
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 100])
    return str(path)


def _det(score: float, kps=FRONTAL) -> FaceDetection:
    return FaceDetection(score=score, bbox=np.array([40.0, 40.0, 260.0, 300.0]), kps=kps)


def test_keeps_top_three_by_quality(tmp_path: Path):
    markers = [10, 60, 110, 160, 210]
    scores = [0.70, 0.95, 0.80, 0.90, 0.75]
    det = FakeDetector({m: _det(s) for m, s in zip(markers, scores)})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    faces = analyzer([_crop(tmp_path, m) for m in markers])
    assert len(faces) == 3
    assert faces[0].quality >= faces[1].quality >= faces[2].quality
    assert faces[0].embedding.shape == (512,)
    assert faces[0].face_jpeg[:2] == b"\xff\xd8"


def test_skips_unreadable_and_faceless_crops(tmp_path: Path):
    det = FakeDetector({})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    assert analyzer([str(tmp_path / "missing.jpg"), _crop(tmp_path, 10)]) == []


def test_rejects_profile_faces(tmp_path: Path):
    profile = FRONTAL.copy()
    profile[2, 0] += 60.0  # nose far to one side
    det = FakeDetector({10: _det(0.95, profile)})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    assert analyzer([_crop(tmp_path, 10)]) == []


def test_face_thumbnail_is_decodable_and_clamped():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    data = face_thumbnail_jpeg(img, np.array([-10.0, -10.0, 50.0, 50.0]))
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and decoded.shape[0] <= 100 and decoded.shape[1] <= 100
