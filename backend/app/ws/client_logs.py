# backend/app/ws/client_logs.py
"""Inbound WebSocket for browser console logs.

This endpoint INVERTS the direction of every other /ws/* route. The others
push server state to the browser; this one accepts a stream from it. That
difference is why it needs its own backpressure rule: a browser stuck in a
console.error loop would otherwise flood the API, and unlike a push socket
there is no natural limit on how fast a client can talk.

The rule here is DROP, never buffer. A per-connection token bucket rejects
envelopes over the rate and the socket is closed on sustained abuse. Logs
are diagnostics -- losing some beats an API that falls over while trying
not to.
"""
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.auth.ws_auth import authenticate_ws
from app.logs import capture_switch
from app.logs.client_ingest import ClientLogError, ingest_blob

logger = logging.getLogger(__name__)
router = APIRouter()

# Envelopes allowed per window, per connection. The client batches on a few
# seconds, so a well-behaved session sends well under this.
_MAX_ENVELOPES_PER_WINDOW = 20
_WINDOW_S = 10.0
# Consecutive rejections tolerated before the socket is closed. One bad
# batch is a bug; thirty in a row is a loop.
_MAX_STRIKES = 30


@router.websocket("/client-logs")
async def ws_client_logs(websocket: WebSocket):
    """Accept gzipped console-log envelopes from an authenticated browser.

    Any role may report its own console: a viewer's errors are exactly the
    ones nobody is watching a terminal for.
    """
    claims = await authenticate_ws(websocket, role="viewer")
    if claims is None:
        return

    session = claims.get("preferred_username") or claims.get("sub")

    # The shim stays dormant until told otherwise, so capture costs nothing
    # while the switch is off. State is re-sent on every reconnect, which is
    # also how a client finds out the window closed while it was away.
    await websocket.send_json({
        "type": "capture",
        "enabled": await capture_switch.is_enabled(),
        "remaining_s": await capture_switch.remaining_s(),
    })
    window_start = time.monotonic()
    in_window = 0
    strikes = 0
    stored = 0

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            blob = message.get("bytes")
            if blob is None:
                # Text frames are not accepted: the contract is a gzip blob,
                # and allowing an uncompressed path would bypass the
                # compressed-size cap.
                strikes += 1
                await websocket.send_json({"ok": False, "error": "expected a binary gzip frame"})
                if strikes >= _MAX_STRIKES:
                    break
                continue

            now = time.monotonic()
            if now - window_start >= _WINDOW_S:
                window_start = now
                in_window = 0
            in_window += 1
            if in_window > _MAX_ENVELOPES_PER_WINDOW:
                # Dropped on the floor. The client is told so it can back
                # off and count the loss itself.
                strikes += 1
                await websocket.send_json({"ok": False, "error": "rate limited", "retry_after_s": _WINDOW_S})
                if strikes >= _MAX_STRIKES:
                    logger.warning("Closing client-log socket for %s: sustained flooding", session)
                    break
                continue

            if not await capture_switch.is_enabled():
                # Window closed mid-session. Discard and tell the client to
                # go dormant rather than closing the socket, so it picks the
                # next window up without reconnecting.
                await websocket.send_json({"type": "capture", "enabled": False, "remaining_s": 0})
                continue

            try:
                written = await ingest_blob(blob, claims=claims)
            except ClientLogError as exc:
                strikes += 1
                await websocket.send_json({"ok": False, "error": str(exc)})
                if strikes >= _MAX_STRIKES:
                    logger.warning("Closing client-log socket for %s: repeated bad envelopes", session)
                    break
                continue
            except Exception:
                # A storage fault must not kill the operator's dashboard
                # session. Report and keep the socket open.
                logger.exception("Failed to store client logs for %s", session)
                await websocket.send_json({"ok": False, "error": "storage unavailable"})
                continue

            strikes = 0
            stored += written
            await websocket.send_json({"ok": True, "stored": written})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("client-log socket error for %s", session)
    finally:
        logger.info("client-log socket closed for %s after %d records", session, stored)
        try:
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
        except Exception:
            pass
