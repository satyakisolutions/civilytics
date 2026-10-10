"""dispatch_ip_speaker — send TTS alert to IP speaker via HTTP."""
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_dispatch_ip_speaker(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_ip_speaker"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "ip_speaker_not_configured"}
        config = resp.json().get("value", {})

    host = config.get("speaker_host")
    port = config.get("speaker_port", "8080")
    protocol = config.get("speaker_protocol", "http")
    zone = config.get("speaker_zone", "")

    if not host:
        return {"status": "SKIPPED", "reason": "ip_speaker_incomplete_config"}

    detection_type = event.get("detection_type", "unknown event")
    camera_id = event.get("camera_id", "unknown")
    priority = event.get("priority", "medium")

    announcement = (
        f"Attention {zone}. "
        f"{detection_type.replace('_', ' ')} detected at camera {camera_id}. "
        f"Priority {priority}. Please investigate."
    )

    speaker_url = f"{protocol}://{host}:{port}/api/announce"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                speaker_url,
                json={"text": announcement, "priority": priority, "zone": zone},
            )
            resp.raise_for_status()
        log.info("ip_speaker.announced", event_id=event_id, host=host)
        return {"status": "COMPLETED", "channel": "ip_speaker", "host": host}
    except Exception as e:
        log.error("ip_speaker.failed", event_id=event_id, error=str(e))
        raise Exception(f"IP speaker dispatch failed: {e}")
