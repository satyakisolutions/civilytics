# WasteWatch: Technical Approach Document

---

## 1. System Overview

WasteWatch is a monolithic single-node AI platform for real-time illegal waste dumping detection, designed to run entirely on one NVIDIA RTX 3090 GPU. The architecture prioritizes GPU utilization efficiency, event-driven processing, and graceful degradation when resources are constrained.

### Design Principles

1. **Single-GPU co-tenancy** — DeepStream inference and VLM analysis share the RTX 3090 via time-slicing, with degradation controls that reduce VLM concurrency when GPU utilization exceeds thresholds.
2. **Event-driven, not polling** — All inter-service communication flows through Apache Kafka topics. Workers consume events asynchronously, enabling backpressure and independent scaling.
3. **Graceful degradation** — If the VLM service is overloaded, the system continues detecting and tracking; if Qdrant is down, logs fall back to database storage; if Dropbox is unavailable, evidence queues locally.
4. **Zero external cloud dependency for core detection** — All inference runs locally. External services (Dropbox, Telegram, Neon) are optional enhancements, not hard dependencies.

---

## 2. Architecture Components

### 2.1 Video Ingestion Layer

**MediaMTX** serves as the bidirectional RTSP proxy and WebRTC bridge:
- **Input**: Receives raw RTSP H.264 streams from IP cameras (published to `/cam-01`, `/cam-02`, etc.)
- **Output**: Receives inference-annotated streams back from the DeepStream pipeline via RTP/UDP (published to `/cam-01-out`, etc.)
- Serves WebRTC (WHEP) to the browser dashboard (:8889) — frontend fetches the **inference output streams**, not the raw camera feeds
- Configuration-driven source management via `mediamtx.yml`

### 2.2 GPU Pipeline (DeepStream 6.x)

The pipeline is a GStreamer-based application running inside an NVIDIA DeepStream container with full GPU access. It uses a **tee** element to split the processed stream into two branches:

```
RTSP Source (MediaMTX :8554)
    → nvstreammux (batch=4, 1920x1080)
    → ROI Preprocessor (poly→rect crop, reduces GPU load)
    → nvinfer (RF-DETR, TensorRT FP16, 640x640)
    → nvtracker (ByteTrack, libnvds_nvmultiobjecttracker)
    → nvdsanalytics (ROI polygon testing)
    → Event FSM (dwell time state machine)
    → tee
        ├── Branch 1 (Video Output):
        │   queue → nvvideoconvert → H.264 encoder → RTP payload
        │   → UDP sink → MediaMTX :8554 (inference output stream)
        │
        └── Branch 2 (Metadata):
            queue → fakesink (pad probe extracts NvDsBatchMeta)
            → Kafka producer (metadata, events, vlm.requests topics)
```

**ROI Preprocessor (Polygon → Rect Crop)**
- Users define **polygon** ROIs via the interactive frontend editor
- The preprocessor automatically computes the **bounding rectangle** of each polygon
- Only the cropped rect region is sent to nvinfer, avoiding full-frame GPU inference
- The polygon and rect are interdependent: editing the polygon auto-updates the rect
- Users never need to manually define rects — only polygon editing is exposed in the UI
- This optimization significantly reduces GPU utilization when ROI covers a small portion of the frame

**Object Detection — RF-DETR**
- Architecture: Real-Time Detection Transformer
- Export: PyTorch → ONNX → TensorRT FP16 engine
- Classes: person, bicycle, car, motorcycle, bus, truck (COCO subset)
- Input: 640x640, batch size 4, frame skip 2

**Multi-Object Tracking — ByteTrack**
- NVIDIA's built-in low-level tracker library
- Maintains per-track state: `track_id`, `first_seen`, `last_seen`, `bbox_history`
- Lost track timeout: 30 seconds
- Bbox history sampled every ~0.5s, capped at 50 frames

**ROI Management**
- Polygons defined per-camera via REST API (`PUT /api/cameras/{id}/roi`)
- Normalized coordinates (0-1) for resolution independence
- Ray-casting point-in-polygon algorithm for real-time in-ROI testing
- Dynamic `nvdsanalytics` config generation from polygon definitions

**Event Finite State Machine (FSM)**
```
IDLE ──[enters ROI]──→ ENTER ──[dwell ≥ 5s]──→ OBSERVE
                                                    │
                                            [dwell ≥ 15s]
                                                    ↓
                                                VIOLATION ──→ ALERTING ──→ ESCALATED
                                                    │
                                            [leaves ROI]
                                                    ↓
                                                  EXITED
```
- Per-camera, per-track FSM instances
- Configurable thresholds via system config API
- State transitions published to `wastewatch.events` Kafka topic
- `VIOLATION` state triggers VLM analysis request

