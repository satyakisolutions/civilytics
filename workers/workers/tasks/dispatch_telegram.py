"""dispatch_telegram — send alert to Telegram chats."""
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_dispatch_telegram(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    async with service_client(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_telegram"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "telegram_not_configured"}
        config = resp.json().get("value", {})

    bot_token = config.get("tg_bot_token")
    chat_ids = [c.strip() for c in config.get("tg_chat_ids", "").split(",") if c.strip()]

    if not bot_token or not chat_ids:
        return {"status": "SKIPPED", "reason": "telegram_incomplete_config"}

    detection_type = event.get("detection_type", "Unknown event")
    camera_id = event.get("camera_id", "unknown")
    confidence = event.get("confidence", 0)
    priority = event.get("priority", "medium")

    message = (
        f"\U0001f6a8 *Civilytics Alert*\n\n"
        f"*Type:* {detection_type.replace('_', ' ').title()}\n"
        f"*Camera:* {camera_id}\n"
        f"*Confidence:* {confidence:.0%}\n"
        f"*Priority:* {priority.upper()}\n"
        f"*Event:* `{event_id}`"
    )

    results = []
    async with service_client(timeout=30) as client:
        for chat_id in chat_ids:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                )
                resp.raise_for_status()
                results.append({"chat_id": chat_id, "status": "sent"})
            except Exception as e:
                log.warning("telegram.send_failed", chat_id=chat_id, error=str(e))
                results.append({"chat_id": chat_id, "status": "failed", "error": str(e)})

    log.info("telegram.dispatched", event_id=event_id, results=results)
    return {"status": "COMPLETED", "channel": "telegram", "results": results}
