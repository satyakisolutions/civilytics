import uuid

import numpy as np
import pytest
from qdrant_client import QdrantClient

from app.subjects.vector_store import FaceVectorStore


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def store():
    s = FaceVectorStore(QdrantClient(":memory:"), "test_faces")
    s.ensure_collection()
    return s


def test_ensure_collection_is_idempotent(store):
    store.ensure_collection()


def test_search_empty_collection(store):
    assert store.search(_unit(1)) == []


def test_upsert_and_search_returns_subject_hits(store):
    a, b = uuid.uuid4(), uuid.uuid4()
    fa = uuid.uuid4()
    store.upsert(fa, _unit(1), a, uuid.uuid4())
    store.upsert(uuid.uuid4(), _unit(2), b, uuid.uuid4())
    hits = store.search(_unit(1), limit=2)
    assert hits[0].subject_id == a and hits[0].face_id == fa
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)
    assert len(hits) == 2


def test_upsert_overwrites_subject(store):
    face, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    store.upsert(face, _unit(1), a, uuid.uuid4())
    store.upsert(face, _unit(1), b, uuid.uuid4())
    [hit] = store.search(_unit(1))
    assert hit.subject_id == b


def test_delete_removes_points(store):
    face = uuid.uuid4()
    store.upsert(face, _unit(1), uuid.uuid4(), uuid.uuid4())
    store.delete([face])
    store.delete([])
    assert store.search(_unit(1)) == []


def test_all_point_ids_pages_through_collection(store):
    faces = [uuid.uuid4() for _ in range(5)]
    for i, f in enumerate(faces):
        store.upsert(f, _unit(i), uuid.uuid4(), uuid.uuid4())
    assert sorted(store.all_point_ids(page_size=2)) == sorted(faces)


def test_all_point_ids_empty(store):
    assert store.all_point_ids() == []
