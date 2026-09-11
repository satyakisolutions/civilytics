import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

_subscribers: set[WebSocket] = set()


async def broadcast_event(data: dict):
    """Push event to all /ws/events subscribers."""
    dead = []
    for ws in list(_subscribers):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers.discard(ws)


@router.websocket("/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _subscribers.add(websocket)
    logger.info("EventWebSocket connected, total=%d", len(_subscribers))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(websocket)
        logger.info("EventWebSocket disconnected, total=%d", len(_subscribers))
