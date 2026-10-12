"""update_analytics — idempotent aggregate updates."""
import json
import httpx
from workers.http_client import service_client
import structlog
from datetime import datetime, timezone
from aiokafka import AIOKafkaProducer
from workers.config import settings

log = structlog.get_logger()

_producer: AIOKafkaProducer | None = None


async def _get_producer() -> AIOKafkaProducer:
    global _producer
    if _producer is None:
        _producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await _producer.start()
    return _producer


async def handle_update_analytics(input_data: dict, task: dict) -> dict:
    event = input_data.get("validated_event", input_data.get("event", {}))
    event_id = event.get("event_id", "unknown")

    log.info("analytics.updating", event_id=event_id)

    analytics_payload = {
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
    }

    try:
        async with service_client(timeout=30) as client:
            resp = await client.post(
                f"{settings.api_base_url}/api/analytics/upsert",
                json=analytics_payload,
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPError as e:
        raise Exception(f"Analytics update failed: {e}")

    log.info("analytics.updated", event_id=event_id)

    completed_at = datetime.now(timezone.utc).isoformat()

    producer = await _get_producer()
    await producer.send(
        "wastewatch.events",
        value={
            **analytics_payload,
            "analytics_version": result.get("version", 1),
            "completed_at": completed_at,
            "source": "workflow_worker",
        },
    )

    return {
        "analytics_status": "COMPLETED",
        "update_version": result.get("version", 1),
        "completed_at": completed_at,
    }
