import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    Subject,
    SubjectFace,
    subject_code,
)
from app.subjects.config import SubjectSettings

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)


def test_subject_code_pads_to_three_digits():
    assert subject_code(1) == "S-001"
    assert subject_code(42) == "S-042"
    assert subject_code(1234) == "S-1234"


async def test_subject_and_face_roundtrip(session_factory):
    async with session_factory() as s:
        subject = Subject(code_num=1, first_seen_at=NOW, last_seen_at=NOW)
        s.add(subject)
        await s.flush()
        s.add(SubjectFace(
            subject_id=subject.id, incident_id=uuid.uuid4(), crop_path="/x.jpg",
            quality=0.5, embedding=b"\x00" * 2048,
        ))
        await s.commit()
    async with session_factory() as s:
        loaded = (await s.execute(select(Subject))).scalar_one()
        assert loaded.code == "S-001"
        assert loaded.status == "active"
        assert loaded.incident_count == 0
        face = (await s.execute(select(SubjectFace))).scalar_one()
        assert face.qdrant_synced is False
        assert len(face.embedding) == 2048


async def test_alert_has_incident_id(session_factory):
    iid = uuid.uuid4()
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="t", status=AlertStatus.active, incident_id=iid))
        await s.commit()
    async with session_factory() as s:
        alert = (await s.execute(select(Alert))).scalar_one()
        assert alert.incident_id == iid


def test_settings_defaults_match_spec():
    s = SubjectSettings()
    assert (s.match_auto, s.match_review, s.max_faces, s.retention_days) == (0.55, 0.40, 10, 90)
    assert s.det_model == "/app/models/face/det_10g.onnx"
    assert s.collection == "offender_faces"


def test_settings_from_env_casts_types():
    s = SubjectSettings.from_env({
        "WW_FACE_MATCH_AUTO": "0.6",
        "WW_SUBJECT_MAX_FACES": "5",
        "WW_FACES_PRIVATE_DIR": "/tmp/f",
    })
    assert s.match_auto == 0.6
    assert s.max_faces == 5
    assert s.faces_dir == "/tmp/f"
    assert s.match_review == 0.40


def test_settings_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        SubjectSettings(match_auto=0.3, match_review=0.4)


def test_migration_008_follows_007():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "008_subjects.py"
    spec = importlib.util.spec_from_file_location("mig008", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "008"
    assert mod.down_revision == "007"
