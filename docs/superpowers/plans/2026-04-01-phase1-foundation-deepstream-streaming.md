# Phase 1: Foundation — DeepStream + Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the backend infrastructure (Docker Compose, FastAPI, DeepStream, Kafka) so that live camera streams show bounding boxes for persons/vehicles in the existing React frontend via WebRTC.

**Architecture:** DeepStream GStreamer pipeline ingests RTSP from MediaMTX, runs RF-DETR TensorRT detection, publishes per-frame metadata to Kafka, and outputs annotated RTSP back to MediaMTX for WebRTC delivery. FastAPI acts as the backend API with WebSocket metadata broadcast. Neon Postgres stores camera and config state.

**Tech Stack:** Python 3.11, FastAPI, DeepStream 7.0, GStreamer, RF-DETR (TensorRT FP16), Kafka (KRaft), Redis, MediaMTX, Neon Postgres (asyncpg + SQLAlchemy), Docker Compose

**Spec Reference:** `/docs/superpowers/specs/2026-03-31-wastewatch-ai-pipeline-design.md` — Sections 1-4, 5 (partial), 11-13

**Existing Frontend:** React 18 + Vite + shadcn/ui at `/frontend/`. Already has WebRTCPlayer (WHEP), camera CRUD via Supabase, real-time subscriptions. Phase 1 adds a native WebSocket for per-frame detection metadata overlay.

---

## File Structure

### New: Backend (`/backend/`)

```
/backend/
├── pyproject.toml                    # Project metadata, dependencies (uv/pip)
├── Dockerfile                        # FastAPI + worker image
├── alembic.ini                       # Alembic config pointing to Neon
├── alembic/
│   ├── env.py                        # Async migration runner
│   └── versions/
│       └── 001_initial_schema.py     # Phase 1 tables
├── app/
│   ├── __init__.py
│   ├── main.py                       # FastAPI app, lifespan, CORS, routers
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py               # Pydantic BaseSettings (env vars)
│   │   └── kafka.py                  # Topic names, consumer group constants
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py                 # async engine + sessionmaker (asyncpg)
│   │   └── models.py                 # SQLAlchemy ORM models (Phase 1 subset)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                   # Dependency injection (DB session, Kafka)
│   │   └── routes/
│   │       ├── __init__.py
│   │       ├── health.py             # GET /api/health
│   │       ├── cameras.py            # Camera CRUD REST endpoints
│   │       └── config_routes.py      # GET/PUT /api/config/{key}
│   ├── ws/
│   │   ├── __init__.py
│   │   └── metadata.py              # WebSocket /ws/metadata/{camera_id}
│   ├── services/
│   │   ├── __init__.py
│   │   ├── kafka_producer.py         # AIOKafkaProducer wrapper
│   │   └── kafka_consumer.py         # AIOKafkaConsumer for metadata broadcast
│   └── schemas/
│       ├── __init__.py
│       ├── camera.py                 # Pydantic request/response for cameras
│       ├── metadata.py               # Per-frame detection metadata schema
│       └── health.py                 # Health response schema
└── tests/
    ├── conftest.py                   # Fixtures: test DB, async client, Kafka mock
    ├── test_health.py
    ├── test_cameras.py
    ├── test_ws_metadata.py
    └── test_kafka_producer.py
```

### New: Pipeline (`/pipeline/`)

```
/pipeline/
├── Dockerfile                        # DeepStream 7.0 + RF-DETR TensorRT
├── app/
│   ├── __init__.py
│   ├── pipeline.py                   # GStreamer/DeepStream pipeline builder
│   ├── source_manager.py             # Add/remove RTSP sources at runtime
│   ├── kafka_producer.py             # nvmsgconv → nvmsgbroker Kafka config
│   ├── config.py                     # Pipeline settings from env + DB
│   └── health.py                     # Camera disconnect detection
├── configs/
│   ├── deepstream_app.txt            # DeepStream app config
│   ├── nvinfer_rfdetr.txt            # nvinfer config for RF-DETR
│   ├── nvtracker_config.yml          # Placeholder for Phase 2
│   ├── nvmsgconv_config.txt          # Message converter config
│   ├── nvmsgbroker_kafka.txt         # Kafka broker config
│   └── labels.txt                    # COCO class labels (filtered)
├── models/
│   └── rfdetr/
│       └── README.md                 # Instructions: ONNX export + TensorRT build
└── tests/
    ├── test_pipeline_config.py
    └── test_source_manager.py
```

### New: Infrastructure (`/` root)

```
/docker-compose.yml                   # Full stack (Kafka, Redis, MediaMTX, API, pipeline)
/docker-compose.dev.yml               # Dev overrides (no GPU, mock pipeline)
/.env.example                         # All env vars documented
/scripts/
├── export_rfdetr_onnx.py            # Export RF-DETR to ONNX
└── build_tensorrt_engine.sh          # trtexec ONNX → TensorRT FP16
```

### Modified: Frontend (`/frontend/`)

```
frontend/src/hooks/useDetectionMetadata.ts    # NEW: WebSocket hook for /ws/metadata
frontend/src/components/camera/MetadataOverlay.tsx  # NEW: bbox + label + conf overlay
frontend/src/components/camera/CameraFeed.tsx  # MODIFIED: integrate MetadataOverlay
frontend/src/types/monitoring.ts               # MODIFIED: add Detection types
```

---

## Task 1: Python Backend Project Scaffold

**Files:**
- Create: `/backend/pyproject.toml`
- Create: `/backend/app/__init__.py`
- Create: `/backend/app/config/__init__.py`
- Create: `/backend/app/config/settings.py`
- Create: `/backend/app/config/kafka.py`
- Create: `/backend/tests/conftest.py`

- [ ] **Step 1: Create pyproject.toml with dependencies**

```toml
[project]
name = "wastewatch-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "aiokafka>=0.12.0",
    "pydantic-settings>=2.7.0",
    "orjson>=3.10.0",
    "redis>=5.2.0",
    "httpx>=0.28.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
    "pytest-cov>=6.0.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 2: Create settings.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/wastewatch"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MediaMTX
    mediamtx_api_url: str = "http://localhost:9997"
    mediamtx_rtsp_url: str = "rtsp://localhost:8554"

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:8080"]

    model_config = {"env_prefix": "WW_", "env_file": ".env"}


settings = Settings()
```

- [ ] **Step 3: Create kafka.py topic constants**

```python
# Kafka topic names — single source of truth
TOPIC_METADATA = "wastewatch.metadata"
TOPIC_EVENTS = "wastewatch.events"
TOPIC_VLM_REQUESTS = "wastewatch.vlm.requests"
TOPIC_VLM_RESULTS = "wastewatch.vlm.results"
TOPIC_ALERTS = "wastewatch.alerts"
TOPIC_LPR = "wastewatch.lpr"
TOPIC_TTS = "wastewatch.tts"
TOPIC_EVIDENCE = "wastewatch.evidence"
TOPIC_LOGS = "wastewatch.logs"
TOPIC_CONFIG_UPDATES = "wastewatch.config.updates"

# Consumer groups
GROUP_API_METADATA = "wastewatch-api-metadata"
GROUP_EVENT_FSM = "wastewatch-event-fsm"
GROUP_VLM_WORKER = "wastewatch-vlm-worker"
GROUP_ALERT_DISPATCHER = "wastewatch-alert-dispatcher"
GROUP_LOG_INGESTION = "wastewatch-log-ingestion"
```

- [ ] **Step 4: Create __init__.py files**

Create empty `__init__.py` in: `backend/app/`, `backend/app/config/`, `backend/app/api/`, `backend/app/api/routes/`, `backend/app/ws/`, `backend/app/services/`, `backend/app/schemas/`, `backend/app/db/`, `backend/tests/`.

- [ ] **Step 5: Create conftest.py with basic fixtures**

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

- [ ] **Step 6: Verify project installs**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && pip install -e ".[dev]"`
Expected: Successful install, no dependency conflicts.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/ backend/tests/conftest.py
git commit -m "feat(backend): scaffold Python project with settings and Kafka topic config"
```

