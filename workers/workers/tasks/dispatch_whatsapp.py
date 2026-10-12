"""dispatch_whatsapp — send alert via WhatsApp Business API."""
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_dispatch_whatsapp(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    async with service_client(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_whatsapp"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "whatsapp_not_configured"}
        config = resp.json().get("value", {})

    api_url = config.get("wa_api_url")
    api_token = config.get("wa_api_token")
    phone_numbers = [
        p.strip()
        for p in config.get("wa_phone_numbers", "").split(",")
        if p.strip()
    ]

    if not api_url or not api_token or not phone_numbers:
        return {"status": "SKIPPED", "reason": "whatsapp_incomplete_config"}

    detection_type = event.get("detection_type", "Unknown event")
    camera_id = event.get("camera_id", "unknown")
    confidence = event.get("confidence", 0)
    priority = event.get("priority", "medium")

    message = (
        f"🚨 *Civilytics Alert*\n\n"
        f"*Type:* {detection_type.replace('_', ' ').title()}\n"
        f"*Camera:* {camera_id}\n"
        f"*Confidence:* {confidence:.0%}\n"
        f"*Priority:* {priority.upper()}\n"
        f"*Event:* {event_id}"
    )

    results = []
    async with service_client(timeout=30) as client:
        for phone in phone_numbers:
            try:
                resp = await client.post(
                    f"{api_url}/messages",
                    headers={"Authorization": f"Bearer {api_token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": phone,
                        "type": "text",
                        "text": {"body": message},
                    },
                )
                resp.raise_for_status()
                results.append({"phone": phone, "status": "sent"})
            except Exception as e:
                log.warning("whatsapp.send_failed", phone=phone, error=str(e))
                results.append({"phone": phone, "status": "failed", "error": str(e)})

    log.info("whatsapp.dispatched", event_id=event_id, results=results)
    return {"status": "COMPLETED", "channel": "whatsapp", "results": results}
