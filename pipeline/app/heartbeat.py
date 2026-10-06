# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Pipeline liveness heartbeat.

Two consumers, two mechanisms, deliberately:

1. **Docker healthcheck** reads a file. A file needs no network, no Redis
   client and no extra package in the 42 GB DeepStream image -- the check
   is a stat() and an arithmetic comparison. If Redis is the thing that is
   down, a Redis-based healthcheck would mark the pipeline unhealthy even
   though it is processing frames perfectly well.

2. **services/health_monitor** already reads `ww:pipeline:status`,
   `ww:pipeline:fps` and `ww:pipeline:active_sources` from Redis
   (health_monitor/main.py:555-557) -- but nothing in the pipeline ever
   wrote them, so those reads always returned None. This closes that gap.

Called from `health_check_cb`, which GLib already runs every 10 s, so
there is no new timer and no new thread.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Default lives under /tmp rather than the evidence volume: it is pure
# scratch, written every 10 s, and must stay writable even if the evidence
# mount fills up.
HEARTBEAT_PATH = Path(os.environ.get("WW_PIPELINE_HEARTBEAT", "/tmp/wastewatch/pipeline.heartbeat"))

_REDIS_STATUS_KEY = "ww:pipeline:status"
_REDIS_FPS_KEY = "ww:pipeline:fps"
_REDIS_SOURCES_KEY = "ww:pipeline:active_sources"
# Keys expire so a dead pipeline stops looking alive to health_monitor.
_REDIS_TTL_S = 60


def write_heartbeat(
    redis_client=None,
    *,
    active_sources: int = 0,
    fps: float = 0.0,
    state: str = "running",
) -> None:
    """Record liveness. Never raises -- a failed heartbeat must not kill the
    pipeline's housekeeping tick."""
    now = time.time()
    payload = {
        "ts": now,
        "state": state,
        "active_sources": active_sources,
        "fps": round(float(fps), 2),
        "pid": os.getpid(),
    }

    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never sees a half-written file.
        tmp = HEARTBEAT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(HEARTBEAT_PATH)
    except OSError as exc:
        logger.warning("Could not write heartbeat file %s: %s", HEARTBEAT_PATH, exc)

    if redis_client is None:
        return
    try:
        redis_client.set(_REDIS_STATUS_KEY, state, ex=_REDIS_TTL_S)
        redis_client.set(_REDIS_FPS_KEY, payload["fps"], ex=_REDIS_TTL_S)
        redis_client.set(_REDIS_SOURCES_KEY, active_sources, ex=_REDIS_TTL_S)
    except Exception as exc:  # redis client type varies; never fatal
        logger.debug("Heartbeat Redis publish failed: %s", exc)


def is_fresh(max_age_s: float = 45.0, path: Path | None = None) -> bool:
    """True if the heartbeat was written within *max_age_s*.

    45 s default against a 10 s write interval tolerates three missed
    ticks, so a single slow housekeeping pass does not flap the container
    between healthy and unhealthy.
    """
    p = path or HEARTBEAT_PATH
    try:
        data = json.loads(p.read_text())
        return (time.time() - float(data["ts"])) <= max_age_s
    except (OSError, ValueError, KeyError):
        return False


if __name__ == "__main__":
    # Entry point for the Docker healthcheck:
    #   CMD python3 -m app.heartbeat
    import sys
    sys.exit(0 if is_fresh() else 1)