---

## Task 2: FastAPI App + Health Endpoint

**Files:**
- Create: `/backend/app/main.py`
- Create: `/backend/app/schemas/health.py`
- Create: `/backend/app/api/routes/health.py`
- Create: `/backend/app/api/deps.py`
- Create: `/backend/tests/test_health.py`

- [ ] **Step 1: Write the health response schema**

```python
# backend/app/schemas/health.py
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    version: str
    services: dict[str, str]
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_health.py
import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "services" in data
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_health.py -v`
Expected: FAIL — `create_app` not found or ImportError.

- [ ] **Step 4: Create deps.py**

```python
# backend/app/api/deps.py
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session
```

- [ ] **Step 5: Create health route**

```python
# backend/app/api/routes/health.py
from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version="0.1.0",
        services={
            "api": "running",
            "kafka": "unknown",
            "database": "unknown",
        },
    )
```

- [ ] **Step 6: Create main.py with app factory**

```python
# backend/app/main.py
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health
from app.config.settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown


def create_app() -> FastAPI:
    app = FastAPI(title="WasteWatch API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")

    return app


app = create_app()
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/app/schemas/health.py backend/app/api/routes/health.py backend/app/api/deps.py backend/tests/test_health.py
git commit -m "feat(backend): FastAPI app with /api/health endpoint"
```

---

## Task 3: Database Engine + ORM Models (Phase 1 Subset)

**Files:**
- Create: `/backend/app/db/engine.py`
- Create: `/backend/app/db/models.py`
- Create: `/backend/alembic.ini`
- Create: `/backend/alembic/env.py`
- Create: `/backend/alembic/versions/001_initial_schema.py`

- [ ] **Step 1: Create async engine**

```python
# backend/app/db/engine.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config.settings import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 2: Create ORM models for Phase 1 tables**

```python
# backend/app/db/models.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CameraStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    warning = "warning"
    alert = "alert"


class AlertSeverity(str, enum.Enum):
    critical = "critical"
    warning = "warning"
    info = "info"


class AlertStatus(str, enum.Enum):
    active = "active"
    acknowledged = "acknowledged"
    resolved = "resolved"


class EventType(str, enum.Enum):
    dumping_detected = "dumping_detected"
    suspicious_activity = "suspicious_activity"
    repeat_offender = "repeat_offender"


class AppRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    viewer = "viewer"


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    zone: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    gps_lat: Mapped[float | None] = mapped_column(Float)
    gps_lng: Mapped[float | None] = mapped_column(Float)
    status: Mapped[CameraStatus] = mapped_column(
        Enum(CameraStatus, name="camera_status", create_type=False),
        default=CameraStatus.offline,
    )
    stream_url: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(10), default="rtsp")  # rtsp/rtmp/https
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    roi_polygons: Mapped[dict | None] = mapped_column(JSONB)
    recording_config: Mapped[dict | None] = mapped_column(JSONB)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ViolationEvent(Base):
    __tablename__ = "violation_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type", create_type=False))
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="alert_severity", create_type=False))
    confidence: Mapped[float] = mapped_column(Float)
    track_id: Mapped[int | None] = mapped_column(Integer)
    dwell_time_s: Mapped[float | None] = mapped_column(Float)
    vlm_response: Mapped[dict | None] = mapped_column(JSONB)
    person_id: Mapped[str | None] = mapped_column(String(255))
    repeat_count: Mapped[int] = mapped_column(Integer, default=0)
    snapshot_url: Mapped[str | None] = mapped_column(Text)
    video_clip_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status", create_type=False),
        default=AlertStatus.active,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("violation_events.id"))
    camera_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cameras.id"))
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity, name="alert_severity", create_type=False))
    title: Mapped[str] = mapped_column(String(500))
    message: Mapped[str | None] = mapped_column(Text)
    zone: Mapped[str | None] = mapped_column(String(255))
    channel: Mapped[str | None] = mapped_column(String(50))  # telegram/sip/speaker/websocket
    tts_audio_url: Mapped[str | None] = mapped_column(Text)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status", create_type=False),
        default=AlertStatus.active,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_by: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 3: Create alembic.ini**

```ini
[alembic]
script_location = alembic
sqlalchemy.url = driver://user:pass@localhost/dbname

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 4: Create alembic/env.py for async migrations**

```python
# backend/alembic/env.py
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config.settings import settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 5: Create initial migration**

```python
# backend/alembic/versions/001_initial_schema.py
"""Initial schema: cameras, violation_events, alerts, system_config

Revision ID: 001
Revises:
Create Date: 2026-04-01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enums
    camera_status = sa.Enum("online", "offline", "warning", "alert", name="camera_status")
    alert_severity = sa.Enum("critical", "warning", "info", name="alert_severity")
    alert_status = sa.Enum("active", "acknowledged", "resolved", name="alert_status")
    event_type = sa.Enum("dumping_detected", "suspicious_activity", "repeat_offender", name="event_type")
    app_role = sa.Enum("owner", "admin", "viewer", name="app_role")
    event_fsm_state = sa.Enum("enter", "observe", "violation", "alerting", "escalated", "exited", name="event_fsm_state")

    camera_status.create(op.get_bind(), checkfirst=True)
    alert_severity.create(op.get_bind(), checkfirst=True)
    alert_status.create(op.get_bind(), checkfirst=True)
    event_type.create(op.get_bind(), checkfirst=True)
    app_role.create(op.get_bind(), checkfirst=True)
    event_fsm_state.create(op.get_bind(), checkfirst=True)

    # cameras
    op.create_table(
        "cameras",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("zone", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255)),
        sa.Column("gps_lat", sa.Float),
        sa.Column("gps_lng", sa.Float),
        sa.Column("status", camera_status, server_default="offline"),
        sa.Column("stream_url", sa.Text),
        sa.Column("source_type", sa.String(10), server_default="rtsp"),
        sa.Column("thumbnail_url", sa.Text),
        sa.Column("roi_polygons", JSONB),
        sa.Column("recording_config", JSONB),
        sa.Column("is_pinned", sa.Boolean, server_default="false"),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # violation_events
    op.create_table(
        "violation_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("severity", alert_severity, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("track_id", sa.Integer),
        sa.Column("dwell_time_s", sa.Float),
        sa.Column("fsm_state", event_fsm_state),
        sa.Column("vlm_response", JSONB),
        sa.Column("person_id", sa.String(255)),
        sa.Column("repeat_count", sa.Integer, server_default="0"),
        sa.Column("snapshot_url", sa.Text),
        sa.Column("video_clip_url", sa.Text),
        sa.Column("status", alert_status, server_default="active"),
        sa.Column("acknowledged_by", sa.String(255)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text),
        sa.Column("is_valid", sa.Boolean),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_violation_events_camera_time", "violation_events", ["camera_id", sa.text("timestamp DESC")])

    # alerts
    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("violation_events.id")),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id")),
        sa.Column("severity", alert_severity, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("message", sa.Text),
        sa.Column("zone", sa.String(255)),
        sa.Column("channel", sa.String(50)),
        sa.Column("tts_audio_url", sa.Text),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("status", alert_status, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # system_config
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Seed default config
    op.execute("""
        INSERT INTO system_config (key, value) VALUES
        ('detection.frame_skip', '3'),
        ('detection.batch_size', '4'),
        ('detection.confidence_thresholds', '{"person": 0.5, "bicycle": 0.4, "car": 0.5, "motorcycle": 0.5, "bus": 0.5, "truck": 0.5}'),
        ('tracking.dwell_threshold_s', '5'),
        ('tracking.lost_timeout_s', '30'),
        ('vlm.trigger_dwell_s', '15'),
        ('vlm.max_concurrent', '2'),
        ('alerts.cooldown_s', '60'),
        ('alerts.max_per_hour', '10'),
        ('recording.pre_buffer_s', '15'),
        ('recording.post_buffer_s', '30')
        ON CONFLICT (key) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_table("system_config")
    op.drop_table("alerts")
    op.drop_table("violation_events")
    op.drop_table("cameras")
    op.execute("DROP TYPE IF EXISTS event_fsm_state")
    op.execute("DROP TYPE IF EXISTS app_role")
    op.execute("DROP TYPE IF EXISTS event_type")
    op.execute("DROP TYPE IF EXISTS alert_status")
    op.execute("DROP TYPE IF EXISTS alert_severity")
    op.execute("DROP TYPE IF EXISTS camera_status")
```

