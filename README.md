<div align="center">

# Satyaki Solutions Pvt Ltd

**"Creating Innovative Impulse"**

[satyaki.co.in](https://satyaki.co.in)

---

# WasteWatch

**AI-Powered Illegal Waste Dumping Detection Platform**

`Computer Vision` `Deep Learning` `Real-Time Analytics` `Edge AI` `IoT Surveillance`

[![Platform](https://img.shields.io/badge/Platform-Linux%20x86__64-blue?style=flat-square)](#prerequisites)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20CUDA-76b900?style=flat-square)](#prerequisites)
[![DeepStream](https://img.shields.io/badge/DeepStream-7.0-76b900?style=flat-square)](#tech-stack)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=flat-square)](#tech-stack)
[![React](https://img.shields.io/badge/Frontend-React%2018-61DAFB?style=flat-square)](#tech-stack)
[![License](https://img.shields.io/badge/License-Proprietary-FCA719?style=flat-square)](#license)

---

*First deployment: Lake Shore Gardens, Vidyaranyapura, Bangalore*

</div>

---

## Overview

WasteWatch is an enterprise-grade, AI-powered surveillance platform that detects and prevents illegal waste dumping in real time. Originally deployed at Lake Shore Gardens (LSG), Thindlu, Vidyaranyapura, Bangalore, the system processes live RTSP camera feeds through a high-performance NVIDIA DeepStream inference pipeline, applies a multi-stage finite state machine for violation tracking, and dispatches multi-channel alerts with full evidentiary chains. Designed for residential communities, municipal bodies, industrial estates, and smart city deployments across India.

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

## Services — Microservices Architecture

WasteWatch runs as **15 Docker Compose services**: 6 infrastructure, 1 pipeline, and 8 backend microservices (same Docker image, different `CMD`).

### Infrastructure Services

| Service | Image | Port(s) | Purpose |
|---|---|---|---|
| `kafka` | apache/kafka:3.7.0 | 9092 | Event streaming (KRaft mode, no ZooKeeper) |
| `redis` | redis:7-alpine | 6379 | Caching, throttling, pipeline command channel |
| `mediamtx` | bluenviron/mediamtx | 8554, 8889, 9997 | RTSP ingest, per-camera WebRTC output, API |
| `keycloak` | keycloak:24.0 | 8180 | Identity and access management / SSO |
| `qdrant` | qdrant/qdrant:v1.9 | 6333 | Vector store for semantic log search |
| `vlm` | vLLM | 8000 | Qwen2.5-VL-7B-AWQ vision-language model (GPU) |

### AI Pipeline

| Service | Image | GPU | Purpose |
|---|---|---|---|
| `pipeline` | DeepStream 7.0 | Required | PGIE (RF-DETR) + SGIE (Action Classifier) + ByteTrack + wastewatchmeta C plugin + NvDsSR smart recording + per-camera RTSP output |

### Backend Microservices

Each service consumes its own Kafka topics and scales independently. All use the same `./backend` Docker image with different `command`:

| Service | Consumes | Produces | Purpose |
|---|---|---|---|
| `api` | — | — | FastAPI REST + WebSocket (no workers, thin) |
| `vlm-worker` | `vlm.requests` | `vlm.results` | Qwen2.5-VL-7B waste dumping analysis |
| `tts-worker` | `tts` | — | Piper TTS audio generation for IP speakers |
| `lpr-worker` | `lpr` | — | PaddleOCR Indian license plate recognition |
| `alert-svc` | `vlm.results`, `alerts` | — | Telegram + IP speaker + WebSocket dispatch |
| `sip-svc` | `sip`, `alerts` | — | SIP/Twilio voice calls + escalation cascade |
| `evidence-svc` | `evidence` | — | Video clip management + Dropbox cloud sync |
| `log-worker` | `logs` | — | Centralized log ingestion + Qdrant vectors |

### Communication

| Channel | Purpose | Direction |
|---|---|---|
| **Kafka** (12 topics) | Data streaming — metadata, events, VLM, alerts, TTS, LPR, evidence, logs | Pipeline → Services, Service ↔ Service |
| **Redis pub/sub** | Control commands — clear violation, update ROI, pause camera | Backend → Pipeline |
| **WebSocket** | Real-time UI updates — detections, alerts, VLM enrichment | API → Frontend |

---

## Alert Priority System

WasteWatch uses a **3-stage priority alert pipeline** that fires alerts in order within 5-20 seconds:

### Priority Order

| Priority | Channel | Trigger |
|:--------:|---------|---------|
| **P1** | Smart Recording | NvDsSRStart — 15s pre-buffer captures lead-up |
| **P2** | VLM + LPR via Kafka | Confirm dumping + identify vehicle plate |
| **P3** | SIP Call | Voice call to zone operator (immediate for P0_CRITICAL) |
| **P4** | WebSocket | Dashboard red/yellow alert banner |
| **P5** | Telegram | Push notification to zone operator's phone |
| **P6** | IP Speaker | On-site audio deterrent |

### Alert Stages

```
Stage 1: PRELIMINARY (T+200-500ms)
    Detection consistent across 2-3 frames
    → Recording starts, VLM requested, WebSocket warning

Stage 2: CONFIRMED (T+500ms or dwell > 20s)
    Action SGIE high-confidence OR dwell threshold met
    → SIP call, Telegram, IP speaker, full fan-out

Stage 3: ENRICHED (T+2-5s)
    VLM + LPR results arrive
    → Update alert severity, add plate number, waste type
    → If VLM says NOT dumping → auto-downgrade + cancel SIP
```

### Escalation Cascade

| Delay | Action |
|------:|--------|
| T+0s | IP Speaker + WebSocket + Telegram (instant) |
| T+0s | SIP call if P0_CRITICAL (confidence > 0.8) |
| T+60s | SIP call to primary operator (if not acknowledged) |
| T+120s | SIP call to zone supervisor |
| T+300s | SIP call to admin (final escalation) |

### Auto-Stop / Auto-Recovery

| Trigger | Action |
|---------|--------|
| Object leaves ROI for 5+ seconds | Stop recording, clear alert, fade bbox |
| VLM says NOT dumping + stagnant dwell 10s | Downgrade to info, stop recording |
| Track re-enters ROI within 60s | **Auto-recover**: restart full alert cascade |
| High-conf dumping redetected on same track | **Auto-recover**: re-dispatch all channels |

---

## Resource Requirements

### Minimum Deployment Specs

| Tier | Setup | RAM | GPU | Cameras |
|------|-------|----:|-----|--------:|
| **Dev** | Single machine | 32 GB | RTX 3060 12GB | 4 cameras |
| **Production (small)** | Single server | 64 GB | RTX 4080 16GB | 8 cameras |
| **Production (large)** | 2 servers | 128 GB | 2x RTX 4090 24GB | 16+ cameras |

### Per-Service Memory

| Service | RAM | GPU VRAM | CPU |
|---------|----:|--------:|----:|
| `pipeline` (DeepStream) | 4 GB | 4-6 GB | 4 cores |
| `vlm` (Qwen2.5-VL-7B-AWQ) | 8 GB | 6-8 GB | 2 cores |
| `api` (FastAPI) | 256 MB | — | 1 core |
| `vlm-worker` | 512 MB | — | 1 core |
| `tts-worker` (Piper) | 512 MB | — | 1 core |
| `lpr-worker` (PaddleOCR) | 1 GB | — | 2 cores |
| `alert-svc` | 256 MB | — | 1 core |
| `sip-svc` | 256 MB | — | 1 core |
| `evidence-svc` | 512 MB | — | 1 core |
| `log-worker` | 256 MB | — | 1 core |
| `kafka` | 1 GB | — | 1 core |
| `redis` | 256 MB | — | 1 core |
| PostgreSQL | 512 MB | — | 1 core |
| `keycloak` | 512 MB | — | 1 core |
| `qdrant` | 512 MB | — | 1 core |
| `mediamtx` | 128 MB | — | 1 core |
| **TOTAL** | **~19 GB** | **~12 GB** | **~20 cores** |

### GPU VRAM Breakdown (RTX 3060 12GB)

```
RF-DETR TensorRT FP16:        ~1.5 GB
Action Classifier TensorRT:   ~0.3 GB
ByteTrack tracker:             ~0.2 GB
NvDsSR recording encoder:     ~0.5 GB
nvvideoconvert + nvosd:        ~0.3 GB
Per-camera decode (4 cams):    ~0.8 GB
vLLM Qwen2.5-VL-7B-AWQ:       ~6.0 GB  (gpu-memory-utilization=0.6)
─────────────────────────────────────────
Total:                         ~9.6 GB / 12 GB available
Headroom:                      ~2.4 GB
```

### Storage Requirements

| Data | Size | Notes |
|------|-----:|-------|
| AI Models (Git LFS) | ~180 MB | RF-DETR ONNX + Action Classifier ONNX + Piper TTS |
| TensorRT Engines | ~100 MB | Auto-built on first run, cached in Docker volume |
| Docker Images | ~15 GB | DeepStream + vLLM + backend |
| Evidence Recordings | ~50 MB/event | MP4 clips (pre+post buffer), snapshots, metadata |
| PostgreSQL | ~1 GB/year | Cameras, events, alerts, tracks, VLM analyses, plates |
| Kafka Logs | ~2 GB | Retention-based, configurable |
| Qdrant Vectors | ~500 MB/year | Log embeddings for semantic search |

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
| Python | 3.12+ (for backend development) |
| RAM | 32 GB+ (19 GB for all services) |
| VRAM | 12 GB+ (RTX 3060 minimum, RTX 4080+ recommended) |

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
├── docker-compose.yml              # All environments (profiles: dev/test/prod/lite)
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
./scripts/start.sh dev
```

---

## Deployment

### Local / On-Premise

```bash
# One-command setup
bash scripts/setup.sh

# Or manually:
docker compose build
docker compose up -d
docker compose ps
curl http://localhost:8080/api/health
```

### AWS Cloud Deployment Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            AWS VPC (10.0.0.0/16)                            │
│                                                                             │
│  ┌──── Public Subnet ────────────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  CloudFront CDN ──→ ALB (Application Load Balancer)                   │  │
│  │                       ├─→ :443 → api (Fargate)                        │  │
│  │                       └─→ :8889 → mediamtx WebRTC (Fargate)           │  │
│  │                                                                       │  │
│  │  NAT Gateway (for private subnet egress)                              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──── Private Subnet A (GPU) ───────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  EC2 g5.2xlarge (NVIDIA A10G 24GB)                                    │  │
│  │  ├── pipeline (DeepStream 7.0)        ~6 GB VRAM                      │  │
│  │  └── vlm (vLLM Qwen2.5-VL-7B-AWQ)    ~8 GB VRAM                      │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──── Private Subnet B (Fargate — CPU microservices) ───────────────────┐  │
│  │                                                                       │  │
│  │  ECS Fargate Cluster                                                  │  │
│  │  ├── api           (0.5 vCPU, 1 GB)    REST + WebSocket              │  │
│  │  ├── vlm-worker    (0.5 vCPU, 1 GB)    VLM Kafka consumer            │  │
│  │  ├── tts-worker    (0.5 vCPU, 1 GB)    Piper TTS audio               │  │
│  │  ├── lpr-worker    (1 vCPU, 2 GB)      PaddleOCR plates              │  │
│  │  ├── alert-svc     (0.25 vCPU, 512 MB) Telegram + IP speaker         │  │
│  │  ├── sip-svc       (0.25 vCPU, 512 MB) SIP gateway + escalation      │  │
│  │  ├── evidence-svc  (0.5 vCPU, 1 GB)    Evidence + S3 sync            │  │
│  │  ├── log-worker    (0.25 vCPU, 512 MB) Qdrant log ingestion          │  │
│  │  └── mediamtx      (0.5 vCPU, 512 MB)  RTSP/WebRTC media server      │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──── AWS Managed Services ─────────────────────────────────────────────┐  │
│  │                                                                       │  │
│  │  Amazon MSK (Kafka)         — 2 brokers, kafka.m5.large               │  │
│  │  Amazon ElastiCache (Redis) — cache.t3.medium, 1 replica              │  │
│  │  Amazon RDS (PostgreSQL)    — db.t3.medium, Multi-AZ                  │  │
│  │  Amazon S3                  — Evidence storage (replaces Dropbox)      │  │
│  │  Amazon ECR                 — Docker image registry                   │  │
│  │  AWS Secrets Manager        — Keycloak passwords, API keys            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──── Private Subnet C (Auth + Vector DB) ──────────────────────────────┐  │
│  │                                                                       │  │
│  │  Fargate: keycloak  (1 vCPU, 2 GB)   IAM/SSO                         │  │
│  │  Fargate: qdrant    (0.5 vCPU, 1 GB) Vector log search               │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘

External:
  ├── Telegram Bot API      (alert push notifications)
  ├── SIP/Twilio            (voice call escalation)
  └── IP Speakers           (on-premise, connected via VPN)
```

### AWS Service Mapping

| WasteWatch Service | AWS Service | Instance / Config |
|---|---|---|
| `pipeline` + `vlm` | **EC2 g5.2xlarge** | NVIDIA A10G 24GB, 8 vCPU, 32 GB RAM |
| `api` | **ECS Fargate** | 0.5 vCPU, 1 GB |
| `vlm-worker` | **ECS Fargate** | 0.5 vCPU, 1 GB |
| `tts-worker` | **ECS Fargate** | 0.5 vCPU, 1 GB |
| `lpr-worker` | **ECS Fargate** | 1 vCPU, 2 GB |
| `alert-svc` | **ECS Fargate** | 0.25 vCPU, 512 MB |
| `sip-svc` | **ECS Fargate** | 0.25 vCPU, 512 MB |
| `evidence-svc` | **ECS Fargate** | 0.5 vCPU, 1 GB |
| `log-worker` | **ECS Fargate** | 0.25 vCPU, 512 MB |
| `mediamtx` | **ECS Fargate** | 0.5 vCPU, 512 MB |
| `keycloak` | **ECS Fargate** | 1 vCPU, 2 GB |
| `qdrant` | **ECS Fargate** | 0.5 vCPU, 1 GB |
| Kafka | **Amazon MSK** | 2x kafka.m5.large |
| Redis | **Amazon ElastiCache** | cache.t3.medium |
| PostgreSQL | **Amazon RDS** | db.t3.medium, Multi-AZ |
| Evidence/Recordings | **Amazon S3** | Standard tier + lifecycle rules |
| Docker Images | **Amazon ECR** | Private registry |
| Secrets | **AWS Secrets Manager** | Keycloak, Telegram, Twilio tokens |
| Load Balancer | **ALB** | HTTPS termination, WebSocket support |
| CDN | **CloudFront** | Frontend static assets |

### AWS Cost Estimation

#### 4-Camera Deployment (Residential Community / Small Site)

| Resource | Service | Config | Monthly | Yearly |
|----------|---------|--------|--------:|-------:|
| GPU Instance | EC2 g5.2xlarge | 1x On-Demand | $958 | $11,496 |
| | EC2 g5.2xlarge | 1x Reserved (1yr) | **$607** | **$7,284** |
| Fargate (9 services) | ECS | ~5 vCPU, 10 GB total | $165 | $1,980 |
| Kafka | MSK | 2x kafka.m5.large | $380 | $4,560 |
| Redis | ElastiCache | cache.t3.medium | $47 | $564 |
| PostgreSQL | RDS | db.t3.medium Multi-AZ | $98 | $1,176 |
| S3 Storage | S3 | ~100 GB/month evidence | $3 | $36 |
| Load Balancer | ALB | 1 ALB + data transfer | $25 | $300 |
| ECR | ECR | ~5 GB images | $1 | $12 |
| Secrets Manager | Secrets | ~10 secrets | $4 | $48 |
| Data Transfer | VPC | ~500 GB/month | $45 | $540 |
| **TOTAL (On-Demand)** | | | **$1,726** | **$20,712** |
| **TOTAL (Reserved GPU)** | | | **$1,375** | **$16,500** |

#### 8-Camera Deployment (Industrial Estate / Tech Park)

| Resource | Change | Monthly | Yearly |
|----------|--------|--------:|-------:|
| GPU | g5.4xlarge (A10G 24GB, more CPU) | $1,214 | $14,568 |
| | Reserved 1yr | **$770** | **$9,240** |
| Fargate | Scale lpr-worker x2, vlm-worker x2 | $220 | $2,640 |
| MSK | 3x kafka.m5.large | $570 | $6,840 |
| RDS | db.t3.large Multi-AZ | $196 | $2,352 |
| S3 | ~200 GB/month | $5 | $60 |
| Other | ALB + ElastiCache + transfer | $120 | $1,440 |
| **TOTAL (Reserved GPU)** | | **$1,881** | **$22,572** |

#### 16-Camera Deployment (Smart City / Multi-Site)

| Resource | Change | Monthly | Yearly |
|----------|--------|--------:|-------:|
| GPU | 2x g5.2xlarge (pipeline + VLM separate) | $1,214 | $14,568 |
| | Reserved 1yr | **$1,214** | **$14,568** |
| Fargate | Scale all workers x2-3 | $400 | $4,800 |
| MSK | 3x kafka.m5.xlarge | $760 | $9,120 |
| RDS | db.r6g.large Multi-AZ | $350 | $4,200 |
| S3 | ~500 GB/month | $12 | $144 |
| Other | ALB + ElastiCache + transfer | $180 | $2,160 |
| **TOTAL (Reserved GPU)** | | **$2,916** | **$34,992** |

#### Cost Optimization Tips

| Strategy | Savings |
|----------|--------:|
| EC2 Reserved Instances (1yr) | ~37% on GPU |
| EC2 Reserved Instances (3yr) | ~55% on GPU |
| Spot Instances for VLM (interruptible) | ~70% on VLM GPU |
| Fargate Spot for non-critical workers (log, evidence) | ~70% on Fargate |
| S3 Intelligent Tiering for old evidence | ~40% on storage |
| NAT Gateway → VPC endpoints for S3/ECR | ~$30/month |
| Scale down non-business hours (8pm-6am) | ~40% on Fargate |

> **Note**: Prices based on AWS ap-south-1 (Mumbai) region as of 2026. Actual costs vary by region, usage patterns, and negotiated rates. GPU Reserved Instance pricing assumes Standard 1-year All Upfront.

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