### 2.3 Vision Language Model (VLM) Analysis

**Model**: Qwen2.5-VL-7B-Instruct-AWQ (4-bit quantized, ~7 GB VRAM)
**Serving**: vLLM with OpenAI-compatible API

**Analysis Flow**:
1. Pipeline publishes `VLMRequest` to `wastewatch.vlm.requests` on VIOLATION transition
2. `VLMWorker` (max concurrency: 2, semaphore-controlled) consumes request
3. Sends multi-frame analysis to vLLM with context-rich prompt including:
   - Camera zone name and track ID
   - Dwell time and movement path summary
   - Object class (person/vehicle)
   - 3-5 base64-encoded video frames
4. VLM returns structured JSON: `is_dumping`, `waste_type`, `severity`, `confidence`, `reasoning`
5. Result published to `wastewatch.vlm.results`
6. On JSON parse failure: automatic retry with stricter prompt suffix

**Prompt Engineering**:
- Dynamic prompts incorporating spatial context (zone, dwell time, trajectory)
- Waste type classification: plastic_bag, construction_debris, household_waste, organic, unknown
- Severity levels: critical (active dumping), warning (suspicious), info (benign)

### 2.4 Alert System

**AlertDispatcher** consumes `wastewatch.vlm.results` and orchestrates multi-channel delivery:

- **Throttling** (Redis-backed):
  - Per-camera cooldown period (configurable, default 60s)
  - Hourly rate limit per camera (configurable, default 10/hour)
  - Prevents alert fatigue during sustained activity

- **Channels**:
  - **Telegram**: Photo + formatted message with severity, location, waste type, confidence
  - **WebSocket**: Real-time push to `/ws/alerts` for dashboard notification panel

- **Filtering**: Only alerts on `is_dumping=True` OR severity in {critical, high, medium}

### 2.5 Evidence Collection

**Smart Snapshot Selection**:
- Scores candidate frames using weighted criteria:
  - Confidence (40%) — detection model confidence
  - Sharpness (30%) — Laplacian variance
  - Framing (30%) — object centering and size in frame
- Best frame is annotated with bounding boxes, track IDs, timestamps, and zone boundaries

**Evidence Package** per violation:
1. Annotated snapshot (best frame, bbox overlay)
2. Raw snapshot (unmodified)
3. Video clip (VIOLATION → EXITED duration)
4. Metadata JSON (FSM state, track history, VLM analysis, timestamps)

**Dropbox Sync**:
- Path structure: `/WasteWatch/{zone}/{camera_id}/{YYYY-MM-DD}/{filename}`
- Resumable uploads with 3-retry exponential backoff
- `sync_status` tracking: pending → uploading → synced / failed
- Graceful skip if access token not configured

### 2.6 License Plate Recognition (LPR)

- **Engine**: PaddleOCR with Indian license plate regex validation
- **Flow**: `wastewatch.lpr` Kafka topic → `LPRWorker` → OCR → database
- **Output**: plate number, confidence score, vehicle bounding box, timestamp
- **Use case**: Repeat offender identification and fine enforcement

### 2.7 Text-to-Speech (TTS)

- **Engine**: Piper TTS (ONNX-based, CPU inference)
- **Model**: `en_US-lessac-medium`
- **Caching**: SHA256(text) → audio URL, stored in `tts_cache` table
- **Fallback**: Generates minimal WAV placeholder if Piper service unavailable
- **Use case**: Audio announcements for field teams and control room operators

### 2.8 Backend API (FastAPI)

**Framework**: FastAPI with async/await throughout, Uvicorn ASGI server

**REST Endpoints** (15+):
| Group | Endpoints | Purpose |
|---|---|---|
| Cameras | CRUD + list | Camera source management |
| ROI | GET/PUT/DELETE | Zone polygon management |
| Events | List + detail + active tracks | Violation event browsing |
| Alerts | List + acknowledge | Alert management |
| Evidence | List by event | Evidence file browsing |
| LPR | List plates | Plate read queries |
| Analytics | Summary, hourly, by-zone, by-camera | Aggregated statistics |
| Config | CRUD | Runtime system configuration |
| System | GPU, containers, Kafka, models, storage, health | Observability |
| Logs | Semantic search | Natural language log queries |

**WebSocket Channels** (3):
- `/ws/metadata/{camera_id}` — Per-frame detection data (bboxes, classes, confidence)
- `/ws/events` — FSM state transitions
- `/ws/alerts` — Real-time alert notifications

