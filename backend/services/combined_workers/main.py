# backend/services/combined_workers/main.py
"""Combined backend workers — all lightweight Kafka/Redis consumers
in one process.

ponytail: each service's own main() registers its own SIGTERM/SIGINT
handlers via loop.add_signal_handler on the SAME signal — only the
last-registered handler per signal actually fires. In practice this only
affects shutdown niceness: Docker's stop sequence (SIGTERM, wait, SIGKILL)
still terminates the process either way.
"""
import asyncio
import logging

from services.alerts.main import main as alerts_main
from services.sip.main import main as sip_main
from services.evidence.main import main as evidence_main
from services.health_monitor.main import main as health_monitor_main
from services.logs.main import main as logs_main
from services.reports.main import main as reports_main
from services.lpr.main import main as lpr_main
from services.healer.main import _main as healer_main
from services.analytics.main import main as analytics_main
from services.alert_delivery.main import main as alert_delivery_main
from services.tts.main import main as tts_main

logger = logging.getLogger(__name__)

WORKERS = {
    "alerts": alerts_main,
    "sip": sip_main,
    "evidence": evidence_main,
    "health_monitor": health_monitor_main,
    "logs": logs_main,
    "reports": reports_main,
    "lpr": lpr_main,
    "healer": healer_main,
    "analytics": analytics_main,
    "alert_delivery": alert_delivery_main,
    "tts": tts_main,
}


async def main() -> None:
    logger.info("Starting combined workers: %s", ", ".join(WORKERS))
    results = await asyncio.gather(
        *(fn() for fn in WORKERS.values()), return_exceptions=True
    )
    for name, result in zip(WORKERS, results):
        if isinstance(result, BaseException):
            logger.error("Worker %r exited with an exception", name, exc_info=result)


if __name__ == "__main__":
    asyncio.run(main())
