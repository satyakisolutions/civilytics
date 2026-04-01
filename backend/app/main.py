import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import cameras, health, config_routes, events, roi
from app.config.settings import settings
from app.ws import metadata as ws_metadata
from app.ws import events as ws_events
from app.ws import alerts as ws_alerts
from app.services.kafka_consumer import MetadataConsumer
from app.services.event_consumer import EventConsumer
from app.alerts.dispatcher import AlertDispatcher


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

    alert_dispatcher = AlertDispatcher(settings.kafka_bootstrap_servers)
    try:
        await alert_dispatcher.start()
    except Exception as e:
        logging.getLogger(__name__).warning("AlertDispatcher failed to start: %s", e)
        alert_dispatcher = None

    yield

    if metadata_consumer:
        await metadata_consumer.stop()
    if event_consumer:
        await event_consumer.stop()
    if alert_dispatcher:
        await alert_dispatcher.stop()


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
    app.include_router(ws_metadata.router, prefix="/ws")
    app.include_router(ws_events.router, prefix="/ws")
    app.include_router(ws_alerts.router, prefix="/ws")
    return app


app = create_app()
