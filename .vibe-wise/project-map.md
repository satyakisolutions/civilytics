# Project Map

## Purpose
Civilytics / WasteWatch: detect illegal waste dumping from fixed CCTV, attribute it
to a person or vehicle, and raise alerts with evidence for municipal enforcement.

## Requirements
- Detect dumping events from live RTSP camera feeds.
- Attribute the act (who dumped what), not just "waste is present".
- Alert with evidence (annotated frame/clip) and support operator review in a UI.
- Runs on a single RTX 3060 (12 GB) for the current deployment.

## Components
- `pipeline/` — DeepStream/GStreamer analytics. RF-DETR-Seg nano via TensorRT
  (`pipeline/models/rfdetr_seg/`), custom C++ parser
  (`pipeline/nvdsinfer_custom_impl_Seg/nvdsparseseg_RfdetrSeg.cpp`), nvtracker,
  FSM (`pipeline/app/event_fsm.py`), bag/waste triggers, OSD decoration
  (`pipeline/app/pipeline.py`), output republish (`pipeline/app/output_manager.py`).
- `backend/` — FastAPI (`backend/app/main.py`); routes for alerts, cameras,
  analytics, evidence, lpr, devices, health, logs.
- `frontend/` — React + Vite + Radix UI (`frontend/package.json`).
- `research/` — model work: RF-DETR training, RelateAnything relation model,
  SAM3 masking, dataset prep. Not part of the deployed stack.
- Infra (compose): postgres, kafka, redis, mediamtx, keycloak, qdrant, api,
  vlm-worker, tts-worker, backend-workers, pipeline, vlm, tts.

## Main Flow
cameras --RTSP--> mediamtx --> pipeline (detect -> track -> FSM -> relation)
pipeline --Kafka--> backend-workers --> postgres
backend-workers --> vlm-worker, tts-worker
pipeline --RTSP--> mediamtx (`cam-XX-out`, annotated) --> frontend
api (FastAPI) --> frontend (cameras, alerts read from postgres)

## Data and Trust Boundaries
- postgres: cameras, alerts, alert_deliveries (verified).
- redis: pipeline command channel (`ww:pipeline:commands`), verified.
- kafka: pipeline -> backend event bus (verified).
- mediamtx: RTSP ingest and annotated-output republish (verified).
- keycloak present in full compose; auth is NOT enforced in the lite stack
  (camera POST/PATCH succeeded unauthenticated) — unknown for production.
- qdrant present in full compose; purpose unverified.

## Build and Deployment
- `docker-compose.yml` (full, 13 services), `docker-compose.lite.yml` (core path),
  `.dev.yml`, `.override.yml`.
- Pipeline image builds the custom seg parser at image-build time
  (`pipeline/Dockerfile:131`).
- Deployment target beyond docker compose: unknown.

## Unknowns
- keycloak/qdrant actual role in production.
- Auth enforcement outside the lite stack.
- Deployment target (single host? edge devices? orchestration?).
