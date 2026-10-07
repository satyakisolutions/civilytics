import logging
import os
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
import httpx
from app.config.settings import settings

logger = logging.getLogger(__name__)

# alert-svc, api, and the browser sit on different reachability domains:
# the browser can load a relative `/evidence/...` URL because it's on the
# same LAN as the api container's exposed port, but Telegram's own servers
# (which fetch `photo` URLs themselves when given a bare string) cannot
# reach a private/LAN-only host at all. So instead of handing Telegram a
# URL, fetch the bytes ourselves over the docker network (api container is
# reachable as `api:8080` from here) and upload them as multipart form
# data -- works regardless of whether this deployment is ever
# internet-reachable.
_API_BASE = os.environ.get("WW_API_BASE", "http://api:8080")

_SEVERITY_ICON = {
    "critical": "\u26a0\ufe0f",
    "high": "\U0001f534",
    "medium": "\U0001f7e1",
    "low": "\U0001f7e2",
    "info": "\u2139\ufe0f",
}

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
_IST = ZoneInfo("Asia/Kolkata")


class TelegramAlert:
    def __init__(self, bot_token: str | None = None, chat_ids: list[str] | None = None):
        self._token = bot_token or settings.telegram_bot_token
        self._chat_ids = chat_ids if chat_ids is not None else settings.telegram_chat_ids
        self._client = httpx.AsyncClient(timeout=10.0)

    async def send_alert(
        self,
        camera_id: str,
        severity: str,
        message: str,
        snapshot_url: str | None = None,
        dwell_time_s: float | None = None,
        vlm_summary: str | None = None,
        plate_number: str | None = None,
        camera_name: str | None = None,
        zone: str | None = None,
        location: str | None = None,
        detected_at: float | None = None,
    ) -> list[dict[str, Any]]:
        """Send alert to all configured Telegram chat IDs. Returns list of results."""
        if not self._token or not self._chat_ids:
            logger.warning("Telegram not configured — skipping alert for camera=%s", camera_id)
            return []

        text = self._format_message(
            camera_id=camera_id,
            severity=severity,
            message=message,
            dwell_time_s=dwell_time_s,
            vlm_summary=vlm_summary,
            plate_number=plate_number,
            camera_name=camera_name,
            zone=zone,
            location=location,
            detected_at=detected_at if detected_at is not None else time.time(),
        )

        results = []
        for chat_id in self._chat_ids:
            try:
                if snapshot_url:
                    result = await self._send_photo(chat_id, snapshot_url, caption=text)
                else:
                    result = await self._send_message(chat_id, text)
                results.append(result)
            except Exception as exc:
                logger.error(
                    "Failed to send Telegram alert to chat_id=%s: %s", chat_id, exc
                )
                results.append({"ok": False, "error": str(exc)})
        return results

    async def _send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        url = _TELEGRAM_API.format(token=self._token, method="sendMessage")
        resp = await self._client.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
        resp.raise_for_status()
        return resp.json()

    async def _send_photo(self, chat_id: str, photo_url: str, caption: str) -> dict[str, Any]:
        image_bytes = await self._fetch_evidence_image(photo_url)
        if image_bytes is None:
            # Fall back to a text-only alert rather than dropping the
            # message entirely -- a Telegram alert with no photo still
            # beats no alert at all.
            logger.warning("Could not fetch evidence image %s — sending text-only", photo_url)
            return await self._send_message(chat_id, caption)

        url = _TELEGRAM_API.format(token=self._token, method="sendPhoto")
        resp = await self._client.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("evidence.jpg", image_bytes, "image/jpeg")},
        )
        resp.raise_for_status()
        return resp.json()

    async def _fetch_evidence_image(self, photo_url: str) -> bytes | None:
        # photo_url is a relative path like "/evidence/alerts/<cam>/<file>.jpg"
        # (see pipeline/app/pipeline.py::_save_alert_snapshot); resolve it
        # against the api container's docker-network address, not the
        # browser-facing LAN one.
        full_url = photo_url if photo_url.startswith("http") else f"{_API_BASE}{photo_url}"
        try:
            resp = await self._client.get(full_url)
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.exception("Failed to fetch evidence image %s", full_url)
            return None

    def _format_message(
        self,
        camera_id: str,
        severity: str,
        message: str,
        dwell_time_s: float | None,
        vlm_summary: str | None,
        plate_number: str | None,
        camera_name: str | None = None,
        zone: str | None = None,
        location: str | None = None,
        detected_at: float | None = None,
    ) -> str:
        icon = _SEVERITY_ICON.get(severity.lower(), "\u26a0\ufe0f")
        # Zone/Location/Name identify a physical spot to whoever reads
        # this on their phone; the raw camera_id (a DB UUID) does not.
        # Fall back to it only when camera lookup failed.
        _loc_parts = [p for p in (zone, location, camera_name) if p]
        camera_line = " / ".join(_loc_parts) if _loc_parts else camera_id
        lines = [
            f"{icon} <b>Civilytics Alert</b> [{severity.upper()}]",
            f"\U0001f4f7 Camera: <code>{camera_line}</code>",
            f"\U0001f4ac {message}",
        ]
        # Dwell remains part of the event payload for detection/FSM logic,
        # but operators need the exact occurrence time in the notification.
        # Always render the event epoch in IST with second precision.
        if detected_at is not None:
            event_time_ist = datetime.fromtimestamp(detected_at, tz=_IST)
            lines.append(f"\U0001f552 Time: <b>{event_time_ist:%d-%m-%Y %H:%M:%S} IST</b>")
        if vlm_summary:
            lines.append(f"\U0001f9e0 VLM: {vlm_summary}")
        if plate_number:
            lines.append(f"\U0001f697 Plate: <code>{plate_number}</code>")
        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()
