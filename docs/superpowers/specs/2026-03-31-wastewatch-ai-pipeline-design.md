# Civilytics AI Pipeline — System Design Specification

**Date:** 2026-03-31
**Status:** Approved
**Approach:** A — Monolithic GPU Process (designed for future microservice extraction)

---

## 1. System Overview

Real-time multi-camera illegal waste dumping detection system. Ingests 10-50 IP camera streams, detects persons and vehicles, tracks individuals through polygonal ROI zones, uses a Vision-Language Model to confirm dumping behavior, and dispatches multi-channel alerts with evidence capture.

### Mandatory Model Stack

| Component | Model | Optimization |
|-----------|-------|-------------|
| Detection | RF-DETR (COCO, class-filtered) | TensorRT FP16 |
| Tracking | ByteTrack | CPU (Kalman + Hungarian) |
| Action Understanding | Qwen2.5-VL-7B AWQ 4-bit | vLLM, CUDA MPS |
| TTS | Piper (ONNX) | CPU |
| LPR/OCR | PaddleOCR | GPU |
| ReID Embeddings | pgvector on Neon Postgres | — |
| Log Search | Qdrant (vector DB) | CPU |

### Detection Class Filter (COCO subset)

| Class | COCO ID | Default Confidence |
|-------|---------|-------------------|
| person | 0 | 0.5 |
| bicycle | 1 | 0.4 |
| car | 2 | 0.5 |
| motorcycle | 3 | 0.5 |
| bus | 5 | 0.5 |
| truck | 7 | 0.5 |

Classes and per-class confidence thresholds are configurable from the WebUI. DeepStream `nvinfer` label filter enforces this at the pipeline level. LPR triggers only for car/motorcycle/bus/truck.

### Hardware

- **Current:** NVIDIA RTX 3090 (24GB VRAM), single server
- **Future:** NVIDIA A30 / A100, multi-GPU

### VRAM Budget (RTX 3090)

| Component | VRAM |
|-----------|------|
| RF-DETR (TensorRT FP16) | ~2 GB |
| ByteTrack | 0 (CPU) |
| Qwen2.5-VL-7B (AWQ 4-bit) | ~14 GB |
| Piper TTS | ~0.5 GB |
| PaddleOCR | ~1 GB |
| Frame buffers + overhead | ~2 GB |
| **Total** | **~19.5 GB / 24 GB** |

---

## 2. Architecture

### Approach A: Monolithic GPU Process (Selected)

Single GPU server, DeepStream pipeline with Kafka messaging, all services on one machine via Docker Compose. Code structured with clean module boundaries for future microservice extraction.

```
┌─────────────────────────────────────────────────────────────────┐
│                  DeepStream Pipeline (GStreamer)                 │
│                                                                 │
│  Input Sources       → nvstreammux (batch)                      │
│  RTSP / RTMP / HTTPS   → nvinfer (RF-DETR TensorRT)            │
│  (via MediaMTX)         → nvtracker (ByteTrack)                 │
│                         → nvdsanalytics (ROI polygons)          │
│                         → nvosd (overlay)                       │
│                         → tee ┬→ nvmsgconv → nvmsgbroker → Kafka│
│                               └→ rtsp-sink → MediaMTX (WebRTC) │
└─────────────────────────────────────────────────────────────────┘
             │
             ▼ Kafka Topics
┌───────────────────────────────────────────────┐
│  wastewatch.metadata     (per-frame metadata) │
│  wastewatch.events       (ROI state changes)  │
│  wastewatch.vlm.requests (trigger VLM)        │
│  wastewatch.vlm.results  (VLM responses)      │
│  wastewatch.alerts       (alert fan-out)      │
│  wastewatch.lpr          (plate reads)        │
│  wastewatch.tts          (audio generation)   │
│  wastewatch.evidence     (recording events)   │
│  wastewatch.logs         (structured logs)    │
│  wastewatch.config.updates (hot-reload)       │
└───────────────────────────────────────────────┘
             │
     ┌───────┼───────┬──────────┬────────────┬──────────┐
     ▼       ▼       ▼          ▼            ▼          ▼
  VLM      Alert   LPR      Analytics    TTS        Log
  Worker   Service  Service   Service     Worker     Ingestion
                                                     (→ Qdrant)
```

### Alternative Approaches (Documented for Future)

**Approach B: Microservice Per Stage** — Each pipeline stage in its own container, communicating via Kafka. Independent scaling, fault isolation. Best for multi-GPU / multi-node. Cons: IPC overhead serializing frames, VRAM duplication from separate CUDA contexts.

**Approach C: Triton Inference Server + Orchestrator** — NVIDIA Triton manages all GPU models with built-in batching, model versioning, health checks. Best for 100+ cameras with MLOps team. Cons: Triton operational complexity, custom backend needed for Qwen2.5-VL.

