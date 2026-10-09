"""update_analytics — idempotent aggregate updates."""
import httpx
import structlog
from datetime import datetime, timezone
from workers.config import settings

log = structlog.get_logger()


async def handle_update_analytics(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    event_id = event.get("event_id", "unknown")

    log.info("analytics.updating", event_id=event_id)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.analytics_service_url}/api/analytics/upsert",
                json={
                    "event_id": event_id,
                    "workspace_id": event.get("workspace_id"),
                    "zone_id": event.get("zone_id"),
                    "camera_id": event.get("camera_id"),
                    "detection_type": event.get("detection_type"),
                    "occurred_at": event.get("event_occurred_at"),
                    "confidence": event.get("confidence"),
                    "priority": event.get("priority"),
                    "lpr_result": input_data.get("lpr_status"),
                    "cluster_result": input_data.get("cluster_status"),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise Exception(f"Analytics update failed: {e}")

    log.info("analytics.updated", event_id=event_id)

    return {
        "analytics_status": "COMPLETED",
        "update_version": result.get("version", 1),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
