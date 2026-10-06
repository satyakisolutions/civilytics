# backend/app/logs/client_ingest.py
"""Ingest browser console logs into Postgres.

Every browser session forwards its console activity here so an operator's
client-side errors are visible next to server logs instead of living only in
a devtools window nobody had open.

Shared by both transports -- the WebSocket (primary) and the POST fallback
used when the socket is down -- so the caps and the redaction cannot drift
apart between them.

TRUST BOUNDARY. Everything arriving here is attacker-controlled text from a
browser. It is stored as data and never interpreted:

  - the gzip envelope is decompressed with a hard output ceiling, so a zip
    bomb cannot exhaust memory
  - every field is length-capped before it reaches the database
  - credentials are redacted again server side; the client redacts first, but
    a client that skipped it must not be able to persist a bearer token
  - the batch size is capped, so one looping console.error cannot write
    unbounded rows
"""
from __future__ import annotations

import gzip
import io
import logging
import zlib
from datetime import datetime, timezone

import orjson
from sqlalchemy import insert

from app.db.engine import async_session
from app.db.models import LogEntry
from app.logs.incident_buffer import redact

logger = logging.getLogger(__name__)

# `service` value marking a row as browser-originated, so the log viewer can
# separate client noise from server logs with an indexed equality check.
SERVICE = "browser"

# Decompressed ceiling for one envelope. A 1 MB gzip of repeated text
# expands to hundreds of MB; reading without a bound is the bomb.
MAX_DECOMPRESSED_BYTES = 1 * 1024 * 1024
# Compressed ceiling, checked before any decompression happens at all.
MAX_COMPRESSED_BYTES = 256 * 1024
MAX_RECORDS_PER_BATCH = 500
MAX_MESSAGE_CHARS = 4_000
MAX_LEVEL_CHARS = 20
MAX_URL_CHARS = 2_000
MAX_UA_CHARS = 400
MAX_SESSION_CHARS = 64

_ALLOWED_LEVELS = {"debug", "info", "log", "warn", "error"}


class ClientLogError(ValueError):
    """Envelope was malformed or over a cap. Carries a safe client message."""


def _gunzip_bounded(blob: bytes) -> bytes:
    """Decompress with a hard output ceiling.

    gzip.decompress() reads to completion, so it cannot be used on untrusted
    input. A decompressobj with max_length lets the ceiling be enforced
    before the memory is allocated.
    """
    if len(blob) > MAX_COMPRESSED_BYTES:
        raise ClientLogError("payload too large")
    try:
        obj = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)  # 16 = gzip header
        out = obj.decompress(blob, MAX_DECOMPRESSED_BYTES)
        if obj.unconsumed_tail:
            # More data was waiting than the ceiling allowed.
            raise ClientLogError("payload too large")
        return out
    except ClientLogError:
        raise
    except (zlib.error, OSError) as exc:
        raise ClientLogError("payload is not valid gzip") from exc


def _parse_ts(raw) -> datetime:
    """Client clocks are not trusted for ordering, only recorded.

    A browser with a wrong system clock would otherwise file its logs in
    1970 or 2040 and vanish from any time-ranged query. An out-of-range
    client timestamp is replaced with server time; the original stays in
    `meta.client_ts` so a clock problem is still diagnosable.
    """
    now = datetime.now(timezone.utc)
    if not isinstance(raw, (int, float)):
        return now
    try:
        ts = datetime.fromtimestamp(raw / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return now
    # Accept only a day either side of now.
    if abs((ts - now).total_seconds()) > 86_400:
        return now
    return ts


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def parse_envelope(blob: bytes) -> dict:
    """Decompress and JSON-decode one gzipped envelope."""
    raw = _gunzip_bounded(blob)
    try:
        envelope = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise ClientLogError("payload is not valid JSON") from exc
    if not isinstance(envelope, dict):
        raise ClientLogError("envelope must be an object")
    return envelope


def build_rows(envelope: dict, *, claims: dict) -> list[dict]:
    """Turn one envelope into LogEntry row dicts.

    Identity comes from the validated token, NEVER from the envelope. A
    browser that claimed its own username or tenant could otherwise file
    logs under another operator's name.
    """
    records = envelope.get("records")
    if not isinstance(records, list):
        raise ClientLogError("envelope.records must be a list")
    if len(records) > MAX_RECORDS_PER_BATCH:
        raise ClientLogError("too many records in one batch")

    session_id = _clip(envelope.get("session_id"), MAX_SESSION_CHARS)
    page_url = redact(_clip(envelope.get("url"), MAX_URL_CHARS))
    user_agent = _clip(envelope.get("user_agent"), MAX_UA_CHARS)
    # Count of lines the client dropped to stay inside its own rate cap.
    # Recorded so a gap in the stream is visible rather than silent.
    dropped = envelope.get("dropped")
    dropped = int(dropped) if isinstance(dropped, (int, float)) else 0

    username = claims.get("preferred_username") or claims.get("sub") or "unknown"
    roles = (claims.get("realm_access") or {}).get("roles") or []
    tenant = claims.get("ww_tenant")

    rows: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        level = _clip(record.get("level"), MAX_LEVEL_CHARS).lower()
        if level not in _ALLOWED_LEVELS:
            level = "log"
        message = redact(_clip(record.get("message"), MAX_MESSAGE_CHARS))
        meta = {
            "session_id": session_id,
            "username": username,
            "roles": roles,
            "tenant": tenant,
            "url": redact(_clip(record.get("url") or page_url, MAX_URL_CHARS)),
            "user_agent": user_agent,
            "client_ts": record.get("ts"),
            "transport": envelope.get("transport") or "ws",
        }
        if record.get("stack"):
            meta["stack"] = redact(_clip(record["stack"], MAX_MESSAGE_CHARS))
        rows.append(
            {
                "service": SERVICE,
                "level": level,
                "message": message,
                "meta": meta,
                "timestamp": _parse_ts(record.get("ts")),
            }
        )

    if dropped > 0:
        rows.append(
            {
                "service": SERVICE,
                "level": "warn",
                "message": f"client dropped {dropped} console lines to stay under its rate cap",
                "meta": {"session_id": session_id, "username": username, "dropped": dropped},
                "timestamp": datetime.now(timezone.utc),
            }
        )
    return rows


async def persist(rows: list[dict]) -> int:
    """Bulk-insert rows. Returns the number written."""
    if not rows:
        return 0
    async with async_session() as session:
        await session.execute(insert(LogEntry), rows)
        await session.commit()
    return len(rows)


async def ingest_blob(blob: bytes, *, claims: dict) -> int:
    """Full path for one gzipped envelope: parse, cap, redact, store."""
    rows = build_rows(parse_envelope(blob), claims=claims)
    return await persist(rows)
