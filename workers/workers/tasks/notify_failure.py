"""notify_workflow_failure — alerts on terminal workflow failures."""
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_notify_failure(input_data: dict, task: dict) -> dict:
    workflow_id = input_data.get(
        "workflow_id", task.get("workflowInstanceId", "unknown")
    )
    event_id = input_data.get("event_id", "unknown")
    failed_task_type = input_data.get("failed_task_type", "unknown")
    reason = input_data.get("reason", "Unknown failure")[:500]
    severity = input_data.get("severity", "CRITICAL")

    log.error(
        "workflow.failure_notification",
        workflow_id=workflow_id,
        event_id=event_id,
        failed_task=failed_task_type,
        severity=severity,
    )

    alert_payload = {
        "source_type": "WORKFLOW",
        "source_id": workflow_id,
        "severity": severity,
        "title": f"Workflow failed: {failed_task_type}",
        "description": (
            f"Event {event_id} processing failed at {failed_task_type}: {reason}"
        ),
        "dedupe_key": f"WORKFLOW:{workflow_id}:{failed_task_type}",
    }

    try:
        if settings.alert_webhook_url:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(settings.alert_webhook_url, json=alert_payload)
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", workflow_id=workflow_id)

    return {"notification_status": "SENT", "alert_source": f"WORKFLOW:{workflow_id}"}
