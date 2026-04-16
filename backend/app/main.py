# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""WasteWatch API Server — REST + WebSocket only.

Workers (VLM, TTS, LPR, alerts, evidence, logs, SIP) run as separate
microservices.  See ``backend/services/`` for their entrypoints.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from app.api.routes import cameras, health, config_routes, events, roi, alerts, evidence, lpr, analytics, system, devices, reports
from app.api.routes import logs as log_routes
from app.config.settings import settings
from app.ws import metadata as ws_metadata
from app.ws import events as ws_events
from app.ws import alerts as ws_alerts
from app.services.kafka_consumer import MetadataConsumer
from app.services.event_consumer import EventConsumer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Kafka consumers that feed WebSocket endpoints ---
    metadata_consumer = MetadataConsumer(settings.kafka_bootstrap_servers)
    try:
        await metadata_consumer.start()
    except Exception as e:
        logger.warning("MetadataConsumer failed to start: %s", e)
        metadata_consumer = None

    event_consumer = EventConsumer(settings.kafka_bootstrap_servers)
    try:
        await event_consumer.start()
    except Exception as e:
        logger.warning("EventConsumer failed to start: %s", e)
        event_consumer = None

    yield

    if metadata_consumer:
        await metadata_consumer.stop()
    if event_consumer:
        await event_consumer.stop()


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
    app.include_router(cameras.router, prefix="/api")
    app.include_router(config_routes.router, prefix="/api")
    app.include_router(events.router, prefix="/api")
    app.include_router(roi.router, prefix="/api")
    app.include_router(alerts.router, prefix="/api")
    app.include_router(evidence.router, prefix="/api")
    app.include_router(lpr.router, prefix="/api")
    app.include_router(analytics.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(devices.router, prefix="/api")
    app.include_router(reports.router, prefix="/api")
    app.include_router(log_routes.router, prefix="/api")
    app.include_router(ws_metadata.router, prefix="/ws")
    app.include_router(ws_events.router, prefix="/ws")
    app.include_router(ws_alerts.router, prefix="/ws")

    # Serve TTS audio files so the browser can fetch and play them
    tts_dir = Path("/tmp/wastewatch/tts")
    tts_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/tts", StaticFiles(directory=str(tts_dir)), name="tts")

    # Serve evidence files (keyframes, video clips)
    evidence_dir = Path("/app/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/evidence", StaticFiles(directory=str(evidence_dir)), name="evidence")

    return app


app = create_app()
