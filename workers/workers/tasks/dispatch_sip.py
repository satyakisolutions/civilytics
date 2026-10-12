"""dispatch_sip — trigger SIP/IVR call for alert escalation."""
from workers.http_client import service_client
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_dispatch_sip(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    async with service_client(timeout=10) as client:
        resp = await client.get(
            f"{settings.api_base_url}/api/config/delivery_channel_sip_phone"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "sip_not_configured"}
        config = resp.json().get("value", {})

    sip_server = config.get("sip_server")
    sip_user = config.get("sip_username")
    sip_pass = config.get("sip_password")
    contacts = [
        c.strip()
        for c in config.get("sip_contacts", "").split(",")
        if c.strip()
    ]

    if not sip_server or not contacts:
        return {"status": "SKIPPED", "reason": "sip_incomplete_config"}

    detection_type = event.get("detection_type", "unknown event")
    camera_id = event.get("camera_id", "unknown")
    priority = event.get("priority", "medium")

    # ponytail: HTTP call to SIP gateway (e.g. Omlilo, Twilio, Asterisk ARI).
    # Real SIP INVITE needs pjsip or similar — use HTTP gateway for now.
    tts_text = (
        f"Civilytics alert. {detection_type.replace('_', ' ')} detected "
        f"at camera {camera_id}. Priority {priority}. "
        f"Press 1 to acknowledge."
    )

    results = []
    async with service_client(timeout=60) as client:
        for contact in contacts:
            try:
                resp = await client.post(
                    f"https://{sip_server}/api/call",
                    auth=(sip_user, sip_pass) if sip_user else None,
                    json={
                        "to": contact,
                        "tts_text": tts_text,
                        "event_id": event_id,
                        "priority": priority,
                    },
                )
                resp.raise_for_status()
                results.append({"contact": contact, "status": "initiated"})
            except Exception as e:
                log.warning("sip.call_failed", contact=contact, error=str(e))
                results.append({"contact": contact, "status": "failed", "error": str(e)})

    log.info("sip.dispatched", event_id=event_id, results=results)
    return {"status": "COMPLETED", "channel": "sip", "results": results}
