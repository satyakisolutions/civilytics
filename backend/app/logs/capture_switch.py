# backend/app/logs/capture_switch.py
"""On/off switch for browser console-log capture.

Capture is OFF by default and can only be turned on by an API call.

Why it is not simply always-on: every console line from every operator's
browser, over an 8-hour session, is six figures of rows per operator per
day. Those rows share `log_entries` with the server's own logs, so running
it permanently both grows the table without bound and buries server logs in
client noise. Capture is a diagnostic you switch on while chasing a bug.

Two properties this deliberately has:

  1. A TTL. An admin who turns capture on to reproduce something and then
     forgets is the normal case, not the exception, so "on" expires by
     itself. Staying on forever would have to be an explicit, repeated act.
  2. Fail-CLOSED. If Redis is unreachable the answer is "off". A transport
     fault must not be a way to start recording every operator's browser,
     and silent capture is worse than no capture.

Redis holds the flag rather than a settings row because the TTL is the
feature and because the switch must take effect across every API worker
without a restart.
"""
from __future__ import annotations

import logging

import redis.asyncio as redis

from app.config.settings import settings

logger = logging.getLogger(__name__)

_KEY = "ww:logs:client_capture"

# Longest a single capture window may run. An admin asking for more has to
# re-enable, which keeps "on" a deliberate state rather than a default.
MAX_TTL_S = 24 * 60 * 60
DEFAULT_TTL_S = 60 * 60

_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def is_enabled() -> bool:
    """True only while a capture window is open. Fails closed."""
    try:
        return await _redis().exists(_KEY) == 1
    except Exception:
        logger.warning("Capture switch unreadable; treating capture as OFF")
        return False


async def remaining_s() -> int:
    """Seconds left in the window, or 0 when capture is off."""
    try:
        ttl = await _redis().ttl(_KEY)
    except Exception:
        return 0
    return ttl if ttl and ttl > 0 else 0


async def enable(*, ttl_s: int, enabled_by: str) -> int:
    """Open a capture window. Returns the TTL actually applied."""
    ttl = max(60, min(int(ttl_s), MAX_TTL_S))
    await _redis().set(_KEY, enabled_by, ex=ttl)
    logger.warning(
        "Browser console capture ENABLED by %s for %ds -- operator console "
        "activity is now being recorded", enabled_by, ttl,
    )
    return ttl


async def disable(*, disabled_by: str) -> None:
    await _redis().delete(_KEY)
    logger.warning("Browser console capture DISABLED by %s", disabled_by)


async def enabled_by() -> str | None:
    try:
        return await _redis().get(_KEY)
    except Exception:
        return None
