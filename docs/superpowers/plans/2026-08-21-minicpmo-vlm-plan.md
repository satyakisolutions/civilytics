# MiniCPM-o 4.5 VLM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Swap the trigger-based VLM backend from Qwen2.5-VL to MiniCPM-o 4.5, and add an always-on narration path that flags violation types the FSM/RFDETR detector misses, landing flags in a human-review queue.

**Architecture:** Phase 1 changes config only (`VLMClient` already speaks OpenAI-compatible chat completions — model/endpoint swap, plus `enable_thinking=false` to avoid CoT latency). Phase 2 adds a GLib-timer-driven narration sampler reading the existing `frame_jpeg_cache` (already populated per-buffer by `capture_frame_probe`) at a configurable fps, a duplex-session-shaped `NarrationClient` (stubbed/simulated today — real `vllm-omni` streaming call deferred), a new `narration_flags` DB table + Kafka topic + consumer + review API. This spec deliberately substitutes a new GStreamer tee/queue/relink (see design doc's original "insert tee before nvstreamdemux") with a timer reading the existing capture cache — same effect (narration reads inference-derived frames in parallel to the FSM path, without re-running detection or touching the tee/branch structure that already has `sys.exit(1)` failure paths on relink), far lower risk to touch today.

**Tech Stack:** Python, FastAPI, SQLAlchemy/Alembic, aiokafka, confluent-kafka, GStreamer/pyds, pytest.

**Validation scope today:** everything runs against a mock `VLMClient` HTTP layer and a stubbed `NarrationClient` (no real GPU/vllm-omni deploy, no live camera) — full pipeline wiring, FSM/narration flow, DB, and API are validated end-to-end via tests and a local run against the simulator source already used by pipeline tests. Real `vllm-omni` endpoint swap is a follow-up once GPU capacity is confirmed.

---

## Phase 1 — Trigger model swap

### Task 1: Add MiniCPM-o config settings

**Files:**
- Modify: `backend/app/config/settings.py:14-18`
- Test: `backend/tests/test_settings_vlm.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_vlm.py
"""Tests for VLM-related settings defaults."""
import importlib


def test_vlm_settings_default_to_minicpmo(monkeypatch):
    monkeypatch.delenv("WW_VLM_MODEL", raising=False)
    monkeypatch.delenv("WW_VLM_ENABLE_THINKING", raising=False)
    import app.config.settings as settings_module
    importlib.reload(settings_module)

    assert settings_module.settings.vlm_model == "openbmb/MiniCPM-o-4_5"
    assert settings_module.settings.vlm_enable_thinking is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_settings_vlm.py -v`
Expected: FAIL — `AssertionError` (vlm_model still `"Qwen/Qwen2.5-VL-7B-Instruct-AWQ"`, `vlm_enable_thinking` attribute doesn't exist)

- [ ] **Step 3: Update settings**

```python
# backend/app/config/settings.py — replace lines 14-18
    vlm_base_url: str = "http://localhost:8000/v1"
    vlm_model: str = "openbmb/MiniCPM-o-4_5"
    vlm_max_tokens: int = 512
    vlm_temperature: float = 0.1
    vlm_timeout_s: int = 30
    vlm_enable_thinking: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_settings_vlm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config/settings.py backend/tests/test_settings_vlm.py
git commit -m "feat: default VLM settings to MiniCPM-o 4.5"
```

### Task 2: Pass enable_thinking through VLMClient request payload

**Files:**
- Modify: `backend/app/vlm/client.py:117-125`
- Test: `backend/tests/test_vlm_client.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to backend/tests/test_vlm_client.py
@pytest.mark.asyncio
async def test_analyze_sends_chat_template_kwargs(vlm_request):
    from app.vlm.client import VLMClient
    client = VLMClient()
    response_json = '{"person_description":"","action":"none","is_dumping":false,"waste_type":"none","severity":"info","confidence":0.1,"reasoning":"ok","command":"monitor"}'
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": response_json}}]}

    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        await client.analyze(vlm_request)

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["chat_template_kwargs"] == {"enable_thinking": False}
    await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_vlm_client.py::test_analyze_sends_chat_template_kwargs -v`
Expected: FAIL — `KeyError: 'chat_template_kwargs'`

- [ ] **Step 3: Add the field to the payload**

```python
# backend/app/vlm/client.py — in _call_vlm, replace the payload dict (lines 117-125)
        payload = {
            "model": settings.vlm_model,
            "max_tokens": settings.vlm_max_tokens,
            "temperature": settings.vlm_temperature,
            "chat_template_kwargs": {"enable_thinking": settings.vlm_enable_thinking},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_vlm_client.py -v`
Expected: PASS (all tests in file, including pre-existing ones — `mock_post.call_args.kwargs["json"]` requires the other tests still call `post(..., json=...)`, confirm no signature break)

- [ ] **Step 5: Commit**

```bash
git add backend/app/vlm/client.py backend/tests/test_vlm_client.py
git commit -m "feat: send enable_thinking flag to MiniCPM-o chat completions"
```

### Task 3: Update gRPC server default model_version fallback

**Files:**
- Modify: `backend/app/vlm/grpc_server.py:150`

- [ ] **Step 1: Update the hardcoded fallback string**

```python
# backend/app/vlm/grpc_server.py line 150 — replace
                    model_version=settings.vlm_model or "minicpmo-4.5",
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/vlm/grpc_server.py
git commit -m "fix: update gRPC servicer fallback model version string"
```

Phase 1 is now complete: `Analyze` RPC, proto, Kafka `wastewatch.vlm.results` — all untouched. Model swap is config-only, backed by tests confirming both the settings default and the request payload shape.

---

## Phase 2 — Always-on narration + review queue

### Task 4: Add narration Kafka topic + producer method

**Files:**
- Modify: `backend/app/config/kafka.py:18` (add topic + group constants)
- Modify: `pipeline/app/kafka_producer.py`
- Test: `pipeline/tests/test_kafka_producer_narration.py`

- [ ] **Step 1: Write the failing test**

```python
# pipeline/tests/test_kafka_producer_narration.py
"""Tests for narration flag publishing."""
from unittest.mock import MagicMock, patch


def test_send_narration_flag_publishes_to_topic():
    with patch("app.kafka_producer.Producer") as MockProducer, \
         patch("app.config.pipeline_settings") as mock_settings:
        mock_settings.kafka_bootstrap_servers = "localhost:9092"
        mock_producer_instance = MagicMock()
        MockProducer.return_value = mock_producer_instance

        from app.kafka_producer import PipelineKafkaProducer
        producer = PipelineKafkaProducer()
        producer.send_narration_flag(
            camera_id="cam-01",
            flag_type="dumping_motion",
            confidence=0.82,
            narration_text="Person appears to throw a bag near the fence.",
            evidence_path="/app/evidence/narration/cam-01/evt_1.json",
        )

        call = mock_producer_instance.produce.call_args
        assert call.kwargs["topic"] == "wastewatch.narration.flags"
        assert call.kwargs["key"] == b"cam-01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pipeline && python -m pytest tests/test_kafka_producer_narration.py -v`
Expected: FAIL — `AttributeError: 'PipelineKafkaProducer' object has no attribute 'send_narration_flag'`

- [ ] **Step 3: Add topic constant to backend config (shared naming reference)**

```python
# backend/app/config/kafka.py — add after line 18 (TOPIC_MISSES)
TOPIC_NARRATION_FLAGS = "wastewatch.narration.flags"

# add after line 30 (GROUP_HEALER)
GROUP_NARRATION_WORKER = "wastewatch.group.narration-worker"
```

- [ ] **Step 4: Add producer method**

```python
# pipeline/app/kafka_producer.py — add after send_alert (after line 135)
    def send_narration_flag(
        self,
        camera_id: str,
        flag_type: str,
        confidence: float,
        narration_text: str,
        evidence_path: str | None = None,
    ) -> None:
        """Publish a narration-derived anomaly flag to wastewatch.narration.flags."""
        try:
            self._producer.produce(
                topic="wastewatch.narration.flags",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": camera_id,
                    "flag_type": flag_type,
                    "confidence": confidence,
                    "narration_text": narration_text,
                    "evidence_path": evidence_path,
                    "timestamp": time.time(),
                }),
            )
        except Exception:
            logger.exception("Failed to send narration flag for %s", camera_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd pipeline && python -m pytest tests/test_kafka_producer_narration.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/config/kafka.py pipeline/app/kafka_producer.py pipeline/tests/test_kafka_producer_narration.py
git commit -m "feat: add wastewatch.narration.flags topic + producer method"
```

### Task 5: NarrationFlag DB model + migration

**Files:**
- Modify: `backend/app/db/models.py` (add after `EvidenceFile`, line 235)
- Create: `backend/alembic/versions/006_narration_flags.py`
- Test: `backend/tests/test_narration_flag_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_narration_flag_model.py
"""Tests for the NarrationFlag model."""


def test_narration_flag_model_has_expected_columns():
    from app.db.models import NarrationFlag

    cols = set(NarrationFlag.__table__.columns.keys())
    assert cols == {
        "id", "camera_id", "timestamp", "flag_type", "confidence",
        "narration_text", "evidence_path", "review_status",
        "reviewed_by", "reviewed_at", "created_at",
    }
    assert NarrationFlag.__tablename__ == "narration_flags"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_narration_flag_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'NarrationFlag'`

- [ ] **Step 3: Add the model**

```python
# backend/app/db/models.py — insert after EvidenceFile class (after line 234)
class NarrationFlag(Base):
    __tablename__ = "narration_flags"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flag_type: Mapped[str] = mapped_column(String(50), nullable=False)  # dumping_motion, uncovered_load, anomaly
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    narration_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, confirmed, dismissed
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_narration_flag_model.py -v`
Expected: PASS

- [ ] **Step 5: Write the migration**

```python
# backend/alembic/versions/006_narration_flags.py
"""Add narration_flags table

Revision ID: 006
Revises: 005
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "narration_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("camera_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flag_type", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("narration_text", sa.Text(), nullable=False),
        sa.Column("evidence_path", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_narration_flags_review_status_created_at",
        "narration_flags",
        ["review_status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_narration_flags_review_status_created_at", table_name="narration_flags")
    op.drop_table("narration_flags")
```

- [ ] **Step 6: Run migration against local DB**

Run: `cd backend && alembic upgrade head`
Expected: `Running upgrade 005 -> 006, Add narration_flags table`

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/006_narration_flags.py backend/tests/test_narration_flag_model.py
git commit -m "feat: add narration_flags table and model"
```

### Task 6: Narration flags Kafka consumer

**Files:**
- Create: `backend/app/evidence/narration_worker.py`
- Modify: `backend/app/main.py` (wire lifespan start/stop, follow existing `EvidenceWorker` pattern)
- Test: `backend/tests/test_narration_worker.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_narration_worker.py
"""Tests for NarrationFlagWorker."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_process_narration_flag_inserts_row():
    from app.evidence.narration_worker import NarrationFlagWorker

    worker = NarrationFlagWorker.__new__(NarrationFlagWorker)
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = False

    mock_camera_result = MagicMock()
    mock_camera_result.scalar_one_or_none.return_value = MagicMock(id="cam-uuid")
    mock_session.execute = AsyncMock(return_value=mock_camera_result)

    with patch("app.evidence.narration_worker.async_session", return_value=mock_session):
        await worker._process_flag({
            "camera_id": "cam-01",
            "flag_type": "dumping_motion",
            "confidence": 0.82,
            "narration_text": "Person throws a bag near the fence.",
            "evidence_path": "/app/evidence/narration/cam-01/evt_1.json",
            "timestamp": datetime.now(timezone.utc).timestamp(),
        })

    mock_session.add.assert_called_once()
    added_row = mock_session.add.call_args.args[0]
    assert added_row.flag_type == "dumping_motion"
    assert added_row.review_status == "pending"
    mock_session.commit.assert_awaited_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_narration_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.evidence.narration_worker'`

- [ ] **Step 3: Implement the worker**

```python
# backend/app/evidence/narration_worker.py
"""Kafka consumer: wastewatch.narration.flags -> narration_flags table.

Camera name (as published by the pipeline) is resolved to its DB UUID the
same way backend/app/api/routes/evidence.py does for evidence files.
"""
import logging
from datetime import datetime, timezone

import orjson
from aiokafka import AIOKafkaConsumer
from sqlalchemy import select

from app.config.kafka import TOPIC_NARRATION_FLAGS, GROUP_NARRATION_WORKER
from app.db.engine import async_session
from app.db.models import Camera, NarrationFlag

logger = logging.getLogger(__name__)


class NarrationFlagWorker:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task = None

    async def start(self):
        import asyncio
        self._consumer = AIOKafkaConsumer(
            TOPIC_NARRATION_FLAGS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_NARRATION_WORKER,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume())
        logger.info("NarrationFlagWorker started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        if self._consumer:
            await self._consumer.stop()

    async def _consume(self):
        try:
            async for msg in self._consumer:
                await self._process_flag(msg.value)
        except Exception:
            logger.exception("Narration flag consumer error")

    async def _process_flag(self, data: dict):
        try:
            camera_name = data.get("camera_id", "unknown")
            async with async_session() as session:
                result = await session.execute(select(Camera).where(Camera.name == camera_name))
                camera = result.scalar_one_or_none()
                if camera is None:
                    logger.warning("Narration flag for unknown camera %s dropped", camera_name)
                    return
                row = NarrationFlag(
                    camera_id=camera.id,
                    timestamp=datetime.fromtimestamp(data.get("timestamp", 0), tz=timezone.utc),
                    flag_type=data.get("flag_type", "anomaly"),
                    confidence=float(data.get("confidence", 0.0)),
                    narration_text=data.get("narration_text", ""),
                    evidence_path=data.get("evidence_path"),
                    review_status="pending",
                )
                session.add(row)
                await session.commit()
        except Exception:
            logger.exception("Failed to process narration flag")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_narration_worker.py -v`
Expected: PASS

- [ ] **Step 5: Wire into app lifespan**

Find the existing `EvidenceWorker` start/stop calls in `backend/app/main.py` (search `EvidenceWorker(`) and add alongside them:

```python
# backend/app/main.py — near the EvidenceWorker() instantiation/start/stop
    from app.evidence.narration_worker import NarrationFlagWorker
    narration_worker = NarrationFlagWorker(bootstrap_servers=settings_module.settings.kafka_bootstrap_servers)
    await narration_worker.start()
    # ... store reference alongside other workers for shutdown, call narration_worker.stop() in shutdown block
```

(Exact variable names for `settings_module`/shutdown block depend on `main.py`'s existing lifespan structure — match how `EvidenceWorker` is already registered there, same file, same function.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/evidence/narration_worker.py backend/app/main.py backend/tests/test_narration_worker.py
git commit -m "feat: add narration flags Kafka consumer"
```

### Task 7: Narration flags review API

**Files:**
- Create: `backend/app/api/routes/narration_flags.py`
- Modify: `backend/app/main.py:75` (register router, alongside `evidence.router`)
- Test: `backend/tests/test_narration_flags_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_narration_flags_api.py
"""Tests for narration flags review API."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
import uuid


@pytest.mark.asyncio
async def test_confirm_flag_creates_violation_event():
    from app.api.routes.narration_flags import confirm_flag
    from app.db.models import NarrationFlag

    flag_id = uuid.uuid4()
    flag = NarrationFlag(
        id=flag_id, camera_id=uuid.uuid4(), timestamp=datetime.now(timezone.utc),
        flag_type="dumping_motion", confidence=0.8, narration_text="test",
        evidence_path=None, review_status="pending",
    )
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = flag
    mock_db.execute = AsyncMock(return_value=mock_result)

    await confirm_flag(flag_id=flag_id, db=mock_db, _user={"sub": "admin1"})

    assert flag.review_status == "confirmed"
    assert flag.reviewed_by == "admin1"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_narration_flags_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.routes.narration_flags'`

- [ ] **Step 3: Implement the routes**

```python
# backend/app/api/routes/narration_flags.py
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.auth.middleware import require_viewer, require_admin
from app.db.models import NarrationFlag, ViolationEvent, EventType, AlertSeverity

router = APIRouter(prefix="/narration-flags", tags=["narration-flags"])


@router.get("", response_model=list[dict])
async def list_narration_flags(
    review_status: str | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_viewer),
):
    query = select(NarrationFlag).order_by(desc(NarrationFlag.created_at)).limit(limit)
    if review_status:
        query = query.where(NarrationFlag.review_status == review_status)
    result = await db.execute(query)
    flags = result.scalars().all()
    return [
        {
            "id": str(f.id), "camera_id": str(f.camera_id), "timestamp": f.timestamp.isoformat(),
            "flag_type": f.flag_type, "confidence": f.confidence, "narration_text": f.narration_text,
            "evidence_path": f.evidence_path, "review_status": f.review_status,
            "reviewed_by": f.reviewed_by,
        }
        for f in flags
    ]


@router.post("/{flag_id}/confirm", response_model=dict)
async def confirm_flag(
    flag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(select(NarrationFlag).where(NarrationFlag.id == flag_id))
    flag = result.scalar_one_or_none()
    if flag is None:
        raise HTTPException(status_code=404, detail="Narration flag not found")
    if flag.review_status != "pending":
        raise HTTPException(status_code=400, detail=f"Flag already {flag.review_status}")

    flag.review_status = "confirmed"
    flag.reviewed_by = _user.get("sub", "unknown")
    flag.reviewed_at = datetime.now(timezone.utc)

    event = ViolationEvent(
        camera_id=flag.camera_id,
        timestamp=flag.timestamp,
        event_type=EventType.dumping if flag.flag_type in ("dumping_motion", "uncovered_load") else EventType.suspicious,
        severity=AlertSeverity.warning,
        confidence=flag.confidence,
        vlm_response={"source": "narration", "flag_type": flag.flag_type, "narration_text": flag.narration_text},
    )
    db.add(event)
    await db.commit()
    return {"id": str(flag.id), "review_status": flag.review_status, "violation_event_id": str(event.id)}


@router.post("/{flag_id}/dismiss", response_model=dict)
async def dismiss_flag(
    flag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_admin),
):
    result = await db.execute(select(NarrationFlag).where(NarrationFlag.id == flag_id))
    flag = result.scalar_one_or_none()
    if flag is None:
        raise HTTPException(status_code=404, detail="Narration flag not found")
    if flag.review_status != "pending":
        raise HTTPException(status_code=400, detail=f"Flag already {flag.review_status}")

    flag.review_status = "dismissed"
    flag.reviewed_by = _user.get("sub", "unknown")
    flag.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(flag.id), "review_status": flag.review_status}
```

Check `backend/app/db/models.py` for `EventType`/`AlertSeverity` enum member names before running (they're referenced by `ViolationEvent` at model_lines 81-86) — use `dumping`/`suspicious`/`warning` only if those exact members exist; if not, grep `class EventType` / `class AlertSeverity` in `backend/app/db/models.py` and substitute the real member names.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_narration_flags_api.py -v`
Expected: PASS

- [ ] **Step 5: Register the router**

```python
# backend/app/main.py — add alongside line 75 (evidence.router)
    from app.api.routes import narration_flags
    app.include_router(narration_flags.router, prefix="/api")
```

(Add the import near the other route imports at the top of `main.py`, not inline, if that's the existing convention — check how `evidence` is imported.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/narration_flags.py backend/app/main.py backend/tests/test_narration_flags_api.py
git commit -m "feat: add narration flags review API"
```

### Task 8: NarrationClient (simulated duplex session)

**Files:**
- Create: `pipeline/app/narration_client.py`
- Test: `pipeline/tests/test_narration_client.py`

- [ ] **Step 1: Write the failing test**

```python
# pipeline/tests/test_narration_client.py
"""Tests for NarrationClient (simulated backend — real vllm-omni swap is a follow-up)."""
from unittest.mock import MagicMock


def test_submit_frame_calls_narrate_and_returns_flags_on_keyword():
    from app.narration_client import NarrationClient

    client = NarrationClient(camera_id="cam-01")
    client._narrate = MagicMock(return_value={
        "narration": "A person tosses a bag over the fence near the bin area.",
        "flags": [{"type": "dumping_motion", "confidence": 0.77, "description": "tossing motion over fence"}],
    })

    result = client.submit_frame(jpeg_bytes=b"fake-jpeg")

    assert result["flags"][0]["type"] == "dumping_motion"
    client._narrate.assert_called_once_with(b"fake-jpeg")


def test_submit_frame_no_flags_when_narration_quiet():
    from app.narration_client import NarrationClient

    client = NarrationClient(camera_id="cam-01")
    client._narrate = MagicMock(return_value={"narration": "Empty parking lot, no activity.", "flags": []})

    result = client.submit_frame(jpeg_bytes=b"fake-jpeg")

    assert result["flags"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pipeline && python -m pytest tests/test_narration_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.narration_client'`

- [ ] **Step 3: Implement the client**

```python
# pipeline/app/narration_client.py
"""Per-camera narration session against MiniCPM-o 4.5.

Today this is a SIMULATED backend: `_narrate` is a stub that a real
vllm-omni duplex streaming call (streaming_prefill/streaming_generate)
replaces later, once GPU capacity + vllm-omni maturity are validated.
This client's public shape (submit_frame -> {"narration", "flags"}) is
the contract callers (the pipeline's narration sampler) depend on, so the
backend can swap without touching call sites.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class NarrationClient:
    def __init__(self, camera_id: str, target: str | None = None) -> None:
        self._camera_id = camera_id
        self._target = target or os.environ.get("WW_NARRATION_TARGET", "vlm-worker:50061")

    def submit_frame(self, jpeg_bytes: bytes) -> dict:
        """Feed one sampled frame into the narration session, return the
        latest narration + any flags it raised."""
        try:
            return self._narrate(jpeg_bytes)
        except Exception:
            logger.exception("Narration call failed for %s", self._camera_id)
            return {"narration": "", "flags": []}

    def _narrate(self, jpeg_bytes: bytes) -> dict:
        """Placeholder backend — replace with a real vllm-omni duplex
        streaming call. Returns no flags so the pipeline path is inert
        until a real backend (or a test) overrides this method."""
        return {"narration": "", "flags": []}

    def close(self) -> None:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pipeline && python -m pytest tests/test_narration_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/app/narration_client.py pipeline/tests/test_narration_client.py
git commit -m "feat: add NarrationClient with simulated backend"
```

### Task 9: Narration sampler wired into pipeline via frame_jpeg_cache timer

**Files:**
- Modify: `pipeline/app/pipeline.py` (near `probe_ctx` construction, ~line 1272-1289, and pipeline setup/teardown)
- Test: `pipeline/tests/test_narration_sampler.py`

- [ ] **Step 1: Write the failing test**

```python
# pipeline/tests/test_narration_sampler.py
"""Tests for the narration sampler (GLib-timer frame_jpeg_cache reader)."""
from unittest.mock import MagicMock


def test_narration_tick_submits_cached_frames_and_publishes_flags():
    from app.narration_sampler import narration_tick

    fake_client = MagicMock()
    fake_client.submit_frame.return_value = {
        "narration": "Overflowing bin, waste spilling onto the ground.",
        "flags": [{"type": "uncovered_load", "confidence": 0.65, "description": "spill"}],
    }
    kafka_producer = MagicMock()
    ctx = {
        "frame_jpeg_cache": {0: b"jpeg-bytes-for-source-0"},
        "narration_clients": {0: fake_client},
        "source_id_to_camera": {0: "cam-01"},
        "kafka_producer": kafka_producer,
    }

    narration_tick(ctx)

    fake_client.submit_frame.assert_called_once_with(jpeg_bytes=b"jpeg-bytes-for-source-0")
    kafka_producer.send_narration_flag.assert_called_once_with(
        camera_id="cam-01",
        flag_type="uncovered_load",
        confidence=0.65,
        narration_text="Overflowing bin, waste spilling onto the ground.",
        evidence_path=None,
    )


def test_narration_tick_skips_sources_with_no_flags():
    from app.narration_sampler import narration_tick

    fake_client = MagicMock()
    fake_client.submit_frame.return_value = {"narration": "quiet", "flags": []}
    kafka_producer = MagicMock()
    ctx = {
        "frame_jpeg_cache": {0: b"jpeg"},
        "narration_clients": {0: fake_client},
        "source_id_to_camera": {0: "cam-01"},
        "kafka_producer": kafka_producer,
    }

    narration_tick(ctx)

    kafka_producer.send_narration_flag.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pipeline && python -m pytest tests/test_narration_sampler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.narration_sampler'`

- [ ] **Step 3: Implement the sampler**

```python
# pipeline/app/narration_sampler.py
"""GLib-timer-driven narration sampler.

Reads the frame_jpeg_cache that capture_frame_probe already populates
(pipeline.py's branch-3 capture probe) instead of adding a new
GStreamer tee/queue before nvstreamdemux -- same effect (narration
reads inference-derived frames in parallel to the FSM path, without
re-running detection) with no relinking risk to the existing tee
structure, which sys.exit(1)s on link failure.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def narration_tick(ctx: dict) -> None:
    """Called on a GLib timeout at WW_NARRATION_FPS-derived interval.

    For each camera with a cached frame, submits it to that camera's
    NarrationClient and publishes any resulting flags to Kafka.
    """
    cache: dict[int, bytes] = ctx.get("frame_jpeg_cache", {})
    clients: dict[int, object] = ctx.get("narration_clients", {})
    source_id_to_camera: dict[int, str] = ctx.get("source_id_to_camera", {})
    kafka_producer = ctx.get("kafka_producer")

    for source_id, jpeg_bytes in cache.items():
        client = clients.get(source_id)
        if client is None or jpeg_bytes is None:
            continue
        camera_id = source_id_to_camera.get(source_id, f"source-{source_id}")
        try:
            result = client.submit_frame(jpeg_bytes=jpeg_bytes)
        except Exception:
            logger.exception("Narration submit failed for %s", camera_id)
            continue

        for flag in result.get("flags", []):
            if kafka_producer is not None:
                kafka_producer.send_narration_flag(
                    camera_id=camera_id,
                    flag_type=flag.get("type", "anomaly"),
                    confidence=float(flag.get("confidence", 0.0)),
                    narration_text=result.get("narration", ""),
                    evidence_path=None,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd pipeline && python -m pytest tests/test_narration_sampler.py -v`
Expected: PASS

- [ ] **Step 5: Wire the timer into pipeline setup**

Locate where `probe_ctx` is built in `pipeline.py` (~line 1272) and where the GLib main loop / bus is set up (search for `GLib.timeout_add` or `MainLoop` in `pipeline.py`; if none exists yet, add one near pipeline start). Add:

```python
# pipeline/app/pipeline.py — near probe_ctx construction (~line 1272), extend probe_ctx:
        narration_ctx = {
            "frame_jpeg_cache": frame_jpeg_cache,
            "narration_clients": {},  # populated per-source as cameras register, see source_manager
            "source_id_to_camera": {},  # populated per-source as cameras register
            "kafka_producer": kafka_producer,
        }

# near pipeline startup (wherever the GLib main loop is created), before loop.run():
        from app.narration_sampler import narration_tick
        narration_fps = float(os.environ.get("WW_NARRATION_FPS", "1"))
        GLib.timeout_add(int(1000 / narration_fps), lambda: (narration_tick(narration_ctx), True)[1])
```

Populate `narration_ctx["narration_clients"][source_id]` and `narration_ctx["source_id_to_camera"][source_id]` wherever `source_manager` currently registers a new camera/source (search `pad_to_camera` usage, same registration point used for `probe_ctx["pad_to_camera"]`) — construct a `NarrationClient(camera_id=camera_name)` there and store it keyed by `source_id`.

This step's exact insertion points depend on reading the surrounding 100 lines of `pipeline.py` at implementation time (source registration callback, GLib import, existing main-loop construction) — do that read before editing, since the file is large and line numbers shift as earlier tasks land.

- [ ] **Step 6: Commit**

```bash
git add pipeline/app/pipeline.py pipeline/app/narration_sampler.py pipeline/tests/test_narration_sampler.py
git commit -m "feat: wire narration sampler into pipeline via frame_jpeg_cache timer"
```

### Task 10: End-to-end simulator validation

**Files:**
- Test: `backend/tests/test_narration_flow_e2e.py`

- [ ] **Step 1: Write an end-to-end test wiring narration_tick -> Kafka producer stub -> NarrationFlagWorker -> DB -> API list endpoint**

```python
# backend/tests/test_narration_flow_e2e.py
"""End-to-end simulator test: a narration flag dict flows through the
worker into the DB and is visible via the list API. Simulates the
pipeline's Kafka message shape directly -- no real Kafka broker needed."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
import uuid


@pytest.mark.asyncio
async def test_flag_message_persists_and_is_listable():
    from app.evidence.narration_worker import NarrationFlagWorker
    from app.api.routes.narration_flags import list_narration_flags
    from app.db.models import NarrationFlag, Camera

    camera_uuid = uuid.uuid4()
    fake_camera = Camera(id=camera_uuid, name="cam-01")

    stored_rows = []

    class FakeSession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def execute(self, query):
            result = MagicMock()
            result.scalar_one_or_none.return_value = fake_camera
            result.scalars.return_value.all.return_value = stored_rows
            return result
        def add(self, row):
            stored_rows.append(row)
        async def commit(self):
            pass

    worker = NarrationFlagWorker.__new__(NarrationFlagWorker)
    fake_session = FakeSession()

    with patch("app.evidence.narration_worker.async_session", return_value=fake_session):
        await worker._process_flag({
            "camera_id": "cam-01",
            "flag_type": "dumping_motion",
            "confidence": 0.77,
            "narration_text": "Person tosses a bag over the fence.",
            "evidence_path": None,
            "timestamp": datetime.now(timezone.utc).timestamp(),
        })

    assert len(stored_rows) == 1
    assert stored_rows[0].review_status == "pending"

    listed = await list_narration_flags(review_status=None, limit=50, db=fake_session, _user={"sub": "viewer1"})
    assert listed[0]["flag_type"] == "dumping_motion"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_narration_flow_e2e.py -v`
Expected: PASS — if it fails on a mismatched `Camera` model field name (e.g. `name` column doesn't exist), grep `class Camera` in `backend/app/db/models.py` first and fix the fixture to match real columns.

- [ ] **Step 3: Run full test suites**

Run: `cd backend && python -m pytest -q` and `cd pipeline && python -m pytest -q`
Expected: all PASS, no regressions in existing VLM/evidence/alert tests.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_narration_flow_e2e.py
git commit -m "test: add narration flow end-to-end simulator test"
```

---

## Deferred to a later plan (not in today's scope)

- Real `vllm-omni` deployment + `NarrationClient._narrate` swap to actual duplex streaming calls.
- `WW_NARRATION_MAX_CONCURRENT_CAMS` GPU-sharing throttle (needs real load testing against a deployed model).
- Frontend triage UI for `narration_flags` (spec's API is backend-only; UI wasn't in the approved spec scope).
- Confirming AWQ/GPTQ quantized checkpoint availability for MiniCPM-o 4.5 and GPU headroom sizing.
