# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""TTS Microservice — Piper TTS audio generation for alerts and IP speakers.

Consumes: wastewatch.tts

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.tts.worker import TTSWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.tts-service")


async def main() -> None:
    logger.info("Starting TTS Service")
    logger.info("Consuming: wastewatch.tts")

    worker = TTSWorker(settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("TTS service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down TTS service")
        await worker.stop()
        logger.info("TTS service stopped")


if __name__ == "__main__":
    asyncio.run(main())
