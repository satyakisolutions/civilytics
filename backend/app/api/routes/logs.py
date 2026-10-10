# backend/app/api/routes/logs.py
"""Log search and the POST fallback for browser console logs.

Postgres is the only sink. The previous implementation searched Qdrant, but
Qdrant was dropped from the test and production stacks, so semantic search
had no backing store in either environment -- an endpoint that worked only
in dev. Plain filtered SQL over `log_entries` works everywhere.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.auth.middleware import get_current_user, require_admin
from app.db.engine import async_session
from app.db.models import LogEntry
from app.logs import capture_switch, client_ingest
from app.logs.client_ingest import ClientLogError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/system/logs", tags=["logs"])


@router.get("")
async def search_logs(
    q: str = Query(default="", description="Substring match on the message"),
    service: str | None = Query(default=None, description='e.g. "browser"'),
    level: str | None = None,
    session_id: str | None = Query(default=None, description="One browser session"),
    limit: int = Query(default=100, le=500),
    _user: dict = Depends(require_admin),
):
    """Newest first. Admin-only: logs carry usernames, URLs and user agents."""
    stmt = select(LogEntry).order_by(LogEntry.timestamp.desc()).limit(limit)
    if service:
        stmt = stmt.where(LogEntry.service == service)
    if level:
        stmt = stmt.where(LogEntry.level == level.lower())
    if q:
        # ILIKE, not a vector search. Bound the pattern so a caller cannot
        # force a scan with a pathological wildcard string.
        stmt = stmt.where(LogEntry.message.ilike(f"%{q[:200]}%"))
    if session_id:
        stmt = stmt.where(LogEntry.meta["session_id"].astext == session_id[:64])

    async with async_session() as session:
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "count": len(rows),
        "results": [
            {
                "id": str(row.id),
                "service": row.service,
                "level": row.level,
                "message": row.message,
                "metadata": row.meta,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in rows
        ],
    }


class LogCleanupRequest(BaseModel):
    before: str


@router.delete("/cleanup")
async def cleanup_logs(
    body: LogCleanupRequest,
    _user: dict = Depends(require_admin),
):
    from datetime import datetime
    cutoff = datetime.fromisoformat(body.before)
    async with async_session() as session:
        result = await session.execute(
            delete(LogEntry).where(LogEntry.timestamp < cutoff)
        )
        await session.commit()
    return {"deleted": result.rowcount}


class CaptureRequest(BaseModel):
    enabled: bool
    # Window length. Capped in capture_switch; a forgotten "on" expires.
    ttl_s: int = Field(default=capture_switch.DEFAULT_TTL_S, ge=60, le=capture_switch.MAX_TTL_S)


@router.get("/client/capture")
async def get_capture_state(_user: dict = Depends(require_admin)):
    """Whether browser console capture is currently recording."""
    enabled = await capture_switch.is_enabled()
    return {
        "enabled": enabled,
        "remaining_s": await capture_switch.remaining_s() if enabled else 0,
        "enabled_by": await capture_switch.enabled_by() if enabled else None,
    }


@router.post("/client/capture")
async def set_capture_state(
    body: CaptureRequest, user: dict = Depends(require_admin)
):
    """Turn browser console capture on or off.

    Admin-gated and logged on both edges: switching this on starts recording
    other people's browser activity, which is an action someone should be
    accountable for.
    """
    who = user.get("preferred_username") or user.get("sub") or "unknown"
    if body.enabled:
        ttl = await capture_switch.enable(ttl_s=body.ttl_s, enabled_by=who)
        return {"enabled": True, "remaining_s": ttl}
    await capture_switch.disable(disabled_by=who)
    return {"enabled": False, "remaining_s": 0}


@router.post("/client", status_code=status.HTTP_202_ACCEPTED)
async def ingest_client_logs(
    request: Request, user: dict = Depends(get_current_user)
):
    """Fallback ingest for when the WebSocket is down.

    Same body as the socket: one gzipped JSON envelope. Deliberately shares
    client_ingest with the WS path so the caps and redaction cannot diverge
    between transports.

    Any authenticated role may post its own console -- a viewer's errors are
    the ones nobody has a terminal open for. Authentication is still
    required: an open log sink is an unauthenticated write to the database.
    """
    # Enforced server side, not just in the shim: a browser tab left open
    # from an earlier capture window must not keep writing after the switch
    # is turned off.
    if not await capture_switch.is_enabled():
        return {"stored": 0, "capture_enabled": False}

    blob = await request.body()
    if not blob:
        raise HTTPException(status_code=400, detail="Empty body")
    try:
        written = await client_ingest.ingest_blob(blob, claims=user)
    except ClientLogError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Failed to store client logs via POST fallback")
        raise HTTPException(status_code=503, detail="Log storage unavailable")
    return {"stored": written}
