# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Alert Delivery Service — dispatches alerts to enabled channels.

Consumes: wastewatch.alerts (separate consumer group from alert_consumer.py)

Loads delivery channel configuration from SystemConfig and dispatches to:
  - Email (SMTP via aiosmtplib)
  - IP Speaker (HTTP POST)
  - SIP/Phone (publishes to wastewatch.sip topic for existing SIP worker)
  - Storage/Drive (writes alert JSON to configured path)
  - WhatsApp / Telegram (stub — logs "not implemented")

Each delivery attempt is logged to ops_alert_deliveries table.
"""
import asyncio
import json
import logging
import signal
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import aiosmtplib
import httpx
import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy import insert, select

from app.config.settings import settings
from app.db.engine import async_session
from app.db.models import SystemConfig
from app.db.workflow_models import OpsAlertDelivery

logger = logging.getLogger("wastewatch.alert-delivery")

TOPIC_ALERTS = "wastewatch.alerts"
TOPIC_SIP = "wastewatch.sip"
GROUP_DELIVERY = "alert-delivery-group"


async def _load_channel_config(key: str) -> dict | None:
    try:
        async with async_session() as session:
            row = await session.execute(
                select(SystemConfig.value).where(SystemConfig.key == key)
            )
            val = row.scalar_one_or_none()
            return val if isinstance(val, dict) else None
    except Exception:
        logger.exception("Failed to load config key=%s", key)
        return None


async def _log_delivery(
    alert_id: str,
    channel: str,
    recipient: str,
    status: str,
    error: str | None = None,
    provider_msg_id: str | None = None,
):
    try:
        async with async_session() as session:
            await session.execute(
                insert(OpsAlertDelivery).values(
                    id=uuid.uuid4(),
                    alert_id=uuid.UUID(alert_id) if alert_id else uuid.uuid4(),
                    channel=channel,
                    recipient=recipient,
                    delivery_status=status,
                    error_message=error,
                    provider_message_id=provider_msg_id,
                    delivered_at=datetime.now(timezone.utc) if status == "SENT" else None,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Failed to log delivery: channel=%s status=%s", channel, status)


def _build_alert_email(alert: dict) -> str:
    return f"""<html><body style="font-family:sans-serif;max-width:600px;margin:auto">
<h2 style="color:#d32f2f">⚠ Civilytics Alert</h2>
<table style="border-collapse:collapse;width:100%">
<tr><td style="padding:4px 8px;font-weight:bold">Severity</td>
    <td style="padding:4px 8px">{alert.get('severity','unknown').upper()}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold">Camera</td>
    <td style="padding:4px 8px">{alert.get('camera_id','—')}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold">Zone</td>
    <td style="padding:4px 8px">{alert.get('zone','—')}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold">Detection</td>
    <td style="padding:4px 8px">{alert.get('detection_type','—')}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold">Confidence</td>
    <td style="padding:4px 8px">{alert.get('confidence','—')}</td></tr>
<tr><td style="padding:4px 8px;font-weight:bold">Time</td>
    <td style="padding:4px 8px">{alert.get('detected_at', alert.get('timestamp','—'))}</td></tr>
