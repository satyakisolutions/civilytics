# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""VLM Analysis Microservice — Qwen2.5-VL-7B waste dumping confirmation.

Consumes: wastewatch.vlm.requests
Produces: wastewatch.vlm.results

Standalone service — runs independently of the API server.
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
    logger.info("Starting VLM Analysis Service")
    logger.info("Model endpoint: %s", settings.vlm_base_url)
    logger.info("Consuming: wastewatch.vlm.requests")
    logger.info("Producing: wastewatch.vlm.results")

    worker = VLMWorker(settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("VLM service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down VLM service")
        await worker.stop()
        logger.info("VLM service stopped")


if __name__ == "__main__":
    asyncio.run(main())
