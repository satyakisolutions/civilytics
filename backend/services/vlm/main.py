# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""VLM Analysis Microservice — Qwen2.5-VL-7B waste dumping confirmation.

Consumes: wastewatch.vlm.requests (Kafka)
Produces: wastewatch.vlm.results (Kafka)
"""
import asyncio
import logging
import signal

from app.vlm.worker import VLMWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.vlm-service")


async def main() -> None:
    logger.info("Starting VLM Analysis Service (Kafka consumer)")

    worker = VLMWorker(bootstrap_servers=settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("VLM service ready — consuming wastewatch.vlm.requests")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down VLM service")
        await worker.stop()
        logger.info("VLM service stopped")


if __name__ == "__main__":
    asyncio.run(main())
