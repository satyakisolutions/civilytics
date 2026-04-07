# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""LPR Microservice — PaddleOCR license plate recognition for Indian vehicles.

Consumes: wastewatch.lpr

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.lpr.worker import LPRWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.lpr-service")


async def main() -> None:
    logger.info("Starting LPR Service")
    logger.info("Consuming: wastewatch.lpr")

    worker = LPRWorker(settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("LPR service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down LPR service")
        await worker.stop()
        logger.info("LPR service stopped")


if __name__ == "__main__":
    asyncio.run(main())