**Authentication**: Keycloak JWT middleware
- JWKS endpoint discovery from Keycloak realm
- Token validation with audience and issuer checks
- Role extraction for RBAC (admin, operator, viewer)
- Configurable endpoint protection

### 2.9 Async Kafka Workers

Eight workers run as asyncio tasks within the FastAPI lifespan:

| Worker | Consumes | Produces | Purpose |
|---|---|---|---|
| MetadataConsumer | metadata | — | WebSocket broadcast |
| EventConsumer | events | — | WebSocket broadcast |
| VLMWorker | vlm.requests | vlm.results | VLM inference |
| AlertDispatcher | vlm.results | — | Alert fan-out |
| TTSWorker | tts | — | Audio generation |
| EvidenceWorker | evidence | — | Recording + Dropbox |
| LPRWorker | lpr | — | Plate recognition |
| LogWorker | logs | — | Qdrant embedding |

### 2.10 Data Layer

**PostgreSQL (Neon Serverless)**:
- 12 tables across 5 Alembic migrations
- Async access via SQLAlchemy 2.0 + asyncpg
- Core tables: cameras, violation_events, alerts, track_sessions, vlm_analyses, alert_deliveries, plate_reads, tts_cache, evidence_files, log_entries, system_config

**Redis**:
- Alert throttling (per-camera cooldown counters)
- Session caching
- Rate limiting data structures

**Qdrant**:
- Collection: `wastewatch_logs`
- Embedding model: `all-MiniLM-L6-v2` (384 dimensions)
- Semantic search API: `GET /api/system/logs?q=<natural language query>`
- Graceful degradation if unavailable

### 2.11 System Monitoring & Degradation

**Health Endpoints**:
- GPU: nvidia-smi parsing → utilization %, memory %, temperature, power draw
- Containers: Docker API → per-container CPU/memory/status
- Kafka: Consumer group lag per topic
- Models: vLLM service health check
- Storage: Disk usage for evidence and system partitions
- Aggregated: Combined health score with per-component status

**Degradation System**:
- Monitors GPU utilization, memory pressure, and inference latency
- Automatically reduces VLM concurrency when resources are constrained
- Logs degradation events to Kafka for observability
- Recovers automatically when pressure subsides

### 2.12 Frontend (React)

**Stack**: React 18 + TypeScript + Vite + Tailwind + Shadcn/ui

**Views**:
1. **Monitoring** — Live camera grid showing **inference output streams** via WebRTC (WHEP from MediaMTX :8889), with additional WebSocket metadata overlays (class, confidence, track ID, dwell time), FSM-based color coding (green → yellow → red). The video itself is the annotated GPU pipeline output, not raw camera feeds.
2. **Camera Management** — CRUD, ROI polygon interactive editor with drag handles
3. **Analytics** — Recharts visualizations: hourly trends, zone heatmaps, camera comparisons, summary KPIs
4. **Map** — Leaflet map with camera locations and zone boundaries
5. **History/Timeline** — Event browsing, evidence viewer, video playback
6. **Settings** — MediaMTX configuration, alert thresholds, system tuning
7. **Alert Panel** — Real-time WebSocket notifications with severity badges and acknowledgment

---

## 3. Kafka Topic Architecture

```
                    ┌─────────────────────────────────┐
                    │         DeepStream Pipeline       │
                    └──────┬──────────┬────────────────┘
                           │          │
                    ┌──────▼──┐  ┌────▼─────┐
                    │ metadata│  │  events   │
                    └──┬──────┘  └──┬────────┘
                       │            │
              ┌────────▼──┐   ┌────▼──────────┐
              │ WS /meta  │   │ WS /events    │
              └───────────┘   │               │
                              │  ┌────────────▼──────┐
                              │  │  vlm.requests     │
                              │  └────────┬──────────┘
                              │           │
                              │  ┌────────▼──────────┐
                              │  │  VLM Worker        │
                              │  └────────┬──────────┘
                              │           │
                              │  ┌────────▼──────────┐
                              │  │  vlm.results      │
                              │  └──┬─────┬──────────┘
                              │     │     │
                    ┌─────────▼┐ ┌──▼─────▼──┐
                    │  alerts   │ │ Alert     │
                    └──┬───────┘ │ Dispatcher │
                       │         └──┬────────┘
              ┌────────▼──┐        │
              │ WS /alerts│   ┌────▼──────┐
              └───────────┘   │ Telegram  │
                              └───────────┘

              ┌───────────┐  ┌───────────┐  ┌──────────┐  ┌──────────┐
              │    tts     │  │ evidence  │  │   lpr    │  │   logs   │
              └─────┬─────┘  └─────┬─────┘  └────┬─────┘  └────┬─────┘
                    │              │              │              │
              ┌─────▼─────┐ ┌─────▼─────┐ ┌─────▼────┐  ┌─────▼─────┐
              │ TTS Worker│ │ Evidence  │ │LPR Worker│  │Log Worker │
              │  (Piper)  │ │ (Dropbox) │ │(Paddle)  │  │ (Qdrant)  │
              └───────────┘ └───────────┘ └──────────┘  └───────────┘
```

