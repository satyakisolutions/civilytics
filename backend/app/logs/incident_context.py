# backend/app/logs/incident_context.py
"""Point-in-time system snapshot attached to an incident flush.

Logs alone do not answer the first question anyone asks about an incident:
"was the stream even up?" A DEBUG ring full of inference lines looks
identical whether MediaMTX had twelve publishers or none. This collects the
two things that make the log readable:

  - MediaMTX paths: which inputs are publishing and which outputs have
    readers, right now
  - a whitelisted slice of Redis: pipeline liveness, per-camera FPS and
    last-frame timestamps, health rollups

Three rules it follows, each for a reason:

  1. FAIL SOFT. A collector that cannot reach its dependency records *that*
     as the finding and returns. "MediaMTX unreachable" is often the whole
     diagnosis, and an exception here would lose the log buffer the snapshot
     was meant to annotate.
  2. BOUNDED. Short timeouts and a key cap. An incident flush happens while
     something is already wrong, which is exactly when a dependency hangs;
     blocking there would turn a logging call into an outage.
  3. WHITELISTED, and SCAN not KEYS. Dumping every key would be unbounded,
     would sweep in values we have no business storing in a log file, and
     `KEYS *` blocks the Redis server for the whole scan.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import redis.asyncio as redis

from app.config.settings import settings
from app.logs.incident_buffer import redact

logger = logging.getLogger(__name__)

# Key patterns worth capturing, as SCAN MATCH globs. Deliberately narrow:
# these are the keys that explain a pipeline or camera fault. Anything
# holding a credential or a payload is absent by construction.
REDIS_PATTERNS = (
    "ww:pipeline:*",        # status, fps, active_sources -- pipeline liveness
    "ww:health:*",          # per-component and overall health rollups
    "ww:camera:*:fps",      # per-camera throughput
    "ww:camera:*:last_frame_ts",  # how stale each camera is
    "ww:cameras:list",
    "ww:alerts:active",
    "ww:logs:client_capture",
    "ww:healing:last",
    "ww:lpr:last_active",
)

MAX_KEYS = 400
MAX_VALUE_CHARS = 2_000
_SCAN_COUNT = 200
_MEDIAMTX_TIMEOUT_S = 3.0
_REDIS_TIMEOUT_S = 3.0


async def collect_mediamtx() -> dict:
    """Online input/output streams as MediaMTX itself reports them.

    `ready` is the input side -- a path with a live publisher. `readers` is
    the output side; a ready path with zero readers means the pipeline is
    publishing and nothing is watching, which is a different fault from a
    path that is not ready at all.
    """
    url = f"{settings.mediamtx_api_url}/v3/paths/list"
    try:
        async with httpx.AsyncClient(timeout=_MEDIAMTX_TIMEOUT_S) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        # The failure IS the finding. Record it and move on.
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}", "paths": []}

    items = payload.get("items") or []
    paths = []
    for item in items:
        readers = item.get("readers") or []
        source = item.get("source") or {}
        paths.append(
            {
                "name": item.get("name"),
                "ready": bool(item.get("ready")),
                "input_type": source.get("type"),
                "tracks": item.get("tracks") or [],
                "bytes_received": item.get("bytesReceived"),
                "bytes_sent": item.get("bytesSent"),
                "reader_count": len(readers),
                "reader_types": sorted({r.get("type") for r in readers if r.get("type")}),
            }
        )

    ready = [p for p in paths if p["ready"]]
    return {
        "reachable": True,
        "path_count": len(paths),
        "inputs_online": len(ready),
        "inputs_offline": len(paths) - len(ready),
        # The interesting case: publishing with nobody watching.
        "ready_without_readers": [p["name"] for p in ready if p["reader_count"] == 0],
        "paths": paths,
    }


async def collect_redis() -> dict:
    """Whitelisted Redis keys and their values."""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    out: dict[str, str] = {}
    truncated = False
    try:
        async with asyncio.timeout(_REDIS_TIMEOUT_S):
            for pattern in REDIS_PATTERNS:
                async for key in client.scan_iter(match=pattern, count=_SCAN_COUNT):
                    if len(out) >= MAX_KEYS:
                        truncated = True
                        break
                    try:
                        kind = await client.type(key)
                        if kind == "string":
                            raw = await client.get(key)
                        elif kind == "list":
                            raw = await client.lrange(key, 0, 20)
                        elif kind == "set":
                            raw = sorted(await client.smembers(key))[:20]
                        elif kind == "hash":
                            raw = await client.hgetall(key)
                        else:
                            raw = f"<{kind}>"
                    except Exception as exc:
                        raw = f"<unreadable: {type(exc).__name__}>"
                    # Redacted even though the whitelist should hold nothing
                    # sensitive -- the snapshot is written to a file that
                    # leaves the host.
                    out[key] = redact(str(raw))[:MAX_VALUE_CHARS]
                if truncated:
                    break
    except Exception as exc:
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}", "keys": {}}
    finally:
        try:
            await client.aclose()
        except Exception:
            pass

    return {
        "reachable": True,
        "key_count": len(out),
        "truncated": truncated,
        "keys": dict(sorted(out.items())),
    }


async def collect() -> dict:
    """Both collectors, concurrently. Never raises."""
    try:
        mediamtx, redis_state = await asyncio.gather(
            collect_mediamtx(), collect_redis(), return_exceptions=True
        )
    except Exception as exc:  # pragma: no cover -- gather with return_exceptions
        return {"error": f"snapshot failed: {type(exc).__name__}: {exc}"}

    def _unwrap(value, label):
        if isinstance(value, BaseException):
            return {"reachable": False, "error": f"{type(value).__name__}: {value}"}
        return value

    return {
        "mediamtx": _unwrap(mediamtx, "mediamtx"),
        "redis": _unwrap(redis_state, "redis"),
    }


async def flush_with_snapshot(
    buffer,
    incident_id: str,
    *,
    lookback_s: float | None = None,
    extra: dict | None = None,
):
    """Collect the snapshot, then flush the DEBUG ring with it attached.

    Order matters: the snapshot is taken BEFORE the flush so it describes
    the system at incident time rather than after whatever recovery the
    flush itself raced with.

    `IncidentBuffer.flush_incident` is synchronous and redacts its context,
    so this only has to supply the dict.
    """
    context = {"snapshot": await collect()}
    if extra:
        context.update(extra)
    return buffer.flush_incident(incident_id, lookback_s=lookback_s, context=context)