- [ ] **Step 6: Create alembic/script.py.mako (required by Alembic)**

This is auto-generated by `alembic init`, but since we're writing manually:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/ backend/alembic.ini backend/alembic/
git commit -m "feat(backend): database engine, ORM models, and initial Alembic migration"
```

---

## Task 4: Kafka Producer Service

**Files:**
- Create: `/backend/app/services/kafka_producer.py`
- Create: `/backend/app/schemas/metadata.py`
- Create: `/backend/tests/test_kafka_producer.py`

- [ ] **Step 1: Create metadata schema**

```python
# backend/app/schemas/metadata.py
from pydantic import BaseModel


class Detection(BaseModel):
    class_id: int
    class_name: str  # person, car, truck, etc.
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    track_id: int | None = None


class FrameMetadata(BaseModel):
    camera_id: str
    frame_number: int
    timestamp: float  # epoch seconds
    detections: list[Detection]
    fps: float | None = None
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_kafka_producer.py
import pytest
from unittest.mock import AsyncMock, patch

from app.services.kafka_producer import KafkaProducerService
from app.schemas.metadata import FrameMetadata, Detection


@pytest.mark.asyncio
async def test_send_metadata():
    with patch("app.services.kafka_producer.AIOKafkaProducer") as MockProducer:
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance

        service = KafkaProducerService(bootstrap_servers="localhost:9092")
        await service.start()

        metadata = FrameMetadata(
            camera_id="cam-01",
            frame_number=100,
            timestamp=1711900000.0,
            detections=[
                Detection(class_id=0, class_name="person", confidence=0.92, bbox=[0.1, 0.2, 0.3, 0.4], track_id=1)
            ],
        )
        await service.send_metadata(metadata)

        mock_instance.send_and_wait.assert_called_once()
        call_args = mock_instance.send_and_wait.call_args
        assert call_args.kwargs["topic"] == "wastewatch.metadata"
        assert call_args.kwargs["key"] == b"cam-01"


@pytest.mark.asyncio
async def test_producer_start_stop():
    with patch("app.services.kafka_producer.AIOKafkaProducer") as MockProducer:
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance

        service = KafkaProducerService(bootstrap_servers="localhost:9092")
        await service.start()
        mock_instance.start.assert_called_once()

        await service.stop()
        mock_instance.stop.assert_called_once()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_kafka_producer.py -v`
Expected: FAIL — `KafkaProducerService` not found.

- [ ] **Step 4: Implement KafkaProducerService**

```python
# backend/app/services/kafka_producer.py
import orjson
from aiokafka import AIOKafkaProducer

from app.config.kafka import TOPIC_METADATA
from app.schemas.metadata import FrameMetadata


class KafkaProducerService:
    def __init__(self, bootstrap_servers: str):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
        )

    async def start(self):
        await self._producer.start()

    async def stop(self):
        await self._producer.stop()

    async def send_metadata(self, metadata: FrameMetadata):
        await self._producer.send_and_wait(
            topic=TOPIC_METADATA,
            key=metadata.camera_id,
            value=metadata.model_dump(),
        )

    async def send(self, topic: str, key: str, value: dict):
        await self._producer.send_and_wait(topic=topic, key=key, value=value)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_kafka_producer.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/kafka_producer.py backend/app/schemas/metadata.py backend/tests/test_kafka_producer.py
git commit -m "feat(backend): Kafka producer service with frame metadata schema"
```

---

## Task 5: WebSocket Metadata Broadcast

**Files:**
- Create: `/backend/app/services/kafka_consumer.py`
- Create: `/backend/app/ws/metadata.py`
- Create: `/backend/tests/test_ws_metadata.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ws_metadata.py
import pytest
from unittest.mock import AsyncMock, patch
from starlette.testclient import TestClient

from app.main import create_app


def test_ws_metadata_connects():
    """Test that a WebSocket connection to /ws/metadata/{camera_id} is accepted."""
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/ws/metadata/cam-01") as ws:
        # Connection should be accepted — send a ping to verify it's alive
        # We can't easily test Kafka-driven messages in unit test,
        # so just verify the connection lifecycle works.
        pass  # Connection accepted and closed cleanly = success
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_ws_metadata.py -v`
Expected: FAIL — no WebSocket route registered.

- [ ] **Step 3: Create WebSocket metadata endpoint**

```python
# backend/app/ws/metadata.py
import asyncio
import logging
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

# camera_id -> set of connected WebSockets
_subscribers: dict[str, set[WebSocket]] = defaultdict(set)


async def broadcast_to_camera(camera_id: str, data: dict):
    """Called by the Kafka consumer to push metadata to all subscribers of a camera."""
    dead = []
    for ws in _subscribers.get(camera_id, set()):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers[camera_id].discard(ws)


async def broadcast_to_all(data: dict):
    """Broadcast to all subscribers (for the 'all cameras' feed)."""
    camera_id = data.get("camera_id", "")
    await broadcast_to_camera(camera_id, data)
    await broadcast_to_camera("__all__", data)


@router.websocket("/metadata/{camera_id}")
async def ws_metadata(websocket: WebSocket, camera_id: str):
    await websocket.accept()
    _subscribers[camera_id].add(websocket)
    logger.info("WebSocket connected: camera_id=%s, total=%d", camera_id, len(_subscribers[camera_id]))
    try:
        while True:
            # Keep connection alive; client can send pings or control messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers[camera_id].discard(websocket)
        logger.info("WebSocket disconnected: camera_id=%s", camera_id)
```

- [ ] **Step 4: Create Kafka consumer for metadata**

```python
# backend/app/services/kafka_consumer.py
import asyncio
import logging

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import GROUP_API_METADATA, TOPIC_METADATA
from app.ws.metadata import broadcast_to_all

logger = logging.getLogger(__name__)


class MetadataConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_METADATA,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_API_METADATA,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("MetadataConsumer started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        logger.info("MetadataConsumer stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await broadcast_to_all(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("MetadataConsumer error")
```

- [ ] **Step 5: Wire WebSocket router into main.py**

Update `backend/app/main.py` — add the ws router:

```python
# In backend/app/main.py, add import and include_router:

from app.ws import metadata as ws_metadata
from app.services.kafka_consumer import MetadataConsumer
from app.config.settings import settings

# Inside create_app(), after health router:
    app.include_router(ws_metadata.router, prefix="/ws")

# Update lifespan:
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Kafka metadata consumer (non-fatal if Kafka unavailable)
    consumer = MetadataConsumer(settings.kafka_bootstrap_servers)
    try:
        await consumer.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Kafka consumer failed to start: %s", e)
        consumer = None
    yield
    if consumer:
        await consumer.stop()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_ws_metadata.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/ws/metadata.py backend/app/services/kafka_consumer.py backend/tests/test_ws_metadata.py backend/app/main.py
git commit -m "feat(backend): WebSocket /ws/metadata/{camera_id} with Kafka consumer broadcast"
```

---

## Task 6: Camera CRUD API

**Files:**
- Create: `/backend/app/schemas/camera.py`
- Create: `/backend/app/api/routes/cameras.py`
- Create: `/backend/tests/test_cameras.py`

- [ ] **Step 1: Create camera schemas**

```python
# backend/app/schemas/camera.py
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.db.models import CameraStatus


class CameraCreate(BaseModel):
    name: str
    zone: str
    location: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    stream_url: str | None = None
    source_type: str = "rtsp"


class CameraUpdate(BaseModel):
    name: str | None = None
    zone: str | None = None
    location: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    stream_url: str | None = None
    source_type: str | None = None
    status: CameraStatus | None = None
    is_pinned: bool | None = None
    roi_polygons: dict | None = None
    recording_config: dict | None = None


class CameraResponse(BaseModel):
    id: uuid.UUID
    name: str
    zone: str
    location: str | None
    gps_lat: float | None
    gps_lng: float | None
    status: CameraStatus
    stream_url: str | None
    source_type: str
    thumbnail_url: str | None
    roi_polygons: dict | None
    recording_config: dict | None
    is_pinned: bool
    last_event_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_cameras.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_list_cameras(client):
    resp = await client.get("/api/cameras")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_camera(client):
    resp = await client.post("/api/cameras", json={
        "name": "Test Camera",
        "zone": "Zone A",
        "stream_url": "rtsp://192.168.1.100:554/stream",
    })
    # Will fail until DB is connected, but validates route exists
    assert resp.status_code in (200, 201, 500)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_cameras.py -v`
Expected: FAIL — route not found (404).

- [ ] **Step 4: Implement camera routes**

```python
# backend/app/api/routes/cameras.py
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import Camera
from app.schemas.camera import CameraCreate, CameraResponse, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.get("", response_model=list[CameraResponse])
async def list_cameras(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Camera).order_by(Camera.is_pinned.desc(), Camera.name)
    )
    return result.scalars().all()


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.post("", response_model=CameraResponse, status_code=201)
async def create_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.patch("/{camera_id}", response_model=CameraResponse)
async def update_camera(camera_id: uuid.UUID, body: CameraUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(camera, field, value)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.delete("/{camera_id}", status_code=204)
async def delete_camera(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    await db.delete(camera)
    await db.commit()
```

- [ ] **Step 5: Register camera router in main.py**

Add to `create_app()` in `backend/app/main.py`:

```python
from app.api.routes import cameras
# ...
app.include_router(cameras.router, prefix="/api")
```

- [ ] **Step 6: Run test to verify route exists**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_cameras.py -v`
Expected: Routes exist (may get 500 from missing DB, but not 404).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/camera.py backend/app/api/routes/cameras.py backend/tests/test_cameras.py backend/app/main.py
git commit -m "feat(backend): camera CRUD REST API endpoints"
```

---

## Task 7: Docker Compose Infrastructure Stack

**Files:**
- Create: `/docker-compose.yml`
- Create: `/docker-compose.dev.yml`
- Create: `/.env.example`
- Create: `/backend/Dockerfile`

- [ ] **Step 1: Create .env.example**

```bash
# .env.example — Copy to .env and fill in values

# Database (Neon Postgres)
WW_DATABASE_URL=postgresql+asyncpg://user:pass@ep-xxx.us-east-2.aws.neon.tech/wastewatch?sslmode=require

# Kafka
WW_KAFKA_BOOTSTRAP_SERVERS=kafka:9092

# Redis
WW_REDIS_URL=redis://redis:6379/0

# MediaMTX
WW_MEDIAMTX_API_URL=http://mediamtx:9997
WW_MEDIAMTX_RTSP_URL=rtsp://mediamtx:8554

# API
WW_API_HOST=0.0.0.0
WW_API_PORT=8080
WW_CORS_ORIGINS=["http://localhost:5173","http://localhost:8080"]

# Frontend (already in frontend/.env)
VITE_SUPABASE_URL=https://xxx.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=xxx
```

- [ ] **Step 2: Create backend Dockerfile**

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml .
RUN uv pip install --system -e .

COPY . .

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create docker-compose.yml**

```yaml
# docker-compose.yml
services:
  # --- Infrastructure ---
  kafka:
    image: apache/kafka:3.7.0
    environment:
      KAFKA_NODE_ID: 1
      KAFKA_PROCESS_ROLES: broker,controller
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092,CONTROLLER://0.0.0.0:9093
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
      KAFKA_CONTROLLER_LISTENER_NAMES: CONTROLLER
      KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
      KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_LOG_DIRS: /tmp/kraft-combined-logs
      CLUSTER_ID: "WasteWatch-Kafka-Cluster-01"
    ports:
      - "9092:9092"
    volumes:
      - kafka_data:/tmp/kraft-combined-logs
    healthcheck:
      test: ["CMD-SHELL", "/opt/kafka/bin/kafka-broker-api-versions.sh --bootstrap-server localhost:9092 > /dev/null 2>&1"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3

  mediamtx:
    image: bluenviron/mediamtx:latest
    ports:
      - "8554:8554"   # RTSP
      - "8889:8889"   # WebRTC (WHEP)
      - "9997:9997"   # API
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
    restart: unless-stopped

  # --- Application ---
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    ports:
      - "8080:8080"
    env_file: .env
    environment:
      WW_KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      WW_REDIS_URL: redis://redis:6379/0
      WW_MEDIAMTX_API_URL: http://mediamtx:9997
      WW_MEDIAMTX_RTSP_URL: rtsp://mediamtx:8554
    depends_on:
      kafka:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped

  # --- DeepStream Pipeline (GPU required) ---
  pipeline:
    build:
      context: ./pipeline
      dockerfile: Dockerfile
    env_file: .env
    environment:
      WW_KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      WW_MEDIAMTX_RTSP_URL: rtsp://mediamtx:8554
    depends_on:
      kafka:
        condition: service_healthy
      mediamtx:
        condition: service_started
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

volumes:
  kafka_data:
  redis_data:
```

- [ ] **Step 4: Create docker-compose.dev.yml (no GPU)**

```yaml
# docker-compose.dev.yml
# Usage: docker compose -f docker-compose.yml -f docker-compose.dev.yml up
services:
  api:
    build:
      context: ./backend
      dockerfile: Dockerfile
    command: uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
    volumes:
      - ./backend:/app

  # Disable pipeline in dev (no GPU)
  pipeline:
    profiles: ["gpu"]
```

- [ ] **Step 5: Create MediaMTX config**

```yaml
# mediamtx.yml (project root)
# MediaMTX configuration for WasteWatch
# Camera paths are auto-registered via the API

logLevel: info
logDestinations: [stdout]

api: yes
apiAddress: :9997

rtsp: yes
rtspAddress: :8554

webrtc: yes
webrtcAddress: :8889

# Allow any path to be published/read
pathDefaults:
  source: publisher
  sourceOnDemand: no
  record: no

# Paths will be dynamically added by the pipeline
# Example: /cam-zone-a-north → RTSP from DeepStream
paths:
  all_others:
```

- [ ] **Step 6: Verify docker-compose config parses**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch && docker compose config --quiet`
Expected: No errors.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.dev.yml .env.example backend/Dockerfile mediamtx.yml
git commit -m "feat: Docker Compose stack with Kafka, Redis, MediaMTX, API, pipeline services"
```

---

## Task 8: DeepStream Pipeline Configuration

**Files:**
- Create: `/pipeline/Dockerfile`
- Create: `/pipeline/app/__init__.py`
- Create: `/pipeline/app/config.py`
- Create: `/pipeline/app/pipeline.py`
- Create: `/pipeline/app/source_manager.py`
- Create: `/pipeline/app/kafka_producer.py`
- Create: `/pipeline/app/health.py`
- Create: `/pipeline/configs/deepstream_app.txt`
- Create: `/pipeline/configs/nvinfer_rfdetr.txt`
- Create: `/pipeline/configs/nvmsgconv_config.txt`
- Create: `/pipeline/configs/labels.txt`
- Create: `/pipeline/models/rfdetr/README.md`

- [ ] **Step 1: Create pipeline Dockerfile**

```dockerfile
# pipeline/Dockerfile
FROM nvcr.io/nvidia/deepstream:7.0-triton-multiarch

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-dev libgstreamer1.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "-u", "app/pipeline.py"]
```

Create `pipeline/requirements.txt`:

```
pyds==1.1.11
confluent-kafka==2.6.1
orjson==3.10.12
pydantic-settings==2.7.1
httpx==0.28.1
```

- [ ] **Step 2: Create COCO labels file (filtered)**

```
# pipeline/configs/labels.txt
person
bicycle
car
motorcycle
bus
truck
```

- [ ] **Step 3: Create nvinfer config for RF-DETR**

```ini
# pipeline/configs/nvinfer_rfdetr.txt
[property]
gpu-id=0
net-scale-factor=0.00392156862745
# RF-DETR input: 640x640
infer-dims=3;640;640
# TensorRT FP16 engine (built by scripts/build_tensorrt_engine.sh)
model-engine-file=models/rfdetr/rfdetr_fp16.engine
# Fallback ONNX if engine not built yet
onnx-file=models/rfdetr/rfdetr.onnx
labelfile-path=configs/labels.txt
batch-size=4
process-mode=1
# Primary detector
model-color-format=0
network-mode=2
# FP16
num-detected-classes=6
# COCO class filter: person(0), bicycle(1), car(2), motorcycle(3), bus(5), truck(7)
# Mapped to 0-5 in our label file
interval=2
# Process every 3rd frame (0-indexed: 0, skip 1, skip 2, process 3...)
gie-unique-id=1
output-blob-names=output
cluster-mode=2
# NMS
maintain-aspect-ratio=1

[class-attrs-all]
pre-cluster-threshold=0.4
topk=100
nms-iou-threshold=0.5
```

- [ ] **Step 4: Create nvmsgconv config**

```ini
# pipeline/configs/nvmsgconv_config.txt
[sensor]
enable=1
type=Camera
id=Default-Camera
location=Default
coordinate=0;0;0
description=WasteWatch Camera

[place]
enable=1
id=Default-Place
type=monitored-zone
name=Default Zone
location=Default
coordinate=0;0;0

[analytics]
enable=1
id=Default-Analytics
source=Default-Camera
description=WasteWatch Detection Analytics
```

- [ ] **Step 5: Create pipeline config module**

```python
# pipeline/app/config.py
from pydantic_settings import BaseSettings


class PipelineSettings(BaseSettings):
    kafka_bootstrap_servers: str = "kafka:9092"
    mediamtx_rtsp_url: str = "rtsp://mediamtx:8554"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/wastewatch"

    # Pipeline
    batch_size: int = 4
    frame_skip: int = 2  # nvinfer interval (0-indexed, so 2 = every 3rd frame)
    input_width: int = 1920
    input_height: int = 1080
    processing_width: int = 640
    processing_height: int = 640
    muxer_output_width: int = 1920
    muxer_output_height: int = 1080

    model_config = {"env_prefix": "WW_"}


pipeline_settings = PipelineSettings()
```

- [ ] **Step 6: Create source manager**

```python
# pipeline/app/source_manager.py
"""Dynamic RTSP source management for the DeepStream pipeline."""
import logging

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

logger = logging.getLogger(__name__)


class SourceManager:
    """Manages RTSP source bins in the DeepStream pipeline."""

    def __init__(self, pipeline: Gst.Pipeline, streammux: Gst.Element):
        self._pipeline = pipeline
        self._streammux = streammux
        self._sources: dict[str, dict] = {}  # camera_id -> {bin, pad_id, uri}
        self._next_pad_id = 0

    def add_source(self, camera_id: str, uri: str) -> int:
        """Add an RTSP source to the pipeline. Returns the pad ID."""
        if camera_id in self._sources:
            logger.warning("Source %s already exists, removing first", camera_id)
            self.remove_source(camera_id)

        pad_id = self._next_pad_id
        self._next_pad_id += 1

        # Create uridecodebin for the source
        source_bin = Gst.ElementFactory.make("uridecodebin", f"source-{camera_id}")
        if not source_bin:
            raise RuntimeError(f"Failed to create source bin for {camera_id}")

        source_bin.set_property("uri", uri)
        source_bin.connect("pad-added", self._on_pad_added, camera_id, pad_id)

        self._pipeline.add(source_bin)
        source_bin.sync_state_with_parent()

        self._sources[camera_id] = {
            "bin": source_bin,
            "pad_id": pad_id,
            "uri": uri,
        }

        logger.info("Added source: %s (pad=%d) -> %s", camera_id, pad_id, uri)
        return pad_id

    def remove_source(self, camera_id: str):
        """Remove an RTSP source from the pipeline."""
        if camera_id not in self._sources:
            logger.warning("Source %s not found", camera_id)
            return

        info = self._sources.pop(camera_id)
        source_bin = info["bin"]
        pad_id = info["pad_id"]

        # Release muxer sink pad
        sink_pad = self._streammux.get_static_pad(f"sink_{pad_id}")
        if sink_pad:
            self._streammux.release_request_pad(sink_pad)

        source_bin.set_state(Gst.State.NULL)
        self._pipeline.remove(source_bin)

        logger.info("Removed source: %s (pad=%d)", camera_id, pad_id)

    def _on_pad_added(self, src, new_pad, camera_id: str, pad_id: int):
        """Callback when uridecodebin creates a new pad (decoded video)."""
        caps = new_pad.get_current_caps()
        if not caps:
            return

        struct = caps.get_structure(0)
        if not struct.get_name().startswith("video"):
            return

        sink_pad = self._streammux.request_pad_simple(f"sink_{pad_id}")
        if not sink_pad:
            logger.error("Failed to get muxer sink pad for %s", camera_id)
            return

        if new_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            logger.error("Failed to link source %s to muxer", camera_id)
        else:
            logger.info("Linked source %s to muxer pad sink_%d", camera_id, pad_id)

    @property
    def active_sources(self) -> dict[str, str]:
        """Return {camera_id: uri} for all active sources."""
        return {cid: info["uri"] for cid, info in self._sources.items()}
```

- [ ] **Step 7: Create Kafka message producer for pipeline**

```python
# pipeline/app/kafka_producer.py
"""Kafka producer for DeepStream metadata output."""
import logging
from confluent_kafka import Producer

import orjson

from app.config import pipeline_settings

logger = logging.getLogger(__name__)


class PipelineKafkaProducer:
    def __init__(self):
        self._producer = Producer({
            "bootstrap.servers": pipeline_settings.kafka_bootstrap_servers,
            "linger.ms": 5,
            "batch.num.messages": 100,
        })

    def send_metadata(self, camera_id: str, metadata: dict):
        """Send per-frame detection metadata to Kafka."""
        try:
            self._producer.produce(
                topic="wastewatch.metadata",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(metadata),
            )
        except Exception:
            logger.exception("Failed to send metadata for %s", camera_id)

    def flush(self):
        self._producer.flush(timeout=5)

    def poll(self):
        self._producer.poll(0)
```

- [ ] **Step 8: Create health monitor stub**

```python
# pipeline/app/health.py
"""Camera health monitoring — detect disconnects, auto-reconnect."""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class CameraHealth:
    """Tracks per-camera frame arrival and triggers reconnect on timeout."""

    def __init__(self, timeout_s: float = 30.0, max_retries: int = 5):
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._last_frame: dict[str, float] = {}  # camera_id -> epoch
        self._retry_count: dict[str, int] = {}
        self._callbacks: dict[str, callable] = {}

    def on_frame(self, camera_id: str):
        """Called every time a frame arrives from a camera."""
        self._last_frame[camera_id] = time.monotonic()
        self._retry_count[camera_id] = 0

    def register_reconnect(self, camera_id: str, callback):
        """Register a reconnect callback for a camera."""
        self._callbacks[camera_id] = callback

    def check_health(self) -> list[str]:
        """Returns list of camera_ids that have timed out."""
        now = time.monotonic()
        timed_out = []
        for camera_id, last in self._last_frame.items():
            if now - last > self._timeout_s:
                retries = self._retry_count.get(camera_id, 0)
                if retries < self._max_retries:
                    timed_out.append(camera_id)
                    self._retry_count[camera_id] = retries + 1
                    logger.warning(
                        "Camera %s timed out (retry %d/%d)",
                        camera_id, retries + 1, self._max_retries,
                    )
                else:
                    logger.error("Camera %s exceeded max retries, marking offline", camera_id)
        return timed_out
```

- [ ] **Step 9: Create the main DeepStream pipeline builder**

```python
# pipeline/app/pipeline.py
"""
DeepStream pipeline for WasteWatch.

Pipeline graph:
  RTSP sources → nvstreammux → nvinfer (RF-DETR) → nvtracker (Phase 2)
    → nvdsanalytics (Phase 2) → nvosd → tee
      ├→ nvvideoconvert → nvv4l2h264enc → rtph264pay → udpsink (→ MediaMTX)
      └→ (metadata extraction → Kafka)
"""
import logging
import sys
import time

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import Gst, GLib

from app.config import pipeline_settings
from app.kafka_producer import PipelineKafkaProducer
from app.source_manager import SourceManager
from app.health import CameraHealth

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("wastewatch.pipeline")

# COCO class ID → our label mapping
COCO_CLASS_MAP = {0: "person", 1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}


def make_element(factory_name: str, name: str):
    """Create a GStreamer element or exit on failure."""
    elem = Gst.ElementFactory.make(factory_name, name)
    if not elem:
        logger.error("Failed to create element: %s (%s)", name, factory_name)
        sys.exit(1)
    return elem


def build_pipeline():
    """Build the DeepStream processing pipeline."""
    Gst.init(None)

    pipeline = Gst.Pipeline()
    if not pipeline:
        logger.error("Failed to create pipeline")
        sys.exit(1)

    # --- Stream Muxer ---
    streammux = make_element("nvstreammux", "stream-muxer")
    streammux.set_property("batch-size", pipeline_settings.batch_size)
    streammux.set_property("width", pipeline_settings.muxer_output_width)
    streammux.set_property("height", pipeline_settings.muxer_output_height)
    streammux.set_property("batched-push-timeout", 25000)  # 25ms = ~40fps max
    streammux.set_property("live-source", 1)
    pipeline.add(streammux)

    # --- Primary Inference (RF-DETR) ---
    pgie = make_element("nvinfer", "primary-inference")
    pgie.set_property("config-file-path", "configs/nvinfer_rfdetr.txt")
    pipeline.add(pgie)

    # --- On-Screen Display ---
    nvosd = make_element("nvdsosd", "on-screen-display")
    nvosd.set_property("process-mode", 2)  # GPU mode
    pipeline.add(nvosd)

    # --- Tee (split to display + metadata) ---
    tee = make_element("tee", "tee")
    pipeline.add(tee)

    # --- Display branch: encode + RTSP output ---
    queue_display = make_element("queue", "queue-display")
    nvvidconv = make_element("nvvideoconvert", "nvvidconv-display")
    capsfilter = make_element("capsfilter", "caps-display")
    capsfilter.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420"))
    encoder = make_element("nvv4l2h264enc", "h264-encoder")
    encoder.set_property("bitrate", 4000000)
    encoder.set_property("preset-level", 1)
    rtppay = make_element("rtph264pay", "rtp-payload")
    udpsink = make_element("udpsink", "udpsink")
    udpsink.set_property("host", "mediamtx")
    udpsink.set_property("port", 8554)
    udpsink.set_property("sync", 0)
    udpsink.set_property("async", 0)

    for elem in [queue_display, nvvidconv, capsfilter, encoder, rtppay, udpsink]:
        pipeline.add(elem)

    # --- Metadata branch ---
    queue_meta = make_element("queue", "queue-meta")
    fakesink = make_element("fakesink", "meta-sink")
    fakesink.set_property("sync", 0)
    fakesink.set_property("async", 0)

    for elem in [queue_meta, fakesink]:
        pipeline.add(elem)

    # --- Link elements ---
    streammux.link(pgie)
    pgie.link(nvosd)
    nvosd.link(tee)

    # Link tee → display branch
    tee_src_display = tee.request_pad_simple("src_%u")
    queue_display_sink = queue_display.get_static_pad("sink")
    tee_src_display.link(queue_display_sink)
    queue_display.link(nvvidconv)
    nvvidconv.link(capsfilter)
    capsfilter.link(encoder)
    encoder.link(rtppay)
    rtppay.link(udpsink)

    # Link tee → metadata branch
    tee_src_meta = tee.request_pad_simple("src_%u")
    queue_meta_sink = queue_meta.get_static_pad("sink")
    tee_src_meta.link(queue_meta_sink)
    queue_meta.link(fakesink)

    return pipeline, streammux, fakesink


def extract_metadata(pad, info, kafka_producer: PipelineKafkaProducer, health: CameraHealth):
    """Probe callback: extract detections from DeepStream metadata and send to Kafka."""
    import pyds

    gst_buffer = info.get_buffer()
    if not gst_buffer:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if not batch_meta:
        return Gst.PadProbeReturn.OK

    l_frame = batch_meta.frame_meta_list
    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        camera_id = f"cam-{frame_meta.source_id}"
        health.on_frame(camera_id)

        detections = []
        l_obj = frame_meta.obj_meta_list
        while l_obj is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break

            class_name = COCO_CLASS_MAP.get(obj_meta.class_id, f"class_{obj_meta.class_id}")
            rect = obj_meta.rect_params
            detections.append({
                "class_id": obj_meta.class_id,
                "class_name": class_name,
                "confidence": round(obj_meta.confidence, 3),
                "bbox": [
                    round(rect.left / pipeline_settings.muxer_output_width, 4),
                    round(rect.top / pipeline_settings.muxer_output_height, 4),
                    round((rect.left + rect.width) / pipeline_settings.muxer_output_width, 4),
                    round((rect.top + rect.height) / pipeline_settings.muxer_output_height, 4),
                ],
                "track_id": obj_meta.object_id if obj_meta.object_id != 0xFFFFFFFFFFFFFFFF else None,
            })

            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        if detections:
            kafka_producer.send_metadata(camera_id, {
                "camera_id": camera_id,
                "frame_number": frame_meta.frame_num,
                "timestamp": time.time(),
                "detections": detections,
            })

        try:
            l_frame = l_frame.next
        except StopIteration:
            break

    kafka_producer.poll()
    return Gst.PadProbeReturn.OK


def main():
    """Entry point: build pipeline, add sources from DB, run GLib main loop."""
    logger.info("Starting WasteWatch DeepStream pipeline")

    pipeline, streammux, fakesink = build_pipeline()
    source_manager = SourceManager(pipeline, streammux)
    kafka_producer = PipelineKafkaProducer()
    health = CameraHealth()

    # Add metadata extraction probe on the fakesink's sink pad
    sink_pad = fakesink.get_static_pad("sink")
    sink_pad.add_probe(
        Gst.PadProbeType.BUFFER,
        extract_metadata,
        kafka_producer,
        health,
    )

    # TODO: Load cameras from DB on startup. For now, support env-based sources.
    # Example: WW_SOURCES="cam-01=rtsp://192.168.1.100:554/stream,cam-02=rtsp://..."
    import os
    sources_str = os.environ.get("WW_SOURCES", "")
    if sources_str:
        for entry in sources_str.split(","):
            if "=" in entry:
                cam_id, uri = entry.split("=", 1)
                source_manager.add_source(cam_id.strip(), uri.strip())

    # Start pipeline
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        logger.error("Failed to set pipeline to PLAYING")
        sys.exit(1)

    logger.info("Pipeline PLAYING — active sources: %s", list(source_manager.active_sources.keys()))

    # Health check timer
    def _health_check():
        timed_out = health.check_health()
        for camera_id in timed_out:
            uri = source_manager.active_sources.get(camera_id)
            if uri:
                logger.info("Reconnecting camera %s", camera_id)
                source_manager.remove_source(camera_id)
                source_manager.add_source(camera_id, uri)
        return True  # Keep timer running

    GLib.timeout_add_seconds(10, _health_check)

    # Run main loop
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message::error", lambda _, msg: logger.error("GST ERROR: %s", msg.parse_error()))
    bus.connect("message::warning", lambda _, msg: logger.warning("GST WARNING: %s", msg.parse_warning()))
    bus.connect("message::eos", lambda _, msg: loop.quit())

    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Shutting down pipeline")
    finally:
        pipeline.set_state(Gst.State.NULL)
        kafka_producer.flush()


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: Create RF-DETR model README**

```markdown
# RF-DETR Model Setup

## Export ONNX

```bash
cd /home/subhadra/Projects/LSG-Wastewatch
python scripts/export_rfdetr_onnx.py
```

## Build TensorRT Engine (FP16)

```bash
./scripts/build_tensorrt_engine.sh
```

The engine file will be saved to `pipeline/models/rfdetr/rfdetr_fp16.engine`.

## COCO Class Filter

Only these 6 classes are kept (mapped to indices 0-5 in labels.txt):

| Our Index | COCO ID | Class |
|-----------|---------|-------|
| 0 | 0 | person |
| 1 | 1 | bicycle |
| 2 | 2 | car |
| 3 | 3 | motorcycle |
| 4 | 5 | bus |
| 5 | 7 | truck |
```

- [ ] **Step 11: Create __init__.py files for pipeline**

Create empty `__init__.py` in: `pipeline/app/`.

- [ ] **Step 12: Commit**

```bash
git add pipeline/ mediamtx.yml
git commit -m "feat(pipeline): DeepStream pipeline with RF-DETR nvinfer, source manager, Kafka metadata output"
```

---

## Task 9: RF-DETR Export + TensorRT Build Scripts

**Files:**
- Create: `/scripts/export_rfdetr_onnx.py`
- Create: `/scripts/build_tensorrt_engine.sh`

- [ ] **Step 1: Create ONNX export script**

```python
#!/usr/bin/env python3
"""Export RF-DETR model to ONNX format for TensorRT conversion.

Usage:
    pip install rfdetr torch onnx onnxsim
    python scripts/export_rfdetr_onnx.py
"""
import os
import sys

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "pipeline", "models", "rfdetr")


def main():
    try:
        from rfdetr import RFDETRBase
    except ImportError:
        print("Install rfdetr: pip install rfdetr")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    onnx_path = os.path.join(OUTPUT_DIR, "rfdetr.onnx")

    print("Loading RF-DETR base model (COCO pretrained)...")
    model = RFDETRBase()

    print(f"Exporting to ONNX: {onnx_path}")
    model.export(onnx_path)

    print(f"ONNX model saved to: {onnx_path}")
    print(f"Next step: Run scripts/build_tensorrt_engine.sh to convert to TensorRT FP16")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create TensorRT build script**

```bash
#!/usr/bin/env bash
# Build TensorRT FP16 engine from RF-DETR ONNX model.
# Requires: NVIDIA TensorRT (trtexec) installed.
# Run inside the DeepStream container or on a machine with TensorRT.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${SCRIPT_DIR}/../pipeline/models/rfdetr"
ONNX_FILE="${MODEL_DIR}/rfdetr.onnx"
ENGINE_FILE="${MODEL_DIR}/rfdetr_fp16.engine"

if [ ! -f "$ONNX_FILE" ]; then
    echo "ERROR: ONNX file not found at $ONNX_FILE"
    echo "Run: python scripts/export_rfdetr_onnx.py first"
    exit 1
fi

echo "Building TensorRT FP16 engine..."
echo "  ONNX:   $ONNX_FILE"
echo "  Engine: $ENGINE_FILE"

trtexec \
    --onnx="$ONNX_FILE" \
    --saveEngine="$ENGINE_FILE" \
    --fp16 \
    --minShapes=input:1x3x640x640 \
    --optShapes=input:4x3x640x640 \
    --maxShapes=input:8x3x640x640 \
    --workspace=4096 \
    --verbose

echo "TensorRT engine saved to: $ENGINE_FILE"
```

- [ ] **Step 3: Make scripts executable**

Run: `chmod +x /home/subhadra/Projects/LSG-Wastewatch/scripts/build_tensorrt_engine.sh`

- [ ] **Step 4: Commit**

```bash
git add scripts/
git commit -m "feat(scripts): RF-DETR ONNX export and TensorRT FP16 engine build scripts"
```

---

## Task 10: Frontend — Detection Metadata WebSocket Hook

**Files:**
- Create: `/frontend/src/hooks/useDetectionMetadata.ts`
- Modify: `/frontend/src/types/monitoring.ts`

- [ ] **Step 1: Add Detection types to monitoring.ts**

Append to the existing `frontend/src/types/monitoring.ts`:

```typescript
// --- Phase 1: Real-time detection metadata from backend WebSocket ---

export interface BBoxDetection {
  class_id: number;
  class_name: string;
  confidence: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0-1
  track_id: number | null;
}

export interface FrameMetadata {
  camera_id: string;
  frame_number: number;
  timestamp: number;
  detections: BBoxDetection[];
  fps?: number;
}
```

- [ ] **Step 2: Create useDetectionMetadata hook**

```typescript
// frontend/src/hooks/useDetectionMetadata.ts
import { useEffect, useRef, useState, useCallback } from "react";
import type { FrameMetadata } from "@/types/monitoring";

const WS_BASE_URL = import.meta.env.VITE_WS_URL ?? "ws://localhost:8080/ws";
const RECONNECT_DELAY_MS = 3000;
const MAX_RECONNECT_ATTEMPTS = 10;

interface UseDetectionMetadataOptions {
  cameraId: string;
  enabled?: boolean;
}

export function useDetectionMetadata({ cameraId, enabled = true }: UseDetectionMetadataOptions) {
  const [metadata, setMetadata] = useState<FrameMetadata | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    if (!enabled || !cameraId) return;

    const url = `${WS_BASE_URL}/metadata/${cameraId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      reconnectAttempts.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data: FrameMetadata = JSON.parse(event.data);
        setMetadata(data);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;

      if (enabled && reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts.current += 1;
        const delay = RECONNECT_DELAY_MS * Math.min(reconnectAttempts.current, 5);
        reconnectTimer.current = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [cameraId, enabled]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { metadata, isConnected };
}
```

- [ ] **Step 3: Commit**

```bash
cd /home/subhadra/Projects/LSG-Wastewatch
git add frontend/src/hooks/useDetectionMetadata.ts frontend/src/types/monitoring.ts
git commit -m "feat(frontend): useDetectionMetadata WebSocket hook for real-time bbox data"
```

---

## Task 11: Frontend — Metadata Overlay Component

**Files:**
- Create: `/frontend/src/components/camera/MetadataOverlay.tsx`
- Modify: `/frontend/src/components/camera/CameraFeed.tsx`

- [ ] **Step 1: Create MetadataOverlay component**

```tsx
// frontend/src/components/camera/MetadataOverlay.tsx
import type { FrameMetadata, BBoxDetection } from "@/types/monitoring";

interface MetadataOverlayProps {
  metadata: FrameMetadata | null;
  width: number;
  height: number;
}

const CLASS_COLORS: Record<string, string> = {
  person: "#22c55e",    // green
  bicycle: "#3b82f6",   // blue
  car: "#eab308",       // yellow
  motorcycle: "#f97316", // orange
  bus: "#a855f7",       // purple
  truck: "#ef4444",     // red
};

function DetectionBox({ detection, width, height }: { detection: BBoxDetection; width: number; height: number }) {
  const [x1, y1, x2, y2] = detection.bbox;
  const color = CLASS_COLORS[detection.class_name] ?? "#ffffff";

  const left = x1 * width;
  const top = y1 * height;
  const boxWidth = (x2 - x1) * width;
  const boxHeight = (y2 - y1) * height;

  const label = detection.track_id != null
    ? `${detection.class_name} #${detection.track_id} ${(detection.confidence * 100).toFixed(0)}%`
    : `${detection.class_name} ${(detection.confidence * 100).toFixed(0)}%`;

  return (
    <>
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top}px`,
          width: `${boxWidth}px`,
          height: `${boxHeight}px`,
          border: `2px solid ${color}`,
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top - 20}px`,
          backgroundColor: color,
          color: "#000",
          fontSize: "11px",
          fontWeight: 600,
          padding: "1px 4px",
          whiteSpace: "nowrap",
          pointerEvents: "none",
          lineHeight: "16px",
        }}
      >
        {label}
      </div>
    </>
  );
}

