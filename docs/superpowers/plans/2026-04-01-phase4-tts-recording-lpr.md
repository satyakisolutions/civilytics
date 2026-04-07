# Phase 4: TTS + Recording + LPR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the alert chain with TTS audio generation, smart evidence recording/snapshots, Dropbox sync, and license plate recognition.

**Architecture:** Piper TTS generates warning audio (with caching). Evidence worker captures smart snapshots and video clips on violations, syncs to Dropbox. PaddleOCR reads Indian vehicle plates. All workers consume from Kafka topics.

**Builds on:** Phase 3 (`phase3/vlm-alerts`)

---

## Tasks

### Task 1: TTS Engine + Cache
- Create `/backend/app/tts/__init__.py`, `engine.py`, `cache.py`, `worker.py`
- TTS engine wraps Piper ONNX for WAV generation
- Cache uses tts_cache table (hash text → stored audio)
- Kafka worker consumes `wastewatch.tts`, generates audio, stores to disk + DB

### Task 2: Evidence — Smart Snapshot + Annotator
- Create `/backend/app/evidence/__init__.py`, `snapshot.py`, `annotator.py`
- Smart snapshot scoring: confidence × 0.4 + sharpness × 0.3 + framing × 0.3
- Annotator draws bboxes, labels, dwell time on frames

### Task 3: Evidence — Recording + Dropbox Sync Worker
- Create `/backend/app/evidence/recorder.py`, `dropbox_sync.py`, `worker.py`
- Recorder manages pre/post event clips
- Dropbox sync with resumable uploads, folder structure, retry
- Kafka worker consumes `wastewatch.evidence`

### Task 4: LPR — PaddleOCR + Indian Plate Regex
- Create `/backend/app/lpr/__init__.py`, `ocr.py`, `worker.py`
- Indian plate regex: `[A-Z]{2}-\d{2}-[A-Z]{1,2}-\d{4}`
- Kafka worker consumes `wastewatch.lpr`

### Task 5: DB Migration — plate_reads, tts_cache, evidence updates
- Add PlateRead, TTSCache ORM models
- Alembic 004 migration

### Task 6: API Routes — Evidence + LPR
- GET /api/evidence (list files), GET /api/lpr/plates
- Wire TTS/Evidence/LPR workers into main.py lifespan

### Task 7: Docker — Piper TTS service
- Add Piper TTS service to docker-compose.yml

### Task 8: Pipeline — Trigger LPR on vehicle in ROI
- Add send_lpr_request to pipeline Kafka producer
- Trigger when vehicle class detected in ROI with VIOLATION state
