"""Civilytics Workflow Workers — entry point."""
import asyncio
import signal
import sys
import structlog

from workers.config import settings
from workers.conductor_poller import TaskPoller
from workers.tasks.validate_event import handle_validate
from workers.tasks.extract_evidence import handle_extract_evidence
from workers.tasks.run_lpr import handle_run_lpr
from workers.tasks.cluster_sightings import handle_cluster_sightings
from workers.tasks.update_analytics import handle_update_analytics
from workers.tasks.publish_event import handle_publish_event
from workers.tasks.notify_failure import handle_notify_failure
from workers.tasks.dispatch_telegram import handle_dispatch_telegram
from workers.tasks.dispatch_email import handle_dispatch_email
from workers.intake.kafka_consumer import KafkaIntakeService

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

TASK_HANDLERS = [
    ("validate_dumping_event", handle_validate),
    ("extract_evidence", handle_extract_evidence),
    ("run_lpr_analysis", handle_run_lpr),
    ("cluster_subject_sightings", handle_cluster_sightings),
    ("update_analytics", handle_update_analytics),
    ("publish_event_record", handle_publish_event),
    ("notify_workflow_failure", handle_notify_failure),
    ("dispatch_telegram", handle_dispatch_telegram),
    ("dispatch_email", handle_dispatch_email),
]


def _build_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if settings.conductor_auth_mode == "token" and settings.conductor_auth_token:
        headers["X-Authorization"] = settings.conductor_auth_token
    return headers


async def main():
    headers = _build_headers()
    pollers = [
        TaskPoller(
            conductor_url=settings.conductor_server_url,
            headers=headers,
            task_type=task_type,
            handler=handler,
            worker_id=f"{settings.worker_instance_id}-{task_type}",
            poll_interval=settings.worker_poll_interval_ms / 1000,
            batch_size=settings.worker_batch_size,
        )
        for task_type, handler in TASK_HANDLERS
    ]

    intake = KafkaIntakeService()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: [p.stop() for p in pollers] or intake.stop()
        )

    log.info(
        "workers.starting",
        task_types=[t for t, _ in TASK_HANDLERS],
        instance=settings.worker_instance_id,
    )

    tasks = [asyncio.create_task(p.start()) for p in pollers]
    tasks.append(asyncio.create_task(intake.start()))

    await asyncio.gather(*tasks, return_exceptions=True)
    log.info("workers.stopped")


if __name__ == "__main__":
    asyncio.run(main())