### Ingestion Matrix

MediaMTX accepts RTSP, RTMP, and HTTPS (HLS/MJPEG) as input sources, normalizes to RTSP internally. DeepStream always reads from MediaMTX's RTSP output.

```
RTSP cameras  ──┐
RTMP streams  ──┼──→ MediaMTX ──→ RTSP ──→ DeepStream (processing)
HTTPS streams ──┘       │
(HLS/MJPEG)             └──→ WebRTC ──→ Frontend (display)
```

Camera source config stored in DB, MediaMTX paths auto-generated via its REST API on camera add/edit.

### Data Flow (per frame cycle)

1. **Ingest:** RTSP/RTMP/HTTPS → MediaMTX → DeepStream decode (GPU hardware)
2. **Detect:** Batch N frames → RF-DETR TensorRT → [bbox, class, conf] (filtered to 6 classes)
3. **Track:** Per-camera ByteTrack update → [track_id, state, bbox, dwell_time_s]
4. **ROI Filter:** nvdsanalytics point-in-polygon → discard tracks outside ROI
5. **Event FSM:** Track enters ROI → ENTER → dwell timer → OBSERVE → VIOLATION
6. **VLM:** Async Kafka queue → Qwen2.5-VL-7B → structured JSON response
7. **LPR:** Vehicle in ROI → crop plate → PaddleOCR → regex validate Indian format
8. **Alert:** Kafka event → fan-out to Telegram, SIP, Speaker, WebSocket
9. **TTS:** Piper generates WAV → push to speaker + WebSocket (audio + transcript)
10. **Evidence:** Smart recording (pre+post clip) + smart snapshot → Dropbox sync
11. **Store:** Event + evidence → Neon Postgres, embedding → pgvector, logs → Qdrant

---

## 3. Event Lifecycle FSM

Each tracked person inside an ROI follows this state machine:

```
                    ┌──────────────────────────────────────────┐
                    │                                          │
                    ▼                                          │
┌─────────┐   track enters   ┌─────────┐   dwell >       ┌───┴─────┐
│  IDLE   │──────ROI────────▶│  ENTER  │───threshold────▶│ OBSERVE │
└─────────┘                  └────┬────┘   (5s default)   └────┬────┘
                                  │                            │
                          track leaves ROI              suspicious behavior
                          before threshold              OR dwell > 15s
                                  │                            │
                                  ▼                            ▼
                            ┌──────────┐              ┌────────────┐
                            │  EXITED  │              │ VIOLATION  │──┐
                            │ (no event│              │            │  │
                            │  logged) │              └─────┬──────┘  │
                            └──────────┘                    │         │
                                                     trigger:        │ repeat within
                                                     • VLM analysis  │ cooldown?
                                                     • Smart record  │     │
                                                     • LPR (if veh)  │     ▼
                                                            │    ┌───────────┐
                                                            ▼    │ ESCALATED │
                                                     ┌──────────┐│ (repeat   │
                                                     │ ALERTING ││  offender)│
                                                     │          │└───────────┘
                                                     │ • Telegram│      │
                                                     │ • SIP call│      │
                                                     │ • Speaker │◀─────┘
                                                     │ • WebSocket│
                                                     └─────┬────┘
                                                           │
                                                    track leaves ROI
                                                    OR timeout (5min)
                                                           │
                                                           ▼
                                                     ┌──────────┐
                                                     │  EXITED  │
                                                     │ (event   │
                                                     │  stored) │
                                                     └──────────┘
```

### States

| State | Entry Condition | Actions | Exit Condition |
|-------|----------------|---------|----------------|
| IDLE | Track not in any ROI | None | Track bbox center enters polygon |
| ENTER | Point-in-polygon = true | Start dwell timer, log entry | Dwell > threshold OR leaves ROI |
| OBSERVE | Dwell exceeds threshold (5s) | Buffer frames for VLM, start monitoring | Suspicious signal OR dwell > 15s OR leaves |
| VIOLATION | VLM detects dumping OR extended dwell | Trigger VLM, start smart recording, trigger LPR if vehicle | VLM result received |
| ALERTING | VLM confirms violation | Fan-out alerts (TG, SIP, speaker, WS), save snapshot | All alerts dispatched |
| ESCALATED | Same track_id or ReID match within cooldown | Extended recording, higher severity, flag repeat offender | Track leaves or timeout |
| EXITED | Track leaves ROI or 5min timeout | Finalize recording, sync evidence, update DB | Terminal state |

### VLM Trigger Heuristics (OBSERVE → VIOLATION)

Lightweight checks computed from ByteTrack metadata — no GPU cost:

