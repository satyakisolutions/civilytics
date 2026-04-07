# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Evidence Microservice — Video clip management + Dropbox cloud sync.

Consumes: wastewatch.evidence

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.evidence.worker import EvidenceWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.evidence-service")


async def main() -> None:
    logger.info("Starting Evidence Service")
    logger.info("Consuming: wastewatch.evidence")
    logger.info("RTSP source: %s", settings.mediamtx_rtsp_url)

    worker = EvidenceWorker(
        settings.kafka_bootstrap_servers,
        mediamtx_rtsp_url=settings.mediamtx_rtsp_url,
    )
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("Evidence service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down Evidence service")
        await worker.stop()
        logger.info("Evidence service stopped")


if __name__ == "__main__":
    asyncio.run(main())
