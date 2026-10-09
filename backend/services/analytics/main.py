# backend/services/analytics/main.py
"""Analytics Microservice — pre-compute counters from event stream.

Consumes: wastewatch.events
Writes:   Redis counters read by /api/analytics/* endpoints
"""
import asyncio
import logging
import signal

from app.analytics.worker import AnalyticsWorker
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.analytics-service")


async def main() -> None:
    logger.info("Starting Analytics Service")
    logger.info("Consuming: wastewatch.events → Redis counters")

    worker = AnalyticsWorker(settings.kafka_bootstrap_servers)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("Analytics service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down Analytics service")
        await worker.stop()
        logger.info("Analytics service stopped")


if __name__ == "__main__":
    asyncio.run(main())
