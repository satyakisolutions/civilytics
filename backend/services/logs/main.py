# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Log Microservice — Centralized log ingestion + Qdrant vector storage.

Consumes: wastewatch.logs

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.logs.worker import LogWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.log-service")


async def main() -> None:
    logger.info("Starting Log Ingestion Service")
    logger.info("Consuming: wastewatch.logs")

    worker = LogWorker(settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("Log service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down Log service")
        await worker.stop()
        logger.info("Log service stopped")


if __name__ == "__main__":
    asyncio.run(main())
