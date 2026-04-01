import logging
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
):
    """Format and broadcast an alert to all connected WebSocket clients."""
    payload = {
        "type": "alert",
        "camera_id": camera_id,
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