</table>
<p style="margin-top:16px">{alert.get('title','Alert')}: {alert.get('message','')}</p>
<hr><p style="color:#888;font-size:12px">Civilytics WasteWatch — Automated Alert</p>
</body></html>"""


async def _deliver_email(alert: dict, config: dict, alert_id: str):
    recipients = config.get("recipients", [])
    if not recipients:
        logger.warning("Email channel enabled but no recipients configured")
        await _log_delivery(alert_id, "email", "none", "SKIPPED", error="No recipients")
        return

    html = _build_alert_email(alert)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Civilytics] {alert.get('severity','').upper()}: {alert.get('title','Alert')}"
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            use_tls=settings.smtp_tls,
            timeout=15,
        )
        for r in recipients:
            await _log_delivery(alert_id, "email", r, "SENT")
        logger.info("Email sent to %d recipients for alert %s", len(recipients), alert_id)
    except Exception as e:
        for r in recipients:
            await _log_delivery(alert_id, "email", r, "FAILED", error=str(e)[:500])
        logger.exception("Email delivery failed for alert %s", alert_id)


async def _deliver_ip_speaker(alert: dict, config: dict, alert_id: str):
    endpoint = config.get("endpoint", "")
    if not endpoint:
        await _log_delivery(alert_id, "ip_speaker", "none", "SKIPPED", error="No endpoint")
        return

    tts_text = alert.get("tts_text") or alert.get("message", "Alert detected")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(endpoint, json={"text": tts_text, "priority": "high"})
            resp.raise_for_status()
        await _log_delivery(alert_id, "ip_speaker", endpoint, "SENT")
        logger.info("IP Speaker alert sent to %s", endpoint)
    except Exception as e:
        await _log_delivery(alert_id, "ip_speaker", endpoint, "FAILED", error=str(e)[:500])
        logger.exception("IP Speaker delivery failed")


async def _deliver_sip(alert: dict, producer: AIOKafkaProducer, alert_id: str):
    if not settings.sip_enabled:
        await _log_delivery(alert_id, "sip_phone", "sip_topic", "SKIPPED", error="SIP disabled")
        return
    try:
        await producer.send_and_wait(
            TOPIC_SIP,
            value={
                "alert_id": alert_id,
                "camera_id": alert.get("camera_id"),
                "severity": alert.get("severity"),
                "message": alert.get("message", ""),
                "title": alert.get("title", "Alert"),
            },
        )
        await _log_delivery(alert_id, "sip_phone", "sip_topic", "SENT")
        logger.info("SIP alert published for %s", alert_id)
    except Exception as e:
        await _log_delivery(alert_id, "sip_phone", "sip_topic", "FAILED", error=str(e)[:500])
        logger.exception("SIP delivery failed")


async def _deliver_storage(alert: dict, config: dict, alert_id: str):
    storage_path = config.get("path", "/tmp/wastewatch/alert_archive")
    try:
        out_dir = Path(storage_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"alert_{alert_id}_{ts}.json"
        (out_dir / filename).write_text(json.dumps(alert, default=str, indent=2))
        await _log_delivery(alert_id, "storage_drive", str(out_dir / filename), "SENT")
        logger.info("Alert archived to %s", out_dir / filename)
    except Exception as e:
        await _log_delivery(alert_id, "storage_drive", storage_path, "FAILED", error=str(e)[:500])
        logger.exception("Storage delivery failed")


class AlertDeliveryWorker:
    def __init__(self):
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_ALERTS,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=GROUP_DELIVERY,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
        )
        await self._consumer.start()
        await self._producer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("AlertDeliveryWorker started — consuming %s (group=%s)", TOPIC_ALERTS, GROUP_DELIVERY)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
        if self._producer:
            await self._producer.stop()
        logger.info("AlertDeliveryWorker stopped")

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                try:
                    await self._dispatch(msg.value)
                except Exception:
                    logger.exception("Delivery dispatch failed: %s", str(msg.value)[:200])
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("AlertDeliveryWorker loop error")

    async def _dispatch(self, alert: dict):
        alert_id = alert.get("alert_id", str(uuid.uuid4()))

        channels_cfg = await _load_channel_config("delivery_channels")
        if not channels_cfg:
            logger.debug("No delivery channels configured — skipping")
            return

        tasks = []
        for ch_id, ch_state in channels_cfg.items():
            if not ch_state.get("enabled"):
                continue

            ch_config = await _load_channel_config(f"delivery_channel_{ch_id}") or {}

            if ch_id == "email" and settings.smtp_enabled:
                tasks.append(_deliver_email(alert, ch_config, alert_id))
            elif ch_id == "ip_speaker" and settings.ip_speaker_enabled:
                tasks.append(_deliver_ip_speaker(alert, ch_config, alert_id))
            elif ch_id == "sip_phone":
                tasks.append(_deliver_sip(alert, self._producer, alert_id))
            elif ch_id == "storage_drive":
                tasks.append(_deliver_storage(alert, ch_config, alert_id))
            elif ch_id == "whatsapp":
                logger.info("WhatsApp delivery not implemented — skipping alert %s", alert_id)
                await _log_delivery(alert_id, "whatsapp", "stub", "SKIPPED", error="Not implemented")
            elif ch_id == "telegram":
                logger.info("Telegram delivery not implemented via config — skipping alert %s", alert_id)
                await _log_delivery(alert_id, "telegram", "stub", "SKIPPED", error="Not implemented")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logger.info("Starting Alert Delivery Service")

    worker = AlertDeliveryWorker()
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)

    logger.info("Alert Delivery service ready — waiting for messages")
    try:
        await shutdown.wait()
    finally:
        await worker.stop()
        logger.info("Alert Delivery service stopped")


if __name__ == "__main__":
    asyncio.run(main())