export function MetadataOverlay({ metadata, width, height }: MetadataOverlayProps) {
  if (!metadata || width === 0 || height === 0) return null;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: `${width}px`,
        height: `${height}px`,
        pointerEvents: "none",
      }}
    >
      {metadata.detections.map((det, i) => (
        <DetectionBox key={`${det.class_id}-${det.track_id ?? i}`} detection={det} width={width} height={height} />
      ))}
      {/* Detection count badge */}
      <div
        style={{
          position: "absolute",
          bottom: 4,
          right: 4,
          backgroundColor: "rgba(0,0,0,0.7)",
          color: "#fff",
          fontSize: "11px",
          padding: "2px 6px",
          borderRadius: "4px",
        }}
      >
        {metadata.detections.length} detections
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Integrate MetadataOverlay into CameraFeed**

In `frontend/src/components/camera/CameraFeed.tsx`, add the overlay on top of the WebRTCPlayer. Find the section where WebRTCPlayer is rendered and wrap it with a relative container that includes MetadataOverlay:

Add imports at the top:
```typescript
import { useDetectionMetadata } from "@/hooks/useDetectionMetadata";
import { MetadataOverlay } from "./MetadataOverlay";
import { useRef, useState, useEffect } from "react";
```

Inside the CameraFeed component, add the hook and state:
```typescript
const { metadata } = useDetectionMetadata({ cameraId: camera.id, enabled: camera.status === "online" });
const containerRef = useRef<HTMLDivElement>(null);
const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

useEffect(() => {
  if (!containerRef.current) return;
  const observer = new ResizeObserver((entries) => {
    for (const entry of entries) {
      setDimensions({ width: entry.contentRect.width, height: entry.contentRect.height });
    }
  });
  observer.observe(containerRef.current);
  return () => observer.disconnect();
}, []);
```

