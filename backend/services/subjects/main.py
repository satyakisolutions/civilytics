# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Subject Worker -- violation-time face matching (CPU).

Consumes: wastewatch.faces.requests
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md.
"""
import asyncio
import logging
import signal
import sys

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


async def supervise(worker: SubjectWorker, shutdown: asyncio.Event) -> int:
    """Wait for a shutdown signal or a crashed worker task, stop the worker.

    Returns the process exit code: 0 on shutdown, 1 when a task crashed (so
    compose `restart: unless-stopped` brings the worker back).
    """
    waiters = [
        asyncio.create_task(shutdown.wait()),
        asyncio.create_task(worker.wait_failed()),
    ]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()
        await worker.stop()
    if worker.failed:
        logger.error("Subject worker task crashed; exiting for restart")
        return 1
    logger.info("Subject worker stopped")
    return 0


async def main() -> int:
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
    return await supervise(worker, shutdown)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
