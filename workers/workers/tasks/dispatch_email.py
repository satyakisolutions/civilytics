"""dispatch_email — send alert via SMTP email."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx
import structlog
from workers.config import settings

log = structlog.get_logger()


async def handle_dispatch_email(input_data: dict, task: dict) -> dict:
    event = input_data.get("event", {})
    event_id = event.get("event_id", "unknown")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.evidence_service_url}/api/config/delivery_channel_email"
        )
        if resp.status_code != 200:
            return {"status": "SKIPPED", "reason": "email_not_configured"}
        config = resp.json().get("value", {})

    smtp_host = config.get("smtp_host")
    smtp_port = int(config.get("smtp_port", 587))
    smtp_user = config.get("smtp_user")
    smtp_pass = config.get("smtp_pass")
    from_addr = config.get("from_address", smtp_user)
    to_addrs = [a.strip() for a in config.get("to_addresses", "").split(",") if a.strip()]

    if not smtp_host or not to_addrs:
        return {"status": "SKIPPED", "reason": "email_incomplete_config"}

    detection_type = event.get("detection_type", "Unknown event")
    camera_id = event.get("camera_id", "unknown")
    confidence = event.get("confidence", 0)
    priority = event.get("priority", "medium")

    subject = f"[Civilytics] {priority.upper()}: {detection_type.replace('_', ' ').title()}"
    body = (
        f"Civilytics Alert\n\n"
        f"Type: {detection_type.replace('_', ' ').title()}\n"
        f"Camera: {camera_id}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Priority: {priority.upper()}\n"
        f"Event ID: {event_id}\n"
    )

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.sendmail(from_addr, to_addrs, msg.as_string())
        log.info("email.sent", event_id=event_id, to=to_addrs)
        return {"status": "COMPLETED", "channel": "email", "recipients": to_addrs}
    except Exception as e:
        log.error("email.failed", event_id=event_id, error=str(e))
        raise Exception(f"Email dispatch failed: {e}")
