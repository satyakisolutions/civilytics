import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import cameras, health, config_routes, events, roi, alerts, evidence, lpr, analytics, system
from app.api.routes import logs as log_routes
from app.config.settings import settings
from app.ws import metadata as ws_metadata
from app.ws import events as ws_events
from app.ws import alerts as ws_alerts
from app.services.kafka_consumer import MetadataConsumer
from app.services.event_consumer import EventConsumer
from app.vlm.worker import VLMWorker
from app.alerts.dispatcher import AlertDispatcher
from app.tts.worker import TTSWorker
from app.evidence.worker import EvidenceWorker
from app.lpr.worker import LPRWorker
from app.logs.worker import LogWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    metadata_consumer = MetadataConsumer(settings.kafka_bootstrap_servers)
    try:
        await metadata_consumer.start()
    except Exception as e:
        logging.getLogger(__name__).warning("MetadataConsumer failed to start: %s", e)
        metadata_consumer = None

    event_consumer = EventConsumer(settings.kafka_bootstrap_servers)
    try:
        await event_consumer.start()
    except Exception as e:
        logging.getLogger(__name__).warning("EventConsumer failed to start: %s", e)
        event_consumer = None

    vlm_worker = VLMWorker(settings.kafka_bootstrap_servers)
    try:
        await vlm_worker.start()
    except Exception as e:
        logging.getLogger(__name__).warning("VLMWorker failed to start: %s", e)
        vlm_worker = None

    alert_dispatcher = AlertDispatcher(settings.kafka_bootstrap_servers)
    try:
        await alert_dispatcher.start()
    except Exception as e:
        logging.getLogger(__name__).warning("AlertDispatcher failed to start: %s", e)
        alert_dispatcher = None

    tts_worker = TTSWorker(settings.kafka_bootstrap_servers)
    try:
        await tts_worker.start()
    except Exception as e:
        logging.getLogger(__name__).warning("TTSWorker failed to start: %s", e)
        tts_worker = None

    evidence_worker = EvidenceWorker(settings.kafka_bootstrap_servers)
    try:
        await evidence_worker.start()
    except Exception as e:
        logging.getLogger(__name__).warning("EvidenceWorker failed to start: %s", e)
        evidence_worker = None

    lpr_worker = LPRWorker(settings.kafka_bootstrap_servers)
    try:
        await lpr_worker.start()
    except Exception as e:
        logging.getLogger(__name__).warning("LPRWorker failed to start: %s", e)
        lpr_worker = None

    log_worker = LogWorker(settings.kafka_bootstrap_servers)
    try:
        await log_worker.start()
    except Exception as e:
        logging.getLogger(__name__).warning("LogWorker failed to start: %s", e)
        log_worker = None

    yield

    if metadata_consumer:
        await metadata_consumer.stop()
    if event_consumer:
        await event_consumer.stop()
    if vlm_worker:
        await vlm_worker.stop()
    if alert_dispatcher:
        await alert_dispatcher.stop()
    if tts_worker:
        await tts_worker.stop()
    if evidence_worker:
        await evidence_worker.stop()
    if lpr_worker:
        await lpr_worker.stop()
    if log_worker:
        await log_worker.stop()


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
    app.include_router(log_routes.router, prefix="/api")
    app.include_router(ws_metadata.router, prefix="/ws")
    app.include_router(ws_events.router, prefix="/ws")
    app.include_router(ws_alerts.router, prefix="/ws")
    return app


app = create_app()
