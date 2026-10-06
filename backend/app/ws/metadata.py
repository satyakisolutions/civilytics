import asyncio
import logging
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.ws_auth import authenticate_ws

logger = logging.getLogger(__name__)
router = APIRouter()

# camera_id -> set of connected WebSockets
_subscribers: dict[str, set[WebSocket]] = defaultdict(set)


async def broadcast_to_camera(camera_id: str, data: dict):
    """Push metadata to all subscribers of a camera."""
    dead = []
    for ws in _subscribers.get(camera_id, set()):
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers[camera_id].discard(ws)


async def broadcast_to_all(data: dict):
    """Broadcast to camera-specific subscribers and __all__ subscribers."""
    camera_id = data.get("camera_id", "")
    await broadcast_to_camera(camera_id, data)
    await broadcast_to_camera("__all__", data)


@router.websocket("/metadata/{camera_id}")
async def ws_metadata(websocket: WebSocket, camera_id: str):
    # SECURITY: validate before accept() -- this stream carries live
    # detection metadata per camera and was previously open to any peer.
    if await authenticate_ws(websocket, role="viewer") is None:
        return
    _subscribers[camera_id].add(websocket)
    logger.info(
        "WebSocket connected: camera_id=%s, total=%d",
        camera_id,
        len(_subscribers[camera_id]),
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers[camera_id].discard(websocket)
        logger.info("WebSocket disconnected: camera_id=%s", camera_id)
