# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""VLM Analysis Microservice — Qwen2.5-VL-7B waste dumping confirmation.

Entry path: gRPC :50061 (`wastewatch.vlm.v1.VLMService/Analyze`).
Result path: Kafka `wastewatch.vlm.results` (unchanged, so alert_consumer
and other subscribers work as before).
"""
import asyncio
import logging
import os
import signal

from app.vlm.grpc_server import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.vlm-service")


async def main() -> None:
    port = int(os.environ.get("WW_VLM_GRPC_PORT", "50061"))
    logger.info("Starting VLM Analysis Service (gRPC on :%d)", port)
    logger.info("Result topic: wastewatch.vlm.results")

    serve_task = asyncio.create_task(serve(port=port))

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("VLM service ready — waiting for RPCs")
    try:
        await shutdown.wait()
    finally:
        logger.info("Shutting down VLM service")
        serve_task.cancel()
        try:
            await serve_task
        except asyncio.CancelledError:
            pass
        logger.info("VLM service stopped")


if __name__ == "__main__":
    asyncio.run(main())
