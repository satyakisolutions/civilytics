import logging
import time
import uuid
from datetime import datetime, timezone

from app.ws.alerts import broadcast_alert

logger = logging.getLogger(__name__)


async def push_alert_ws(
    camera_id: str,
    severity: str,
    message: str,
    track_id: int | None = None,
    dwell_time_s: float | None = None,
    vlm_summary: str | None = None,
    plate_number: str | None = None,
    snapshot_url: str | None = None,
    title: str | None = None,
    zone: str | None = None,
    detected_at: float | None = None,
):
    """Format and broadcast an alert to all connected WebSocket clients.

    Direct-alert path (pipeline.py's per-frame FSM alerts) has no DB row --
    this is the sole source of truth for those, so the payload carries full
    Alert-shaped fields (id/title/timestamp/status), not just the raw
    severity/message the caller passed in. Frontend renders this payload
    directly instead of resyncing from the DB-backed REST endpoint.
    """
    ts = detected_at if detected_at is not None else time.time()
    payload = {
        "type": "alert",
        "id": f"live-{uuid.uuid4()}",
        "title": title or f"{severity.title()} Alert",
        "status": "active",
        "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
        "camera_id": camera_id,
        "zone": zone,
        "severity": severity,
        "message": message,
        "track_id": track_id,
        "dwell_time_s": dwell_time_s,
        "vlm_summary": vlm_summary,
        "plate_number": plate_number,
        "snapshot_url": snapshot_url,
    }
    logger.debug("Broadcasting alert via WebSocket: camera=%s severity=%s", camera_id, severity)
    await broadcast_alert(payload)
