# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Alert Microservice — Telegram + WebSocket + IP Speaker alert dispatch.

Consumes: wastewatch.vlm.results, wastewatch.alerts

Includes PipelineNotifier for VLM-clear feedback to the detection pipeline.

Standalone service — runs independently of the API server.
"""
import asyncio
import logging
import signal

from app.alerts.dispatcher import AlertDispatcher
from app.alerts.ip_speaker import IPSpeakerManager
from app.alerts.pipeline_notifier import PipelineNotifier
from app.tts.engine import TTSEngine
from app.config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.alert-service")


async def main() -> None:
    logger.info("Starting Alert Service")
    logger.info("Consuming: wastewatch.vlm.results, wastewatch.alerts")

    # Shared TTS engine for IP speaker audio
    tts_engine = TTSEngine()
    ip_speaker_mgr = IPSpeakerManager(tts_engine=tts_engine)
    if settings.ip_speaker_enabled:
        try:
            await ip_speaker_mgr.precache_common_alerts()
        except Exception as e:
            logger.warning("IP speaker pre-cache failed: %s", e)

    # Pipeline notifier for VLM-clear -> pipeline feedback
    pipeline_notifier = PipelineNotifier()

    alert_dispatcher = AlertDispatcher(
        settings.kafka_bootstrap_servers,
        ip_speaker_manager=ip_speaker_mgr,
        pipeline_notifier=pipeline_notifier,
    )
    await alert_dispatcher.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("Alert service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down Alert service")
        await alert_dispatcher.stop()
        logger.info("Alert service stopped")


if __name__ == "__main__":
    asyncio.run(main())