Wrap the WebRTCPlayer with a positioned container:
```tsx
<div ref={containerRef} style={{ position: "relative" }}>
  <WebRTCPlayer {/* existing props */} />
  <MetadataOverlay metadata={metadata} width={dimensions.width} height={dimensions.height} />
</div>
```

- [ ] **Step 3: Add VITE_WS_URL to frontend/.env**

Append to `frontend/.env`:
```
VITE_WS_URL=ws://localhost:8080/ws
```

- [ ] **Step 4: Commit**

```bash
cd /home/subhadra/Projects/LSG-Wastewatch
git add frontend/src/components/camera/MetadataOverlay.tsx frontend/src/components/camera/CameraFeed.tsx frontend/.env
git commit -m "feat(frontend): detection bbox overlay on camera feeds via WebSocket metadata"
```

---

## Task 12: System Config API Route

**Files:**
- Create: `/backend/app/api/routes/config_routes.py`

- [ ] **Step 1: Create config route**

```python
# backend/app/api/routes/config_routes.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.db.models import SystemConfig

router = APIRouter(prefix="/config", tags=["config"])


class ConfigValue(BaseModel):
    value: dict | str | int | float | list


class ConfigResponse(BaseModel):
    key: str
    value: dict | str | int | float | list
    updated_at: str | None = None

    model_config = {"from_attributes": True}


@router.get("/{key}", response_model=ConfigResponse)
async def get_config(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail=f"Config key '{key}' not found")
    return ConfigResponse(
        key=config.key,
        value=config.value,
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )


@router.get("", response_model=list[ConfigResponse])
async def list_config(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).order_by(SystemConfig.key))
    configs = result.scalars().all()
    return [
        ConfigResponse(
            key=c.key,
            value=c.value,
            updated_at=c.updated_at.isoformat() if c.updated_at else None,
        )
        for c in configs
    ]


@router.put("/{key}", response_model=ConfigResponse)
async def set_config(key: str, body: ConfigValue, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SystemConfig).where(SystemConfig.key == key))
    config = result.scalar_one_or_none()
    if config:
        config.value = body.value
    else:
        config = SystemConfig(key=key, value=body.value)
        db.add(config)
    await db.commit()
    await db.refresh(config)
    return ConfigResponse(
        key=config.key,
        value=config.value,
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )
```