1. **Dwell threshold exceeded** — person in ROI > 15s without significant movement
2. **Posture change** — significant bbox aspect ratio change (bending, crouching)
3. **Object appearance** — new detection near person that wasn't there before
4. **Vehicle stop** — vehicle stops in ROI > 10s with person exiting

### Dwell Time Tracking

```
ByteTrack track enters ROI → start timer
Per-frame update: track.dwell_time_s = current_time - track.entered_at

Visible in:
  - WebUI metadata overlay: "Person #42 | 0.92 | 12.3s" (live counter)
  - Telegram alert: "Duration in zone: 45s"
  - VLM prompt context: "Person has been in zone for {dwell_time}s"
  - DB: track_sessions.dwell_time_s (final value on exit)
  - Analytics: avg/max dwell time per zone, per camera

Overlay color coding:
  Green  (0-5s):   just entered
  Yellow (5-15s):  observing
  Red    (>15s):   potential violation
```

---

## 4. Module Structure

```
/app
├── ingestion/              # DeepStream pipeline setup, RTSP source management
│   ├── pipeline.py             # GStreamer/DeepStream pipeline builder
│   ├── source_manager.py       # Add/remove RTSP/RTMP/HTTPS sources dynamically
│   └── health.py               # Camera disconnect detection + auto-reconnect
│
├── detection_rfdetr/       # RF-DETR model management
│   ├── model.py                # TensorRT engine loader
│   ├── config.py               # nvinfer config (batch, precision, class filter)
│   └── calibration.py          # INT8 calibration data (future)
│
├── tracking_bytetrack/     # ByteTrack via DeepStream nvtracker
│   ├── config.py               # Tracker params (association thresholds)
│   └── state.py                # Track state manager (ACTIVE/LOST/EXITED, dwell time)
│
├── roi/                    # Polygon ROI + event state machine
│   ├── polygons.py             # Per-camera polygon definitions (CRUD via API)
│   ├── analytics.py            # nvdsanalytics config generator
│   └── event_fsm.py            # Event lifecycle state machine
│
├── vlm/                    # Qwen2.5-VL-7B async inference
│   ├── model.py                # vLLM OpenAI-compatible client
│   ├── worker.py               # Kafka consumer, async inference queue
│   ├── prompts.py              # Structured prompt templates
│   └── schemas.py              # Output JSON schemas + Pydantic validation
│
├── lpr/                    # License plate recognition
│   ├── detector.py             # Plate region detector
│   ├── ocr.py                  # PaddleOCR + Indian plate regex (XX-00-XX-0000)
│   └── worker.py               # Kafka consumer for vehicle crop events
│
├── alerts/                 # Multi-channel alert dispatch
│   ├── dispatcher.py           # Kafka consumer, fan-out to channels
│   ├── telegram.py             # Bot API: annotated image + text + plate
│   ├── sip.py                  # SIP call trigger with TTS audio
│   ├── speaker.py              # HTTP push / RTSP audio / SIP to IP speaker
│   └── websocket.py            # Push alert + TTS audio/transcript to frontend
│
├── tts/                    # Piper TTS generation
│   ├── engine.py               # Piper ONNX model, generate WAV
│   ├── cache.py                # LRU cache for repeated warnings (tts_cache table)
│   └── worker.py               # Kafka consumer: wastewatch.tts → audio files
│
├── evidence/               # Smart recording + snapshot + Dropbox sync
│   ├── ring_buffer.py          # Per-camera 30s GPU ring buffer
│   ├── recorder.py             # Trigger-based clip extraction (pre+post event)
│   ├── snapshot.py             # Smart frame selection (confidence x sharpness x framing)
│   ├── annotator.py            # Draw bbox/labels/dwell on snapshot
│   ├── dropbox_sync.py         # Resumable upload, folder structure, retry
│   └── worker.py               # Kafka consumer: wastewatch.evidence
│
├── streaming/              # MediaMTX integration
│   ├── mediamtx.py             # Config management, path registration
│   └── overlay.py              # DeepStream OSD config
│
├── auth/                   # Keycloak integration
│   ├── keycloak.py             # Token validation, role extraction
│   ├── middleware.py           # FastAPI auth middleware (JWT)
│   └── sync.py                 # Keycloak → Neon user sync
│
├── monitoring/             # System observability
│   ├── gpu.py                  # nvidia-smi parser, VRAM tracking
│   ├── containers.py           # Docker API stats collector
│   ├── storage.py              # Disk usage, evidence stats
│   ├── kafka_lag.py            # Consumer lag monitor
│   ├── model_health.py         # Per-model latency tracking
│   └── degrade.py              # Degradation level manager
│
├── logs/                   # Structured logging + Qdrant vector search
│   ├── collector.py            # Kafka log producer (all services)
│   ├── embedder.py             # sentence-transformers log embedding
│   ├── qdrant_store.py         # Qdrant insert + semantic search
│   └── worker.py               # Kafka consumer: wastewatch.logs → Qdrant
│
├── api/                    # FastAPI REST + WebSocket
│   ├── main.py                 # App entrypoint, lifespan events
│   ├── routes/
│   │   ├── cameras.py          # Camera CRUD, ROI polygon management
│   │   ├── events.py           # Query violation events, acknowledge
│   │   ├── analytics.py        # Aggregated stats, ReID similarity search
│   │   ├── evidence.py         # List/download clips and snapshots
│   │   ├── config.py           # System config CRUD (WebUI-managed settings)
│   │   ├── system.py           # Health, GPU, storage, containers, models, degrade
│   │   ├── users.py            # User management (Owner only)
│   │   └── logs.py             # Log search (semantic + exact)
│   ├── ws/
│   │   ├── metadata.py         # Per-camera detection metadata stream
│   │   └── alerts.py           # Alert + TTS audio/transcript push
│   └── deps.py                 # Kafka producer, DB session, Keycloak auth
│
├── analytics/              # Persistent storage + ReID
│   ├── db.py                   # SQLAlchemy/asyncpg models (Neon Postgres)
│   ├── reid.py                 # Person appearance embeddings → pgvector
│   └── aggregator.py           # Kafka consumer: compute stats
│
├── config/                 # Centralized configuration
│   ├── settings.py             # Pydantic settings (env vars only for secrets)
│   └── kafka.py                # Topic definitions, consumer group config
│
└── docker/
    ├── Dockerfile.pipeline     # DeepStream + RF-DETR TensorRT
    ├── Dockerfile.vlm          # vLLM + Qwen2.5-VL-7B AWQ
    ├── Dockerfile.api          # FastAPI
    ├── Dockerfile.workers      # Alert, LPR, evidence workers
    ├── Dockerfile.tts          # Piper TTS
    ├── docker-compose.yml      # Production stack
    └── docker-compose.dev.yml  # Dev overrides
```

