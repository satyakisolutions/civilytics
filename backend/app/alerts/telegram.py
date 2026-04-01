import logging
from typing import Any
import httpx
from app.config.settings import settings

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {
    "critical": "\u26a0\ufe0f",
    "high": "\U0001f534",
    "medium": "\U0001f7e1",
    "low": "\U0001f7e2",
    "info": "\u2139\ufe0f",
}

_TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


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
        url = _TELEGRAM_API.format(token=self._token, method="sendPhoto")
        resp = await self._client.post(
            url,
            json={
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": caption,
                "parse_mode": "HTML",
            },
        )
        resp.raise_for_status()
        return resp.json()

    def _format_message(
        self,
        camera_id: str,
        severity: str,
        message: str,
        dwell_time_s: float | None,
        vlm_summary: str | None,
        plate_number: str | None,
    ) -> str:
        icon = _SEVERITY_ICON.get(severity.lower(), "\u26a0\ufe0f")
        lines = [
            f"{icon} <b>WasteWatch Alert</b> [{severity.upper()}]",
            f"\U0001f4f7 Camera: <code>{camera_id}</code>",
            f"\U0001f4ac {message}",
        ]
        if dwell_time_s is not None:
            lines.append(f"\u23f1 Dwell time: <b>{dwell_time_s:.1f}s</b>")
        if vlm_summary:
            lines.append(f"\U0001f9e0 VLM: {vlm_summary}")
        if plate_number:
            lines.append(f"\U0001f697 Plate: <code>{plate_number}</code>")
        return "\n".join(lines)

    async def close(self):
        await self._client.aclose()
