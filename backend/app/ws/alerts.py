import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

# All connected alert subscribers
_subscribers: set[WebSocket] = set()


async def broadcast_alert(data: dict):
    """Push alert payload to all /ws/alerts subscribers."""
    dead = []
    for ws in list(_subscribers):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers.discard(ws)


@router.websocket("/alerts")
async def ws_alerts(websocket: WebSocket):
    await websocket.accept()
    _subscribers.add(websocket)
    logger.info("AlertWebSocket connected, total=%d", len(_subscribers))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(websocket)
        logger.info("AlertWebSocket disconnected, total=%d", len(_subscribers))