---

## 5. Database Schema (Neon Postgres)

Single Neon instance shared between backend (direct) and frontend (via Supabase as real-time proxy + auth layer).

### Enums

```sql
CREATE TYPE app_role AS ENUM ('owner', 'admin', 'viewer');
CREATE TYPE camera_status AS ENUM ('online', 'offline', 'warning', 'alert');
CREATE TYPE track_state AS ENUM ('active', 'lost', 'exited');
CREATE TYPE event_fsm_state AS ENUM ('enter', 'observe', 'violation', 'alerting', 'escalated', 'exited');
CREATE TYPE event_type AS ENUM ('dumping_detected', 'suspicious_activity', 'repeat_offender');
CREATE TYPE alert_severity AS ENUM ('critical', 'warning', 'info');
CREATE TYPE alert_status AS ENUM ('active', 'acknowledged', 'resolved');
CREATE TYPE alert_channel AS ENUM ('telegram', 'sip', 'speaker', 'websocket');
CREATE TYPE evidence_type AS ENUM ('video_clip', 'snapshot', 'annotated_snapshot', 'metadata_json');
CREATE TYPE sync_status AS ENUM ('pending', 'uploading', 'synced', 'failed');
```

### Tables

**Existing (updated):**

- `cameras` — add: `roi_polygons JSONB`, `recording_config JSONB`, `source_type TEXT` (rtsp/rtmp/https)
- `violation_events` — add: `fsm_state event_fsm_state`, `vlm_response JSONB`, `track_id INT`, `dwell_time_s FLOAT`
- `alerts` — add: `channel alert_channel`, `tts_audio_url TEXT`, `delivered_at TIMESTAMPTZ`
- `evidence_files` — add: `evidence_type evidence_type`
- `user_roles` — enum updated to `owner/admin/viewer`

**New tables:**

