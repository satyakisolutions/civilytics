<div align="center">

# Satyaki Solutions Pvt Ltd

**"Creating Innovative Impulse"**

[satyaki.co.in](https://satyaki.co.in)

---

# WasteWatch

**AI-Powered Illegal Waste Dumping Detection for Local Self Government Bodies**

`Computer Vision` `Deep Learning` `Real-Time Analytics` `Edge AI` `IoT Surveillance`

[![Platform](https://img.shields.io/badge/Platform-Linux%20x86__64-blue?style=flat-square)](#prerequisites)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76b900?style=flat-square)](#prerequisites)
[![DeepStream](https://img.shields.io/badge/DeepStream-7.0-76b900?style=flat-square)](#tech-stack)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat-square)](#tech-stack)
[![React](https://img.shields.io/badge/Frontend-React%2018-61DAFB?style=flat-square)](#tech-stack)
[![License](https://img.shields.io/badge/License-Proprietary-FCA719?style=flat-square)](#license)

---

*Built for LSG (Local Self Government) bodies in Kerala, India*

</div>

---

## Overview

WasteWatch is an enterprise-grade, AI-powered surveillance platform that detects and prevents illegal waste dumping in real time. Designed for deployment across municipalities and panchayats under Kerala's Local Self Government framework, the system processes live RTSP camera feeds through a high-performance NVIDIA DeepStream inference pipeline, applies a multi-stage finite state machine for violation tracking, and dispatches multi-channel alerts with full evidentiary chains.

The platform combines edge-optimized object detection (RF-DETR with TensorRT FP16), vision-language model analysis (Qwen2.5-VL-7B), license plate recognition (PaddleOCR), and text-to-speech audio warnings into a unified, event-driven architecture orchestrated through Apache Kafka.

### Key Capabilities

| Capability | Description |
|---|---|
| **Real-Time Detection** | RF-DETR object detection via DeepStream nvinfer, TensorRT FP16, 640x640 inference at batch size 4 |
| **Multi-Object Tracking** | ByteTrack via nvtracker with configurable association thresholds |
| **Violation FSM** | Per-track state machine: IDLE - ENTER - OBSERVE - VIOLATION - ALERTING - ESCALATED - EXITED |
| **ROI Management** | User-drawn polygons, point-in-polygon checks, GPU-efficient cropped inference |
| **VLM Analysis** | Qwen2.5-VL-7B confirms dumping, classifies waste type, assesses severity |
| **License Plate Recognition** | PaddleOCR with Indian plate format validation (KL-01-AB-1234) |
| **Smart Recording** | FFmpeg RTSP clip capture with pre/post buffer around violation events |
| **Audio Alerts** | Piper TTS generates warnings, cached by content hash |
| **Multi-Channel Dispatch** | Telegram, WebSocket, with per-camera cooldown and hourly rate limits |
| **Evidence Chain** | Snapshots, annotated frames, video clips, metadata JSON, Dropbox cloud sync |
| **Dynamic Streams** | StreamWatcher with RTSP probing, auto-reconnect, exponential backoff (5s-300s) |
| **Analytics Dashboard** | Hourly stats, zone/camera breakdowns, violation trend analysis |
| **System Monitoring** | GPU stats, Kafka lag, container health, storage, model health |
| **Semantic Log Search** | Qdrant vector store for log embeddings and full-text search |
| **WebRTC Streaming** | MediaMTX-based live WebRTC player in the React frontend |

---

## Architecture

```
                         +------------------+
                         |   RTSP Cameras   |
                         +--------+---------+
                                  |
                    +-------------v--------------+
                    |   DeepStream 7.0 Pipeline  |
                    |   RF-DETR + ByteTrack      |
                    |   ROI Preprocessor          |
                    |   FSM + Kafka Producer      |
                    +-------------+--------------+
                                  |
                    +-------------v--------------+
                    |      Apache Kafka          |
                    |   (Event-Driven Bus)       |
                    +--+---+---+---+---+---+----+
                       |   |   |   |   |   |
          +------------+   |   |   |   |   +------------+
          |            |   |   |   |                    |
    +-----v----+ +-----v--v-+ | +--v--------+  +-------v------+
    | Metadata | |  VLM     | | |  Alert     |  |  Evidence    |
    | Consumer | |  Worker  | | |  Dispatch  |  |  Worker      |
    +----------+ +----------+ | +------------+  +--------------+
                              |
                  +-----------+-----------+
                  |       |       |       |
            +-----v-+ +---v---+ +v------+ +--------+
            |  LPR  | |  TTS  | |  Log  | | Event  |
            |Worker | |Worker | |Worker | |Consumer|
            +-------+ +-------+ +-------+ +--------+
                              |
                    +---------v----------+
                    |   FastAPI Backend   |
                    |   REST + WebSocket  |
                    +---------+----------+
                              |
                    +---------v----------+
                    |   React Frontend   |
                    |   Dashboard + Maps |
                    +--------------------+
```

### Kafka Topic Map

| Topic | Purpose |
|---|---|
| `wastewatch.metadata` | Per-frame detection data from pipeline |
| `wastewatch.events` | FSM state transitions |
| `wastewatch.vlm.requests` | VLM analysis requests |
| `wastewatch.vlm.results` | VLM analysis results |
| `wastewatch.alerts` | Alert dispatch queue |
| `wastewatch.lpr` | License plate recognition requests |
| `wastewatch.tts` | Text-to-speech requests |
| `wastewatch.evidence` | Evidence processing queue |
| `wastewatch.logs` | Centralized logging |
| `wastewatch.config.updates` | Dynamic configuration updates |

---

## Tech Stack

### Pipeline

| Component | Technology |
|---|---|
| Framework | NVIDIA DeepStream 7.0, GStreamer |
| Python Bindings | PyDS (DeepStream Python) |
| Object Detection | RF-DETR (COCO, 6 classes) |
| Tracking | ByteTrack via nvtracker |
| Inference Engine | TensorRT FP16 |
| Message Bus | Confluent Kafka Producer |

### Backend

| Component | Technology |
|---|---|
| Framework | FastAPI (async) |
| ORM | SQLAlchemy (async) |
| Database | PostgreSQL |
| Migrations | Alembic |
| Message Queue | aiokafka |
| Cache | Redis 7 |
| Auth / IAM | Keycloak 24.0 |
| Vector Store | Qdrant v1.9 |

### Frontend

| Component | Technology |
|---|---|
| Framework | React 18, TypeScript |
| Build Tool | Vite |
| Styling | TailwindCSS |
| UI Components | shadcn/ui, Radix UI |
| Data Fetching | React Query (TanStack) |
| Maps | Leaflet |
| Charts | Recharts |
| Live Video | WebRTC |

### Infrastructure

| Component | Technology |
|---|---|
| Orchestration | Docker Compose |
| Message Broker | Apache Kafka 3.7.0 (KRaft) |
| Media Server | MediaMTX (RTSP/WebRTC) |
| VLM Inference | vLLM (Qwen2.5-VL-7B-Instruct-AWQ) |
| TTS Engine | Piper TTS |

### AI/ML Models

| Model | Purpose |
|---|---|
| RF-DETR | Object detection -- 6 classes: person, bicycle, car, motorcycle, bus, truck |
| Qwen2.5-VL-7B-Instruct-AWQ | Vision-language analysis for dumping confirmation and waste classification |
| Piper TTS | Text-to-speech audio alert generation |
| PaddleOCR | Indian license plate recognition (KL-XX-XX-XXXX format) |

---

## Services

All services are defined in `docker-compose.yml`:

| Service | Image / Build | Port(s) | Purpose |
|---|---|---|---|
| `kafka` | apache/kafka:3.7.0 | 9092 | Event streaming (KRaft mode, no ZooKeeper) |
| `redis` | redis:7-alpine | 6379 | Caching and session state |
| `mediamtx` | bluenviron/mediamtx | 8554, 8889, 9997 | RTSP ingest, WebRTC output, API |
| `keycloak` | keycloak:24.0 | 8180 | Identity and access management / SSO |
| `qdrant` | qdrant/qdrant:v1.9 | 6333 | Vector store for semantic log search |
| `api` | ./backend | 8080 | FastAPI REST + WebSocket backend |
| `pipeline` | ./pipeline | -- | DeepStream inference pipeline (GPU) |
| `vlm` | vLLM | 8000 | Qwen2.5-VL-7B vision-language model |
| `tts` | Piper | 5500 | Text-to-speech audio generation |

---

## Prerequisites

| Requirement | Minimum |
|---|---|
| OS | Ubuntu 22.04+ / Linux x86_64 |
| GPU | NVIDIA GPU with CUDA 12.x and driver 535+ |
| NVIDIA Container Toolkit | Installed and configured |
| Docker | 24.0+ |
| Docker Compose | v2.20+ |
| PostgreSQL | 15+ (host or containerized) |
| Node.js | 18+ (for frontend development) |
| Python | 3.11+ (for backend development) |
| VRAM | 8 GB+ (16 GB+ recommended for VLM) |

---

## Quick Start

### 1. Clone and Configure

```bash
git clone <repository-url> wastewatch
cd wastewatch
cp .env.example .env
```

Edit `.env` with your configuration (see [Configuration](#configuration)).

### 2. Build TensorRT Engine

```bash
# Export RF-DETR model to ONNX
python scripts/export_rfdetr_onnx.py

# Build TensorRT FP16 engine with dynamic batch shapes
bash scripts/build_tensorrt_engine.sh
```

### 3. Start Infrastructure Services

```bash
docker compose up -d kafka redis mediamtx keycloak qdrant
```

### 4. Run Database Migrations

```bash
cd backend
alembic upgrade head
```

### 5. Start Application Services

```bash
# Start all services
docker compose up -d

# Or start individually
docker compose up -d api pipeline vlm tts
```

### 6. Start Frontend (Development)

```bash
cd frontend
npm install
npm run dev
```

### 7. Verify

```bash
# Health check
curl http://localhost:8080/api/health

# Smoke test
bash scripts/smoke_test.sh
```

The dashboard is available at `http://localhost:5173` (dev) or `http://localhost:8080` (production).

---

## Configuration

All environment variables are prefixed with `WW_`:

### Core

| Variable | Description | Default |
|---|---|---|
| `WW_DATABASE_URL` | PostgreSQL connection string | -- |
| `WW_KAFKA_BOOTSTRAP_SERVERS` | Kafka broker address | `kafka:9092` |
| `WW_REDIS_URL` | Redis connection string | `redis://redis:6379` |

### Media & Inference

| Variable | Description | Default |
|---|---|---|
| `WW_MEDIAMTX_API_URL` | MediaMTX API endpoint | `http://mediamtx:9997` |
| `WW_MEDIAMTX_RTSP_URL` | MediaMTX RTSP base URL | `rtsp://mediamtx:8554` |
| `WW_VLM_BASE_URL` | vLLM API endpoint | `http://vlm:8000` |
| `WW_VLM_MODEL` | VLM model identifier | `Qwen2.5-VL-7B-Instruct-AWQ` |
| `WW_SOURCES` | Comma-separated RTSP source URIs | -- |
| `WW_BATCH_SIZE` | DeepStream inference batch size | `4` |
| `WW_FRAME_SKIP` | Inference interval (process every Nth frame) | `2` |

### Authentication

| Variable | Description | Default |
|---|---|---|
| `WW_KEYCLOAK_URL` | Keycloak server URL | `http://keycloak:8080` |
| `WW_KEYCLOAK_REALM` | Keycloak realm name | -- |
| `WW_KEYCLOAK_CLIENT_ID` | Keycloak client ID | -- |

### Alerts

| Variable | Description | Default |
|---|---|---|
| `WW_TELEGRAM_BOT_TOKEN` | Telegram Bot API token | -- |
| `WW_TELEGRAM_CHAT_IDS` | Comma-separated Telegram chat IDs | -- |

---

## Project Structure

```
wastewatch/
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── alerts/             # Telegram, WebSocket, throttling
│   │   ├── analytics/          # Aggregation queries
│   │   ├── api/                # REST routes
│   │   ├── auth/               # Keycloak middleware
│   │   ├── config/             # Settings, Kafka topics
│   │   ├── db/                 # SQLAlchemy models, engine
│   │   ├── evidence/           # Recording, snapshots, Dropbox sync
│   │   ├── logs/               # Qdrant log store
│   │   ├── lpr/                # PaddleOCR plate recognition
│   │   ├── monitoring/         # GPU, Kafka, containers, storage
│   │   ├── schemas/            # Pydantic models
│   │   ├── services/           # Kafka consumers
│   │   ├── tts/                # Piper TTS engine
│   │   ├── vlm/                # VLM client, prompts, schemas
│   │   └── ws/                 # WebSocket handlers
│   ├── alembic/                # Database migrations
│   ├── tests/                  # Test suite
│   └── Dockerfile
├── frontend/                   # React + TypeScript SPA
│   └── src/
│       ├── components/         # UI components
│       ├── hooks/              # Custom React hooks
│       ├── context/            # React contexts
│       └── integrations/       # External service clients
├── pipeline/                   # NVIDIA DeepStream pipeline
│   ├── app/
│   │   ├── pipeline.py         # Main GStreamer pipeline
│   │   ├── event_fsm.py        # Finite state machine
│   │   ├── tracking.py         # Track state management
│   │   ├── roi.py              # ROI polygon manager
│   │   ├── preprocessor.py     # ROI-based GPU cropping
│   │   ├── source_manager.py   # Dynamic RTSP source management
│   │   ├── stream_watcher.py   # Auto-reconnect watcher
│   │   └── kafka_producer.py   # Confluent Kafka producer
│   ├── configs/                # DeepStream, nvinfer, tracker configs
│   ├── models/                 # RF-DETR ONNX / TensorRT models
│   └── Dockerfile              # DeepStream 7.0 Triton base
├── scripts/
│   ├── build_tensorrt_engine.sh    # ONNX to TensorRT FP16
│   ├── export_rfdetr_onnx.py       # RF-DETR to ONNX export
│   └── smoke_test.sh               # System smoke test
├── docs/
│   ├── architecture.drawio         # System architecture diagram
│   └── cloud-deployment.drawio     # Cloud deployment diagram
├── docker-compose.yml              # Production services
├── docker-compose.dev.yml          # Development overrides
└── mediamtx.yml                    # MediaMTX configuration
```

---

## Model Pipeline

### NvDsInfer Configuration (RF-DETR)

The DeepStream pipeline uses a custom nvinfer configuration for the RF-DETR object detector:

| Parameter | Value |
|---|---|
| Config file | `pipeline/configs/nvinfer_rfdetr.txt` |
| Model format | ONNX, auto-converted to TensorRT FP16 |
| Input dimensions | 3 x 640 x 640 |
| Batch size | 4 |
| Inference interval | 2 (every other frame) |
| Classes | 6: person, bicycle, car, motorcycle, bus, truck |
| NMS IoU threshold | 0.5 |
| Pre-cluster threshold | 0.4 |
| Top-k detections | 100 |

Detection metadata is extracted via a custom GStreamer pad probe that parses `NvDsBatchMeta` for per-frame object bounding boxes, class IDs, and confidence scores.

### Auto TensorRT Engine Building

The `scripts/` directory provides an automated pipeline for building optimized TensorRT engines:

**Step 1 -- Export to ONNX** (`export_rfdetr_onnx.py`)
- Downloads the RF-DETR base model weights
- Exports to ONNX format with dynamic batch axis

**Step 2 -- Build TensorRT Engine** (`build_tensorrt_engine.sh`)
- Converts ONNX to TensorRT FP16 engine with dynamic batch shapes:

| Shape Profile | Dimensions | Purpose |
|---|---|---|
| `minShapes` | 1 x 3 x 640 x 640 | Single image inference |
| `optShapes` | 4 x 3 x 640 x 640 | Optimal batch (default) |
| `maxShapes` | 8 x 3 x 640 x 640 | Maximum batch capacity |

- Allocates 4 GB workspace for layer optimization
- Engine is cached and reused until the model or GPU architecture changes

### ROI Preprocessor

The ROI preprocessor computes axis-aligned bounding rectangles from user-drawn ROI polygons and crops the inference input to that region. This reduces the pixel area fed to TensorRT by up to **80%**, significantly lowering GPU inference time and memory usage while maintaining full detection accuracy within the monitored zone.

### Finite State Machine (FSM)

Each tracked object follows a per-track state machine with configurable dwell thresholds:

```
IDLE ──(enters ROI)──> ENTER ──(5s dwell)──> OBSERVE ──(15s dwell)──> VIOLATION
  |                                                                       |
  |                                                          VLM analysis + alerts
  |                                                                       |
  <──(exits ROI)── EXITED <──────── ESCALATED <──────── ALERTING <────────┘
```

---

## API Reference

### REST Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/health` | System health check |
| GET/POST | `/api/cameras` | Camera CRUD operations |
| GET/PUT | `/api/config` | System configuration management |
| GET | `/api/events` | Violation event listing and filtering |
| GET/POST/DELETE | `/api/roi` | ROI polygon management |
| GET | `/api/alerts` | Alert history and status |
| GET | `/api/evidence` | Evidence files (snapshots, clips, metadata) |
| GET | `/api/lpr` | License plate read history |
| GET | `/api/analytics` | Hourly stats, zone/camera breakdowns |
| GET | `/api/system` | GPU stats, Kafka lag, container health, storage |
| GET | `/api/logs` | Semantic log search via Qdrant |

### WebSocket Endpoints

| Endpoint | Description |
|---|---|
| `/ws/metadata` | Real-time per-frame detection metadata stream |
| `/ws/events` | FSM state transition events |
| `/ws/alerts` | Live alert notifications |

### Backend Workers (Kafka Consumers)

All workers are started during the FastAPI application lifespan:

| Worker | Kafka Topic | Responsibility |
|---|---|---|
| MetadataConsumer | `wastewatch.metadata` | Persists detection metadata, feeds dashboard |
| EventConsumer | `wastewatch.events` | Processes FSM transitions, creates ViolationEvent records |
| VLMWorker | `wastewatch.vlm.requests` | Sends frames to Qwen2.5-VL-7B for analysis |
| AlertDispatcher | `wastewatch.alerts` | Dispatches alerts via Telegram, WebSocket with throttling |
| TTSWorker | `wastewatch.tts` | Generates audio warnings via Piper TTS |
| EvidenceWorker | `wastewatch.evidence` | Processes snapshots, clips, Dropbox sync |
| LPRWorker | `wastewatch.lpr` | Runs PaddleOCR plate recognition |
| LogWorker | `wastewatch.logs` | Indexes log entries into Qdrant |

### Database Models (PostgreSQL)

`Camera` | `ViolationEvent` | `Alert` | `SystemConfig` | `TrackSession` | `VLMAnalysis` | `AlertDelivery` | `PlateRead` | `TTSCacheEntry` | `EvidenceFile` | `LogEntry`

---

## Frontend Views

| View | Description |
|---|---|
| **AnalyticsView** | Charts and statistics -- hourly trends, camera breakdowns, violation heatmaps |
| **CameraManagementView** | Camera CRUD, RTSP configuration, ROI polygon drawing |
| **HistoryView** | Violation event history with filtering and evidence review |
| **MapView** | Leaflet map with camera locations and violation markers |
| **MonitoringView** | System health -- GPU utilization, Kafka lag, container status, storage |
| **SettingsView** | System configuration, alert thresholds, user management |
| **TimelineView** | Chronological violation timeline with frame-by-frame playback |

---

## Development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run with hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # Development server on :5173
npm run build        # Production build
npm run lint         # ESLint
```

### Development Compose

```bash
# Uses docker-compose.dev.yml overrides (volume mounts, debug logging)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

---

## Deployment

### Production

```bash
# Build all images
docker compose build

# Start all services
docker compose up -d

# Verify
docker compose ps
curl http://localhost:8080/api/health
```

### GPU Requirements

The `pipeline` service requires the NVIDIA Container Toolkit. Ensure the Docker runtime is configured:

```bash
# Verify NVIDIA runtime
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### Scaling Considerations

- **Kafka**: Scale partitions per topic for higher throughput
- **Pipeline**: One pipeline container per GPU; configure `WW_SOURCES` per instance
- **VLM**: Requires 8 GB+ VRAM; use separate GPU or time-share with pipeline
- **API**: Stateless; scale horizontally behind a load balancer
- **Redis**: Use Redis Sentinel or Cluster for HA

---

## Security

| Feature | Implementation |
|---|---|
| **Authentication** | Keycloak SSO/IAM with RBAC |
| **Authorization** | Role-based access: `owner`, `admin`, `viewer` |
| **RTSP Validation** | Only `rtsp://` scheme allowed; cloud metadata endpoints blocked |
| **Path Traversal Prevention** | Camera ID sanitization on all file operations |
| **Container Isolation** | Non-root Docker containers |
| **Injection Prevention** | Dropbox JSON injection guards |
| **CORS** | Origin whitelist enforcement |
| **Secrets Management** | All sensitive values via environment variables, never committed |

---

## Testing

```bash
# Backend tests
cd backend
pytest --cov=app --cov-report=term-missing

# Frontend tests
cd frontend
npm run test

# System smoke test
bash scripts/smoke_test.sh
```

Current coverage: **86% backend**, **38 frontend component tests**.

---

## License

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.

This software is proprietary and confidential. Unauthorized copying, distribution,
or modification of this software, via any medium, is strictly prohibited.

For licensing inquiries, contact: info@satyaki.co.in
Website: https://satyaki.co.in

---

<div align="center">

**Built with precision by [Satyaki Solutions Pvt Ltd](https://satyaki.co.in)**

*"Creating Innovative Impulse"*

</div>
