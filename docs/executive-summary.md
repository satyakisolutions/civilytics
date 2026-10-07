# Civilytics: Executive Summary

**AI-Powered Real-Time Illegal Waste Dumping Detection for LSG Kerala**

---

## The Problem

Illegal waste dumping is a persistent public health and environmental crisis across Kerala's local self-government (LSG) jurisdictions. Current enforcement is entirely reactive — complaints arrive hours or days after dumping occurs, offenders are long gone, and evidence is circumstantial at best. Municipal sanitation teams spend 60-70% of their time cleaning dump sites rather than preventing dumping. There is no systematic way to identify repeat offenders, track hotspot patterns, or measure whether enforcement actions are working.

## The Solution

Civilytics is a real-time AI surveillance platform that transforms existing IP camera infrastructure into an intelligent waste dumping detection and evidence collection system. It runs on a single NVIDIA RTX 3090 GPU node, keeping hardware costs under $2,000 while delivering enterprise-grade capabilities.

### How It Works

1. **Detect** — RTSP camera feeds are processed through an NVIDIA DeepStream GPU pipeline using RF-DETR object detection and ByteTrack multi-object tracking at 30 FPS. The system identifies people, vehicles, and objects within operator-defined zones of interest.

2. **Analyze** — When a tracked object dwells in a restricted zone beyond configurable thresholds (default: 15 seconds), a Vision Language Model (Qwen2.5-VL-7B) analyzes video frames to determine whether illegal dumping is occurring, classify waste type, and assess severity.

3. **Alert** — Confirmed violations trigger multi-channel alerts (Telegram bot, real-time WebSocket dashboard) with Redis-backed throttling to prevent alert fatigue. Text-to-speech announcements are generated for field teams.

4. **Preserve** — The system automatically captures the best-quality snapshot (scored by sharpness, confidence, and framing), annotates it with bounding boxes and metadata, records video clips, and syncs all evidence to Dropbox with chain-of-custody metadata.

5. **Identify** — License plate recognition (PaddleOCR) captures vehicle plates at dump sites, enabling repeat-offender tracking and fine enforcement.

6. **Monitor** — A React dashboard provides live camera monitoring, interactive zone-of-interest editing, analytics (hourly/zone/camera breakdowns), and full system health observability (GPU utilization, storage, container status, model health, Kafka consumer lag).

## Key Differentiators

| Capability | Civilytics | Traditional CCTV |
|---|---|---|
| Detection latency | 15-30 seconds | Hours to days (manual review) |
| Evidence quality | Annotated snapshots + video + metadata | Raw footage (if retained) |
| License plate capture | Automatic | Manual (if noticed) |
| Repeat offender tracking | Automated pattern analysis | Spreadsheet / memory |
| Cost per camera | ~$50/month amortized | $200-500/month (security guard) |
| Scalability | Add cameras, same GPU | Linear headcount increase |

## Technical Architecture at a Glance

- **GPU Pipeline**: DeepStream 6.x + RF-DETR (TensorRT FP16) + ByteTrack — all on one RTX 3090, with ROI preprocessor (poly→rect crop) to minimize GPU load
- **Video Output**: Inference-annotated stream pushed back to MediaMTX via RTP/UDP, served as WebRTC to frontend
- **AI Model**: Qwen2.5-VL-7B-AWQ (4-bit quantized) via vLLM, sharing the same GPU
- **Event Streaming**: Apache Kafka with 9 topics and 8 async consumer workers
- **Backend**: FastAPI (Python) with 15+ REST endpoints and 3 WebSocket channels
- **Frontend**: React + TypeScript + Shadcn/ui with real-time overlays and WebRTC video
- **Data**: PostgreSQL (Neon serverless), Redis (caching/throttling), Qdrant (semantic log search)
- **Auth**: Keycloak OAuth2/OIDC with role-based access control
- **Deployment**: Docker Compose (9 services), single-node

## Deployment Requirements

| Resource | Specification |
|---|---|
| GPU | NVIDIA RTX 3090 (24 GB VRAM) |
| CPU | 8+ cores (AMD/Intel) |
| RAM | 32 GB minimum |
| Storage | 500 GB SSD (evidence retention) |
| Network | 100 Mbps+ (camera feeds) |
| Cameras | Any RTSP-compatible IP camera |

## Current Status

The platform is **feature-complete** across all 5 development phases:

- **Phase 1**: Foundation — FastAPI backend, DeepStream pipeline, Kafka streaming, WebSocket metadata
- **Phase 2**: Tracking & ROI — ByteTrack MOT, polygon zone editor, event state machine with dwell timing
- **Phase 3**: VLM & Alerts — Qwen2.5-VL analysis, Telegram/WebSocket alerts, Redis throttling
- **Phase 4**: TTS, Recording & LPR — Piper TTS, smart snapshots, Dropbox evidence sync, PaddleOCR
- **Phase 5**: Analytics, Auth & Observability — Keycloak JWT, analytics dashboard, GPU/system monitoring, semantic log search

**50 commits | 131 files | 6,359 lines of code | 30 automated tests**

## Next Steps

1. **Pilot Deployment** — Install at 2-3 known dumping hotspots in a partner LSG jurisdiction
2. **Model Fine-Tuning** — Collect Kerala-specific training data to improve detection accuracy
3. **Mobile App** — Field officer app for receiving alerts and submitting confirmation
4. **Multi-Node Scaling** — Kubernetes deployment for city-wide coverage (50+ cameras)
5. **Integration** — Connect to existing municipal complaint management systems (e.g., Sankhya, ILGMS)

---

*Built for the Local Self-Government Department, Government of Kerala*