```
track_sessions
  id              UUID PK
  camera_id       FK cameras
  track_id        INT (ByteTrack ID)
  fsm_state       event_fsm_state
  entered_at      TIMESTAMPTZ
  exited_at       TIMESTAMPTZ
  dwell_time_s    FLOAT
  bbox_history    JSONB (sampled positions for path replay)
  reid_embedding  VECTOR(512) (pgvector)
  created_at      TIMESTAMPTZ

vlm_analyses
  id                UUID PK
  track_session_id  FK track_sessions
  camera_id         FK cameras
  frames_analyzed   INT
  prompt_used       TEXT
  response          JSONB ({person_description, action, severity, command, ...})
  inference_time_ms INT
  model_version     TEXT
  created_at        TIMESTAMPTZ

plate_reads
  id                UUID PK
  camera_id         FK cameras
  track_session_id  FK track_sessions (nullable)
  plate_number      TEXT
  confidence        FLOAT
  plate_image_url   TEXT
  vehicle_bbox      JSONB
  timestamp         TIMESTAMPTZ
  created_at        TIMESTAMPTZ

alert_deliveries
  id            UUID PK
  alert_id      FK alerts
  channel       alert_channel
  status        TEXT (sent/failed/retrying)
  payload       JSONB
  delivered_at  TIMESTAMPTZ
  error_message TEXT
  retry_count   INT
  created_at    TIMESTAMPTZ

tts_cache
  id          UUID PK
  text_hash   TEXT UNIQUE
  text        TEXT
  audio_url   TEXT
  duration_ms INT
  created_at  TIMESTAMPTZ

system_config
  key         TEXT PK
  value       JSONB
  updated_by  FK profiles
  updated_at  TIMESTAMPTZ

log_entries
  id          UUID PK
  service     TEXT
  level       TEXT (error/warn/info/debug)
  message     TEXT
  metadata    JSONB
  timestamp   TIMESTAMPTZ
```

### Key Indexes

```sql
CREATE INDEX idx_violation_events_camera_time ON violation_events(camera_id, timestamp DESC);
CREATE INDEX idx_track_sessions_camera_time ON track_sessions(camera_id, entered_at DESC);
CREATE INDEX idx_plate_reads_plate ON plate_reads(plate_number, timestamp DESC);
CREATE INDEX idx_plate_reads_camera_time ON plate_reads(camera_id, timestamp DESC);
CREATE INDEX idx_track_reid ON track_sessions USING ivfflat (reid_embedding vector_cosine_ops);
CREATE INDEX idx_alert_deliveries_status ON alert_deliveries(status) WHERE status != 'sent';
CREATE INDEX idx_log_entries_service_time ON log_entries(service, timestamp DESC);
```

### Row-Level Security

```sql
-- Viewers: read-only
CREATE POLICY viewer_read ON violation_events FOR SELECT
  USING (has_role('viewer', auth.uid()) OR has_role('admin', auth.uid()) OR has_role('owner', auth.uid()));

-- Admins: read + write on events, alerts, cameras
CREATE POLICY admin_write ON violation_events FOR ALL
  USING (has_role('admin', auth.uid()) OR has_role('owner', auth.uid()));

-- Owner only: system_config, user_roles
CREATE POLICY owner_config ON system_config FOR ALL
  USING (has_role('owner', auth.uid()));

CREATE POLICY owner_roles ON user_roles FOR ALL
  USING (has_role('owner', auth.uid()));
```

---

## 6. VLM Prompt Design

### System Prompt

```
You are a waste dumping surveillance analyst. Analyze the provided frames
from camera {camera_id} in {zone_name}.

A person (Track ID: {track_id}) has been in the monitored zone for
{dwell_time}s. Their movement path: {path_summary}.

Analyze the frames and respond ONLY with this JSON:
{
  "person_description": "physical appearance, clothing, distinguishing features",
  "action": "specific action observed across frames",
  "is_dumping": true/false,
  "waste_type": "plastic_bag|construction_debris|household_waste|organic|unknown|none",
  "severity": "critical|warning|info",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of why this is/isn't dumping",
  "command": "warning message to speak aloud if violation confirmed"
}

Rules:
- "critical": active dumping with clear evidence
- "warning": suspicious behavior, possible dumping preparation
- "info": person present but no violation indicators
- If unsure, set severity to "info" — false negatives are better than false alerts
- "command" should be in English, firm but polite, under 15 words
```

### Context Enrichment (appended when available)

```
Additional context:
- Vehicle nearby: {yes/no}, Plate: {plate_number or "unreadable"}
- Time of day: {time} (night-time dumping is higher severity)
- This person/vehicle has been seen {repeat_count} times in the last {hours}h
- Previous violation at this camera: {last_event_summary}
```

### Response Validation

Pydantic model with strict types. On malformed JSON: retry once with stricter prompt. If still fails: default to `severity: "warning"` with generic description, log the failure.

---

## 7. Alert System

### Channels

**Telegram:** Annotated snapshot + VLM text + plate number + dwell time. Template configurable from WebUI.

**SIP/VoIP:** Trigger call, play TTS audio. Script: "Civilytics alert. Dumping detected at {zone_name}. {person_description}. Please investigate immediately."

**IP Speaker:** HTTP push / RTSP audio / SIP. Default cached warnings + contextual VLM command. Supports all three transport protocols.

**WebSocket:** Alert payload + TTS audio URL + transcript for frontend playback.

### WebSocket Alert Payload