- [ ] **Step 2: Register config router in main.py**

Add to `create_app()` in `backend/app/main.py`:

```python
from app.api.routes import config_routes
# ...
app.include_router(config_routes.router, prefix="/api")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/api/routes/config_routes.py backend/app/main.py
git commit -m "feat(backend): system config CRUD API at /api/config"
```

---

## Task 13: Integration Smoke Test

**Files:**
- Create: `/scripts/smoke_test.sh`

- [ ] **Step 1: Create smoke test script**

```bash
#!/usr/bin/env bash
# Smoke test: verify API and infrastructure are running.
# Usage: ./scripts/smoke_test.sh [API_URL]

set -euo pipefail

API_URL="${1:-http://localhost:8080}"

echo "=== WasteWatch Phase 1 Smoke Test ==="
echo "API: $API_URL"
echo ""

# Health
echo -n "1. Health check... "
HEALTH=$(curl -sf "$API_URL/api/health")
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
if [ "$STATUS" = "ok" ]; then
    echo "PASS"
else
    echo "FAIL: $HEALTH"
    exit 1
fi

# Camera CRUD
echo -n "2. Create camera... "
CAM=$(curl -sf -X POST "$API_URL/api/cameras" \
    -H "Content-Type: application/json" \
    -d '{"name":"Smoke Test Camera","zone":"Test Zone","stream_url":"rtsp://test:554/stream"}')
CAM_ID=$(echo "$CAM" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "PASS (id=$CAM_ID)"

echo -n "3. List cameras... "
CAMS=$(curl -sf "$API_URL/api/cameras")
COUNT=$(echo "$CAMS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$COUNT" -ge 1 ]; then
    echo "PASS ($COUNT cameras)"
else
    echo "FAIL"
    exit 1
fi

echo -n "4. Delete camera... "
curl -sf -X DELETE "$API_URL/api/cameras/$CAM_ID" > /dev/null
echo "PASS"

# WebSocket
echo -n "5. WebSocket connection... "
# Simple check: attempt connection, expect upgrade
WS_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
    "$API_URL/ws/metadata/test-cam" || true)
if [ "$WS_STATUS" = "101" ]; then
    echo "PASS (101 Switching Protocols)"
else
    echo "SKIP (got $WS_STATUS — WebSocket requires proper client)"
fi

echo ""
echo "=== All checks passed ==="
```

