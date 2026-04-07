# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""SIP Microservice — Voice call alerts via SIP/Twilio with escalation cascade.

Consumes: wastewatch.sip, wastewatch.alerts (for escalation tracking)

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.alerts.sip_gateway import SIPGateway, SIPConfig
from app.alerts.escalation_worker import EscalationWorker
from app.tts.engine import TTSEngine
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.sip-service")


async def main() -> None:
    logger.info("Starting SIP / Escalation Service")
    logger.info("Consuming: wastewatch.sip, wastewatch.alerts")

    # SIP gateway for outbound voice calls
    sip_config = SIPConfig()
    tts_engine = TTSEngine()
    sip_gateway = SIPGateway(config=sip_config, tts_engine=tts_engine)

    # Escalation worker for cascade logic
    escalation_worker = EscalationWorker(settings.kafka_bootstrap_servers)
    await escalation_worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("SIP service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down SIP service")
        await escalation_worker.stop()
        logger.info("SIP service stopped")


if __name__ == "__main__":
    asyncio.run(main())
