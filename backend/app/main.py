import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import cameras, health
from app.config.settings import settings
from app.ws import metadata as ws_metadata
from app.services.kafka_consumer import MetadataConsumer


@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer = MetadataConsumer(settings.kafka_bootstrap_servers)
    try:
        await consumer.start()
    except Exception as e:
        logging.getLogger(__name__).warning("Kafka consumer failed to start: %s", e)
        consumer = None
    yield
    if consumer:
        await consumer.stop()


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
    app.include_router(ws_metadata.router, prefix="/ws")
    return app


app = create_app()