```json
{
  "type": "alert",
  "alert_id": "uuid",
  "camera_id": "cam-zone-a-north",
  "severity": "critical",
  "title": "Dumping Detected",
  "message": "Person dumping plastic bags near Zone A entrance",
  "snapshot_url": "/evidence/snapshots/...",
  "vlm_response": { "..." },
  "plate_number": "KL-01-AB-1234",
  "dwell_time_s": 45.2,
  "tts": {
    "text": "Stop. Do not leave that bag here. You are being recorded.",
    "audio_url": "/api/tts/audio/evt_abc123.wav",
    "duration_ms": 3200
  },
  "timestamp": "2026-03-31T14:23:05Z"
}
```

### Throttling

- Per camera: max 1 alert per 60s (configurable)
- Same track_id: no repeat unless re-enters after EXITED
- Per person (ReID): similarity > 0.7 = repeat offender, escalate severity
- Per plate: same plate within 24h = repeat, attach history
- Global: max 10 alerts per camera per hour (circuit breaker, batch into digest)

### TTS Caching

Hash text → check `tts_cache` table. Common warnings pre-generated at startup. Piper ONNX: ~200ms for 3-second clip.

---

## 8. Evidence System

### Smart Recording

Per-camera 30s ring buffer (DeepStream appsink, GPU memory). On VIOLATION:

1. Freeze pre-event buffer (configurable: 10-30s before)
2. Continue recording post-event (configurable: 15-60s after)
3. Mux to MP4 (nvv4l2h264enc → mp4mux)
4. Save to `/evidence/videos/{camera_id}/{timestamp}.mp4`
5. Write metadata sidecar JSON
6. On ESCALATED (repeat offender / critical): extend recording up to 5 min

### Smart Snapshot Selection

From VLM analysis frame buffer, score each frame:

```
score = (detection_confidence x 0.4)
      + (blur_sharpness x 0.3)       # Laplacian variance
      + (framing_score x 0.3)        # % of person+waste visible
```

Save both raw + annotated (bbox, labels, dwell time drawn) versions. Annotated version sent in Telegram alerts.

### Dropbox Sync

```
/WasteWatch/
  ├── Zone-A/
  │   ├── cam-zone-a-north/
  │   │   ├── 2026-03-31/
  │   │   │   ├── 14-23-05_dumping.mp4
  │   │   │   ├── 14-23-05_snapshot.jpg
  │   │   │   ├── 14-23-05_annotated.jpg
  │   │   │   └── 14-23-05_metadata.json
```

Resumable uploads via Dropbox API. Retry with exponential backoff (3 attempts). Dead-letter queue on persistent failure. Uses existing `dropbox_tokens` table for auth, `evidence_files` table for sync status.

---

## 9. Authentication (Keycloak)

Replaces Supabase Auth for user management.

### Setup

- Realm: `wastewatch`
- Roles: `owner`, `admin`, `viewer`
- Clients: `wastewatch-frontend` (public, PKCE), `wastewatch-api` (confidential)
- Frontend: `@react-keycloak/web`, OIDC login, JWT access token
- FastAPI: `python-jose` + Keycloak middleware, role extraction from token claims
- Keycloak uses same Neon instance for its own DB

### Permissions

| Action | Owner | Admin | Viewer |
|--------|-------|-------|--------|
| View live streams & alerts | Y | Y | Y |
| Acknowledge/resolve events | Y | Y | N |
| Manage cameras (add/remove/ROI) | Y | Y | N |
| Manage users & assign roles | Y | N | N |
| System settings & config | Y | N | N |
| Delete evidence / purge data | Y | N | N |
| View analytics & history | Y | Y | Y |
| Dropbox integration settings | Y | N | N |
| System dashboard (health/models) | Y | Y | N |
| Model update / degrade controls | Y | N | N |

---

## 10. System Monitoring & Admin Dashboard

### Admin Dashboard (Owner + Admin)

Displays: GPU health (utilization, VRAM, temperature), storage (disk, evidence, Dropbox sync status), container status (all services: status, CPU/mem, uptime), model health (per-model: status, latency, reload button), Kafka consumer lag per topic, log explorer (Qdrant-powered semantic + exact search).

### Model Update & Degradation

**Update:** Upload new model version via UI → validate format → hot-swap (load, warm-up, switch) → rollback support. Version tracked in `system_config`.

**Degrade levels:**

| Level | State | Active Models |
|-------|-------|--------------|
| 0 | Full | All models |
| 1 | Reduced | VLM disabled, heuristic-only alerts |
| 2 | Minimal | VLM + LPR disabled, detection + tracking only |
| 3 | Passthrough | All AI disabled, stream-only |

**Auto-degrade triggers:**
- VLM latency > 10s for 5 consecutive calls → Level 1
- GPU temp > 85C → reduce batch size, skip VLM
- GPU OOM → restart with lower memory allocation
- Kafka lag > 500 → drop oldest VLM requests

