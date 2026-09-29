"""Crops -> the best few usable faces (spec §6.2 steps 2-5)."""
from __future__ import annotations

import cv2
import numpy as np

from app.subjects.config import SubjectSettings
from app.subjects.embedder import align_face
from app.subjects.quality import laplacian_sharpness, passes_gate, quality_score, yaw_proxy
from app.subjects.types import AnalyzedFace


def face_thumbnail_jpeg(img: np.ndarray, bbox: np.ndarray, margin: float = 0.3) -> bytes:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    xa, ya = max(0, int(x1 - mx)), max(0, int(y1 - my))
    xb, yb = min(w, int(x2 + mx)), min(h, int(y2 + my))
    ok, buf = cv2.imencode(".jpg", img[ya:yb, xa:xb], [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


class FaceAnalyzer:
    def __init__(self, detector, embedder, settings: SubjectSettings):
        self._detector = detector
        self._embedder = embedder
        self._settings = settings

    def __call__(self, crop_paths: list[str]) -> list[AnalyzedFace]:
        candidates = []
        for path in crop_paths:
            img = cv2.imread(path)
            if img is None:
                continue
            det = self._detector.detect_best(img)
            if det is None:
                continue
            width = float(det.bbox[2] - det.bbox[0])
            try:
                aligned = align_face(img, det.kps)
            except ValueError:
                continue
            sharpness = laplacian_sharpness(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY))
            yaw = yaw_proxy(det.kps)
            if not passes_gate(det.score, width, sharpness, yaw, self._settings):
                continue
            candidates.append((quality_score(det.score, width, sharpness, yaw), img, det, aligned))
        candidates.sort(key=lambda c: c[0], reverse=True)
        return [
            AnalyzedFace(
                quality=quality,
                embedding=self._embedder.embed(aligned),
                face_jpeg=face_thumbnail_jpeg(img, det.bbox),
            )
            for quality, img, det, aligned in candidates[: self._settings.keep_per_incident]
        ]
