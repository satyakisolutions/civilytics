# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Subject Worker -- violation-time face matching (CPU).

Consumes: wastewatch.faces.requests
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md.
"""
import asyncio
import logging
import signal

from qdrant_client import QdrantClient

from app.config.settings import settings as app_settings
from app.subjects.analyzer import FaceAnalyzer
from app.subjects.config import SubjectSettings
from app.subjects.detector import ScrfdDetector
from app.subjects.embedder import ArcFaceEmbedder
from app.subjects.service import SubjectService
from app.subjects.vector_store import FaceVectorStore
from app.subjects.worker import SubjectWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.subject-worker")


async def main() -> None:
    from app.db.engine import async_session  # lazy: engine needs WW_DATABASE_URL

    settings = SubjectSettings.from_env()
    logger.info(
        "Starting subject worker (auto>=%.2f review>=%.2f, retention %dd)",
        settings.match_auto, settings.match_review, settings.retention_days,
    )
    store = FaceVectorStore(QdrantClient(url=settings.qdrant_url), settings.collection)
    await asyncio.to_thread(store.ensure_collection)
    analyzer = FaceAnalyzer(ScrfdDetector(settings.det_model), ArcFaceEmbedder(settings.rec_model), settings)
    service = SubjectService(async_session, store, settings)
    worker = SubjectWorker(app_settings.kafka_bootstrap_servers, service, analyzer, settings)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    logger.info("Subject worker ready")
    try:
        await shutdown.wait()
    finally:
        await worker.stop()
        logger.info("Subject worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