Admin can manually set level or enable auto-degrade. UI shows current level with reason.

### Health API Endpoints

```
GET  /api/system/health              → overall status
GET  /api/system/gpu                 → nvidia-smi parsed
GET  /api/system/containers          → Docker API stats
GET  /api/system/storage             → disk usage, evidence stats
GET  /api/system/kafka               → consumer lag per topic
GET  /api/system/models              → per-model health + latency
POST /api/system/models/{id}/reload  → hot-reload model
POST /api/system/degrade             → set degradation level
GET  /api/system/logs                → semantic + exact log search
```

### Log Monitoring (Qdrant)

All services emit structured logs → Kafka `wastewatch.logs` → embed via sentence-transformers → store in Qdrant with metadata. Raw logs also stored in Neon `log_entries` table for exact search.

---

## 11. Configuration Management

### Env Vars (secrets + infrastructure only)

```
NEON_DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY,
TELEGRAM_BOT_TOKEN, DROPBOX_APP_KEY, DROPBOX_APP_SECRET,
SIP_PASSWORD, KAFKA_BOOTSTRAP, KC_ADMIN_PASSWORD
```

### WebUI-Managed (system_config table, Owner only)

| Group | Settings |
|-------|----------|
| Cameras | RTSP/RTMP/HTTPS URLs, zones, names, GPS, source_type |
| ROI | Polygon vertices per camera (drawn on canvas overlay) |
| Detection | Frame skip, batch size, per-class confidence thresholds |
| Tracking | Dwell threshold (s), lost timeout (s) |
| VLM | Trigger dwell (s), max concurrent, temperature |
| Alerts | Cooldown (s), max/hr, enabled channels per zone |
| Telegram | Chat IDs per zone, template toggle |
| SIP/VoIP | Server, user, recipient numbers per severity |
| Speakers | Endpoints (type, URL), volume, enabled hours |
| TTS | Voice model, speed, default warning messages |
| Recording | Pre-buffer (s), post-buffer (s), max clip length |
| Evidence | Auto-purge days, Dropbox folder structure, sync toggle |
| Schedule | Active hours per zone |

Config changes published to Kafka `wastewatch.config.updates` for hot-reload without restart.

---

## 12. Kafka Topic Design

| Topic | Key | Partitions | Consumers | Retention |
|-------|-----|-----------|-----------|-----------|
| wastewatch.metadata | camera_id | 10 | WebSocket broadcaster | 1h |
| wastewatch.events | camera_id | 10 | Event FSM, Analytics | 7d |
| wastewatch.vlm.requests | camera_id | 4 | VLM worker | 1h |
| wastewatch.vlm.results | event_id | 4 | Event FSM, Alert dispatcher | 7d |
| wastewatch.alerts | severity | 4 | Telegram, SIP, Speaker, WS | 30d |
| wastewatch.lpr | camera_id | 4 | LPR worker, Analytics | 30d |
| wastewatch.tts | camera_id | 2 | TTS worker | 1h |
| wastewatch.evidence | camera_id | 4 | Evidence sync worker | 30d |
| wastewatch.logs | service | 4 | Log ingestion → Qdrant | 7d |
| wastewatch.config.updates | group | 1 | All services (broadcast) | 1h |

---

## 13. Deployment

### Docker Compose Stack

| Service | Image Base | GPU | Ports |
|---------|-----------|-----|-------|
| pipeline | nvcr.io/nvidia/deepstream:7.0 | Yes (MPS) | internal |
| vlm | vllm/vllm-openai | Yes (MPS) | 8000 internal |
| api | python:3.11-slim | No | 8080 |
| workers | python:3.11-slim | No | internal |
| tts | python:3.11-slim | No | internal |
| kafka | apache/kafka:3.7.0 (KRaft) | No | 9092 |
| redis | redis:7-alpine | No | 6379 |
| mediamtx | bluenviron/mediamtx | No | 8554, 8889, 9997 |
| keycloak | quay.io/keycloak/keycloak:24.0 | No | 8180 |
| qdrant | qdrant/qdrant:v1.9 | No | 6333 |

GPU sharing via CUDA MPS. Pipeline priority (continuous), VLM bursty (event-triggered).

### Health & Recovery

- Camera disconnect: exponential backoff reconnect (1s→30s), mark offline after 5 failures
- Pipeline crash: Docker restart policy, Kafka resume from last offset
- VLM overload: consumer lag monitoring, drop oldest on lag > 100, circuit breaker at 10s
- Evidence storage: alert at 80% disk, auto-purge synced files > N days

### Scaling Path

