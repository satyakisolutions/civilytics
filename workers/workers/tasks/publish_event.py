"""publish_event_record — persists final event for UI screens."""
import httpx
import structlog
from datetime import datetime, timezone
from workers.config import settings

log = structlog.get_logger()


async def handle_publish_event(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    event_id = event.get("event_id", "unknown")
    workflow_id = task.get("workflowInstanceId", "unknown")

    log.info("event.publishing", event_id=event_id, workflow_id=workflow_id)

    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "event_id": event_id,
        "workflow_id": workflow_id,
        "workspace_id": event.get("workspace_id"),
        "zone_id": event.get("zone_id"),
        "camera_id": event.get("camera_id"),
        "detection_type": event.get("detection_type"),
        "confidence": event.get("confidence"),
        "priority": event.get("priority"),
        "evidence_refs": input_data.get("evidence_refs", []),
        "lpr_result": input_data.get("plates"),
        "cluster_result": input_data.get("match_groups"),
        "analytics_status": input_data.get("analytics_status"),
        "published_at": now,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.analytics_service_url}/api/events/publish", json=payload
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise Exception(f"Event publication failed: {e}")

    log.info("event.published", event_id=event_id)

    return {
        "event_record_id": result.get("id", event_id),
        "published_at": now,
    }
