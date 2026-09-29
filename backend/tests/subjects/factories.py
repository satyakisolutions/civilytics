"""Deterministic embeddings, requests and fake stores for subject-service tests."""
import uuid
from datetime import datetime, timezone

import numpy as np

from app.subjects.types import AnalyzedFace, FaceRequest

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def with_similarity(base: np.ndarray, sim: float, seed: int = 999) -> np.ndarray:
    """A unit vector whose cosine similarity to `base` is exactly `sim`."""
    u = unit(seed)
    u = u - float(np.dot(u, base)) * base
    u = u / np.linalg.norm(u)
    v = sim * base + np.sqrt(1.0 - sim * sim) * u
    return (v / np.linalg.norm(v)).astype(np.float32)


def face(vec: np.ndarray, quality: float = 0.8) -> AnalyzedFace:
    return AnalyzedFace(quality=quality, embedding=vec, face_jpeg=JPEG)


def request(incident_id: uuid.UUID | None = None, camera_id: str = "cam-01", at: datetime = T0) -> FaceRequest:
    return FaceRequest(incident_id=incident_id or uuid.uuid4(), camera_id=camera_id, detected_at=at, crops=())


class FailingStore:
    """Qdrant is down: every call raises."""

    def ensure_collection(self):
        pass

    def search(self, vector, limit=20):
        raise ConnectionError("qdrant down")

    def upsert(self, face_id, vector, subject_id, incident_id):
        raise ConnectionError("qdrant down")

    def delete(self, face_ids):
        raise ConnectionError("qdrant down")


class FlakyStore:
    """Wraps a real store; the first `fail_upserts` upserts raise."""

    def __init__(self, inner, fail_upserts: int):
        self.inner = inner
        self.fail_upserts = fail_upserts

    def ensure_collection(self):
        self.inner.ensure_collection()

    def search(self, vector, limit=20):
        return self.inner.search(vector, limit)

    def upsert(self, face_id, vector, subject_id, incident_id):
        if self.fail_upserts > 0:
            self.fail_upserts -= 1
            raise ConnectionError("qdrant blip")
        self.inner.upsert(face_id, vector, subject_id, incident_id)

    def delete(self, face_ids):
        self.inner.delete(face_ids)
