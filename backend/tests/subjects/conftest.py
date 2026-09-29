"""Shared fixtures for subject-ID tests: SQLite (file) DB with the tables we touch."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Alert,
    Base,
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)

TABLES = [
    Alert.__table__,
    Subject.__table__,
    SubjectFace.__table__,
    SubjectIncident.__table__,
    FaceIncident.__table__,
    SubjectMatchReview.__table__,
    SubjectAccessLog.__table__,
]


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'subjects.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=TABLES))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


from qdrant_client import QdrantClient

from app.subjects.config import SubjectSettings
from app.subjects.service import SubjectService
from app.subjects.vector_store import FaceVectorStore


@pytest.fixture
def settings(tmp_path):
    return SubjectSettings(faces_dir=str(tmp_path / "faces"))


@pytest.fixture
def vector_store():
    store = FaceVectorStore(QdrantClient(":memory:"), "test_faces")
    store.ensure_collection()
    return store


@pytest.fixture
def service(session_factory, vector_store, settings):
    return SubjectService(session_factory, vector_store, settings)