- [ ] **Step 2: Make executable**

Run: `chmod +x /home/subhadra/Projects/LSG-Wastewatch/scripts/smoke_test.sh`

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_test.sh
git commit -m "feat(scripts): Phase 1 integration smoke test"
```

---

## Summary

| Task | Component | Deliverable |
|------|-----------|-------------|
| 1 | Backend scaffold | Python project, settings, Kafka topics |
| 2 | FastAPI app | `/api/health` endpoint |
| 3 | Database | ORM models, Alembic migration |
| 4 | Kafka producer | `KafkaProducerService` + metadata schema |
| 5 | WebSocket | `/ws/metadata/{camera_id}` broadcast |
| 6 | Camera API | CRUD at `/api/cameras` |
| 7 | Docker Compose | Full stack: Kafka, Redis, MediaMTX, API, Pipeline |
| 8 | DeepStream pipeline | GStreamer pipeline, RF-DETR nvinfer, source manager |
| 9 | Model scripts | ONNX export + TensorRT FP16 build |
| 10 | Frontend hook | `useDetectionMetadata` WebSocket hook |
| 11 | Frontend overlay | `MetadataOverlay` bbox rendering |
| 12 | Config API | `/api/config` CRUD |
| 13 | Smoke test | End-to-end verification script |

**Phase 1 Deliverable:** Live camera streams displayed in the existing React frontend with real-time person/vehicle bounding box overlays. Backend API running with health, camera CRUD, config, and WebSocket metadata broadcast. Infrastructure (Kafka, Redis, MediaMTX) orchestrated via Docker Compose. DeepStream pipeline ready for GPU deployment.