---

## 4. Deployment Architecture

### Docker Compose Services (9)

| Service | Image | GPU | Port | Purpose |
|---|---|---|---|---|
| kafka | apache/kafka:3.7.0 | No | 9092 | Message broker |
| redis | redis:7-alpine | No | 6379 | Caching/throttling |
| mediamtx | bluenviron/mediamtx | No | 8554, 8889, 9997 | Stream proxy |
| keycloak | keycloak:24.0 | No | 8180 | OAuth2/OIDC |
| qdrant | qdrant:v1.9 | No | 6333 | Vector search |
| api | ./backend (FastAPI) | No | 8080 | REST + WS + Workers |
| pipeline | ./pipeline (DeepStream) | Yes | — | Video processing |
| vlm | vllm/vllm-openai | Yes | 8000 | VLM inference |
| tts | python:3.11-slim | No | 8100 | Audio generation |

### Resource Allocation (RTX 3090, 24 GB VRAM)

- **DeepStream nvinfer**: ~2-4 GB VRAM (TensorRT FP16 engine)
- **ByteTrack nvtracker**: ~0.5 GB VRAM
- **Qwen2.5-VL-7B-AWQ**: ~7 GB VRAM (4-bit quantized via vLLM)
- **Available headroom**: ~12 GB for batch processing and growth

---

## 5. Database Schema

### Migration History

| Version | Name | Tables Added |
|---|---|---|
| 001 | initial_schema | cameras, violation_events, alerts, system_config |
| 002 | track_sessions | track_sessions |
| 003 | vlm_alerts | vlm_analyses, alert_deliveries |
| 004 | phase4_tables | plate_reads, tts_cache, evidence_files |
| 005 | log_entries | log_entries |

### Key Relationships

```
cameras ──< violation_events ──< alerts ──< alert_deliveries
                │
                ├──< evidence_files
                │
cameras ──< track_sessions ──< vlm_analyses
cameras ──< plate_reads
```

---

## 6. Security Architecture

### Authentication & Authorization
- **Keycloak** provides OAuth2/OIDC with JWT tokens
- Backend middleware validates JWTs against Keycloak's JWKS endpoint
- Role-based access: admin (full), operator (events + cameras), viewer (read-only)
- WebSocket connections authenticated via query parameter token

### Data Security
- Database credentials stored in environment variables, not in code
- `.env.example` provided without secrets; `.env` is gitignored
- Dropbox and Telegram tokens are optional; system functions without them
- CORS configured with explicit origin allowlist

### Network Security
- All services communicate within Docker network
- Only API (:8080), MediaMTX (:8554/:8889), and Keycloak (:8180) exposed externally
- Kafka, Redis, Qdrant, and PostgreSQL accessible only within Docker network

---

## 7. Testing Strategy

- **30 automated tests** covering:
  - API endpoint validation (cameras, events, alerts, health, ROI, WebSocket)
  - Event FSM state transitions (all paths including edge cases)
  - Kafka producer serialization
  - VLM response parsing and schema validation
  - Alert throttle logic (cooldown, rate limiting)
- **Test infrastructure**: pytest + pytest-asyncio + httpx.AsyncClient
- **Fixtures**: In-memory SQLite for database tests, mock Kafka producer

---

## 8. Performance Characteristics

| Metric | Target | Approach |
|---|---|---|
| Detection latency | <100ms per frame | TensorRT FP16 batch inference |
| Tracking latency | <10ms per frame | GPU-accelerated ByteTrack |
| VLM analysis time | 5-15s per request | vLLM continuous batching |
| Alert delivery | <2s from VLM result | Async Kafka consumer + WebSocket |
| Frontend video | <500ms latency | WebRTC via MediaMTX |
| API response | <50ms (p95) | Async SQLAlchemy + connection pooling |

---

## 9. Scalability Path

### Current: Single Node (Pilot)
- 1 RTX 3090, 4-8 cameras, all services on one machine
- Docker Compose deployment

### Future: Multi-Node (City-Wide)
- Kubernetes with GPU node pools
- Separate DeepStream pods per camera group
- Centralized Kafka cluster
- Managed PostgreSQL (Neon scales automatically)
- Horizontal VLM serving with model replicas
- 50-200 cameras across multiple locations
