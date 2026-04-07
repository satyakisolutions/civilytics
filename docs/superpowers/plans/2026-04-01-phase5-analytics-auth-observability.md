# Phase 5: Analytics + Auth + Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Production-ready system with analytics aggregation, Keycloak auth, system monitoring (GPU/storage/containers), model degradation, log search via Qdrant, and admin dashboard.

**Architecture:** Keycloak provides OIDC auth with JWT middleware. Analytics service aggregates events into dashboards. Monitoring module tracks GPU health, storage, containers via Docker API. Qdrant stores log embeddings for semantic search. Admin dashboard exposes all system controls.

**Builds on:** Phase 4 (`phase4/tts-recording-lpr`)

---

## Tasks

### Task 1: Keycloak Auth — JWT Middleware + Role Extraction
- Create `/backend/app/auth/__init__.py`, `keycloak.py`, `middleware.py`
- JWT validation using python-jose + Keycloak JWKS
- Role extraction from token claims (owner/admin/viewer)
- FastAPI dependency for role-based access control
- Add keycloak service to docker-compose.yml

### Task 2: Analytics — Aggregation Service + API
- Create `/backend/app/analytics/__init__.py`, `aggregator.py`
- Create `/backend/app/api/routes/analytics.py`
- Endpoints: GET /api/analytics/summary, /api/analytics/hourly, /api/analytics/by-zone, /api/analytics/by-camera

### Task 3: System Monitoring — GPU, Storage, Containers
- Create `/backend/app/monitoring/__init__.py`, `gpu.py`, `storage.py`, `containers.py`, `kafka_lag.py`, `model_health.py`, `degrade.py`
- Create `/backend/app/api/routes/system.py`
- Health endpoints: /api/system/health, /api/system/gpu, /api/system/storage, /api/system/containers, /api/system/kafka, /api/system/models
- POST /api/system/degrade for degradation level control

### Task 4: Log System — Qdrant Vector Search
- Create `/backend/app/logs/__init__.py`, `collector.py`, `qdrant_store.py`, `worker.py`
- Kafka consumer: wastewatch.logs → embed → Qdrant
- GET /api/system/logs with semantic + exact search
- Add Qdrant service to docker-compose.yml

### Task 5: DB Migration — log_entries table
- Add LogEntry ORM model
- Alembic 005 migration

### Task 6: Wire Auth Middleware + All Phase 5 into main.py
- Apply auth middleware to protected routes
- Register analytics, system, logs routes
- Start log worker in lifespan

### Task 7: Docker — Keycloak + Qdrant services
- Add keycloak and qdrant services to docker-compose.yml
- Update .env.example with new variables