1. **Separate GPUs:** GPU 0 = pipeline, GPU 1 = VLM (CUDA_VISIBLE_DEVICES)
2. **Multi-node (K8s):** Kafka + Redis managed services, pipeline DaemonSet, VLM Deployment with HPA
3. **100+ cameras:** Shard DeepStream pipelines by camera group, scale Kafka partitions

---

## 14. Phased Implementation Plan

### Phase 1: Foundation — DeepStream + Streaming (Weeks 1-2)

- Docker stack: Kafka, Redis, MediaMTX, DeepStream base
- DeepStream pipeline: multi-source ingest → nvstreammux → batch decode
- RF-DETR: ONNX export → TensorRT → nvinfer (COCO class filter)
- DeepStream → RTSP sink → MediaMTX → WebRTC
- Kafka producer: wastewatch.metadata
- FastAPI skeleton: health, WebSocket metadata broadcast
- Frontend: WebSocket metadata overlay on WebRTCPlayer
- Neon DB: initial schema migration

**Deliverable:** Live streams in WebUI with person/vehicle bounding boxes

### Phase 2: Tracking + ROI Engine (Weeks 3-4)

- ByteTrack via DeepStream nvtracker
- Track state manager with dwell time tracking
- nvdsanalytics: polygon ROI
- Event FSM (ENTER → OBSERVE → VIOLATION → EXITED)
- Kafka: wastewatch.events
- Frontend: ROI polygon editor, track IDs + dwell time in overlay
- Neon DB: track_sessions, violation_events
- Settings UI: thresholds, ROI management
- Camera health monitoring + auto-reconnect

**Deliverable:** Persistent tracks, dwell timers, ROI events firing

### Phase 3: VLM + Alerts (Weeks 5-7)

- Qwen2.5-VL-7B AWQ via vLLM container
- CUDA MPS setup
- VLM worker + trigger heuristics
- Prompt templates + response validation
- Alert dispatcher: Kafka fan-out
- Telegram bot: annotated snapshot + metadata
- WebSocket: alert + VLM verdict push
- Frontend: alert panel with VLM details + TTS audio playback
- Throttling + de-duplication (Redis)
- Neon DB: vlm_analyses, alerts, alert_deliveries
- Settings UI: VLM config, alert channels, test buttons

**Deliverable:** Dumping → VLM confirms → Telegram + WebUI alert

### Phase 4: TTS + SIP + Recording + LPR (Weeks 8-10)

- Piper TTS container + cache
- IP speaker: HTTP push, RTSP audio, SIP
- SIP call trigger
- Frontend: TTS playback + transcript
- Smart recording: ring buffer, pre+post clip extraction
- Smart snapshot selection
- Dropbox sync worker
- PaddleOCR LPR pipeline + Indian plate regex
- Neon DB: plate_reads, evidence_files, tts_cache
- Settings UI: speakers, TTS, recording, Dropbox, LPR

**Deliverable:** Full alert chain with evidence capture and Dropbox sync

### Phase 5: Analytics + Auth + Observability (Weeks 11-15)

- Analytics aggregation, dashboard wiring
- Timeline + history views with real data
- ReID: embeddings → pgvector, similarity search API
- Keycloak: realm, roles, OIDC frontend, JWT middleware
- Qdrant: log ingestion, semantic search
- Admin dashboard: GPU, storage, containers, Kafka lag, models
- Model update: hot-swap, version management
- Degrade system: auto-triggers, manual toggle
- Log explorer: semantic + exact search
- RLS policies, user management UI
- Load testing (50 streams), connection pool tuning
- Evidence auto-purge, monitoring alerts

**Deliverable:** Production-ready with analytics, auth, observability

### Phase Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──┐
                                    ├──→ Phase 5
                         Phase 4 ──┘
(Phase 4 can overlap with late Phase 3)
```

---

## 15. Trade-offs

| Decision | Trade-off |
|----------|-----------|
| Frame skip (every 3rd @ 30fps) | May miss <300ms events, acceptable for waste dumping (slow action) |
| VLM event-triggered, not continuous | Relies on heuristic triggers, may miss subtle behavior; saves GPU for detection |
| AWQ 4-bit quantization | ~2-3% accuracy loss vs FP16, necessary for 3090 VRAM budget |
| COCO class filter vs fine-tuned | Misses auto-rickshaws and waste objects; fine-tune in Phase 5 with real data |
| pgvector vs FAISS | Slower for large-scale ReID but fewer moving parts; sufficient for <50 cameras |
| Neon serverless | Cold-start latency on idle connections; mitigated by connection pooler |
| Keycloak over Supabase Auth | Additional container to manage; provides full OIDC/RBAC flexibility |
| Qdrant for logs | Overhead for small deployments; enables semantic search not possible with text-only |
