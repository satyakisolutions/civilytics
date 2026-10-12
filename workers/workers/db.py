"""Minimal async DB access for workflow-workers tasks.

workers and backend are separate deployable packages with no shared
import path, so this does NOT duplicate backend/app/db/models.py's ORM
classes (duplicated ORM classes drift silently when a column changes on
one side only). Raw SQL instead — any task reading from a table backend
owns names its columns explicitly, so a schema change is a visible diff
here too.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.config import settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is None:
        if not settings.database_url:
            raise RuntimeError("WW_DATABASE_URL is not set — workers cannot reach the database")
        _engine = create_async_engine(settings.database_url, pool_size=5, max_overflow=5)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory
