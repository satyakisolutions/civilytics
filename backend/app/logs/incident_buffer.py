# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""In-memory ring of recent log records, persisted only around an incident.

WHY THIS INSTEAD OF TURNING ON DEBUG
Running the stack at DEBUG to investigate a problem has two concrete costs
in this deployment:

  * Disk. Prod caps container logs at 50m x 5. The pipeline at
    GST_DEBUG=4 emits per-batch lines at 15 fps x 12 cameras, so the
    rotation window collapses to minutes and the logs you wanted are
    already gone.
  * Data. DEBUG paths log request payloads. POST /api/auth/login carries
    a plaintext password and GET /api/auth/ws-token returns a live bearer
    token. Those would land in container logs and in whatever ships them.

So DEBUG is captured CONTINUOUSLY into a bounded in-memory ring and
written out only when an incident says the surrounding window matters.
Steady state costs no disk and persists nothing.

Same bounding strategy as pipeline/app/ring_buffer.py's CameraRingBuffer:
evict on append so memory stays bounded regardless of log rate. Bounded by
BOTH age and count -- an exception storm can emit thousands of records
inside the age window, and age alone would not hold memory down.

REDACTION
Records are redacted on CAPTURE, not on flush. Redacting at flush time
would leave plaintext credentials sitting in process memory in between,
readable from a core dump or a debugger.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys whose values are replaced wholesale. Matched case-insensitively
# against both dict keys and `key=value` / `"key": "value"` text.
_SENSITIVE_KEYS = (
    "password", "passwd", "secret", "token", "access_token", "refresh_token",
    "authorization", "api_key", "apikey", "client_secret", "bot_token",
    "ww_access", "ww_refresh", "cookie", "set-cookie", "private_key",
)
_REDACTED = "***REDACTED***"

# `key=value`, `key: value`, `"key": "value"` -- covers f-string logging,
# JSON bodies and dict reprs without needing to parse any of them.
#
# Two details that were wrong in the first version and are load-bearing:
#
#  1. The key may be QUOTED (`"password": "x"` in a JSON body). Without
#     the optional quotes the pattern matched nothing in JSON at all --
#     which is exactly the shape POST /api/auth/login logs.
#  2. An unquoted value runs to the next structural delimiter, NOT to the
#     next space. `Authorization: Bearer eyJ...` has a space inside the
#     value; stopping at whitespace redacted the word "Bearer" and left
#     the token in the log.
#
# The value matcher is deliberately greedy to a delimiter. It can
# over-redact when two pairs share a line, which is the safe direction for
# a control whose job is to not leak credentials.
_KV_PATTERN = re.compile(
    # Group 1 captures the key INCLUDING any surrounding quotes. Capturing
    # only the bare word dropped them from the replacement, turning
    # `{"token": "x"}` into `{token: "***"}` -- still not valid JSON.
    r"(?i)([\"']?\b(?:" + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r")\b[\"']?)"
    r"(\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^,;}\n]+)"
)
# Bearer tokens also appear with no key name at all in header dumps.
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")


def _replace_value(m: re.Match[str]) -> str:
    """Swap the value for the placeholder, PRESERVING its quoting.

    Dropping the quotes would turn `{"token": "x"}` into
    `{"token": ***REDACTED***}`, which is no longer valid JSON -- and
    flush_incident round-trips its context through json.loads(redact(...)).
    """
    value = m.group(3)
    quote = value[0] if value[:1] in ('"', "'") else ""
    return f"{m.group(1)}{m.group(2)}{quote}{_REDACTED}{quote}"


def redact(text: str) -> str:
    """Replace credential-shaped values in *text*. Never raises."""
    try:
        # Bearer first: it is the more specific pattern, and running it
        # after _KV_PATTERN would leave a bare token behind whenever the
        # KV match had already consumed the "Authorization" key.
        out = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", text)
        return _KV_PATTERN.sub(_replace_value, out)
    except Exception:  # a pathological message must not break logging
        return "<redaction failed>"


@dataclass(frozen=True)
class BufferedRecord:
    timestamp: float
    level: str
    logger_name: str
    message: str
    module: str = ""
    line: int = 0
    exc_text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {
            "ts": round(self.timestamp, 3),
            "level": self.level,
            "logger": self.logger_name,
            "message": self.message,
        }
        if self.module:
            d["where"] = f"{self.module}:{self.line}"
        if self.exc_text:
            d["exception"] = self.exc_text
        return d


@dataclass
class IncidentBufferConfig:
    """max_age_s: how far back a flush can reach. 300 s covers the approach,
        the dwell and the alert fan-out for a dumping event.
    max_records: hard cap so an exception storm cannot exhaust memory.
        ~20k records at a few hundred bytes each is a handful of MB.
    capture_level: what enters the ring. DEBUG is the point of this class.
    output_dir: where flushed incidents land.
    max_incidents_on_disk: flushed files are pruned oldest-first, so an
        alert storm cannot fill the disk the way DEBUG logging would.
    """
    max_age_s: float = 300.0
    max_records: int = 20_000
    capture_level: int = logging.DEBUG
    output_dir: Path = field(default_factory=lambda: Path("/tmp/wastewatch/incidents"))
    max_incidents_on_disk: int = 200


class IncidentBuffer(logging.Handler):
    """A logging handler that remembers recent records instead of writing them.

    Attach to the root logger at DEBUG while leaving the console handler at
    INFO: the ring sees everything, the console stays readable.

        buf = IncidentBuffer()
        logging.getLogger().addHandler(buf)
        logging.getLogger().setLevel(logging.DEBUG)   # or the ring sees nothing
        for h in logging.getLogger().handlers:
            if h is not buf:
                h.setLevel(logging.INFO)
    """

    def __init__(self, config: IncidentBufferConfig | None = None) -> None:
        super().__init__(level=(config or IncidentBufferConfig()).capture_level)
        self._config = config or IncidentBufferConfig()
        self._records: deque[BufferedRecord] = deque()
        # emit() can be called from any thread; the GStreamer streaming
        # thread and the asyncio loop both log.
        self._lock = threading.Lock()
        self._dropped = 0

    # ── capture ───────────────────────────────────────────────────────
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact(record.getMessage())
            exc_text = None
            if record.exc_info:
                exc_text = redact(self.format(record).split("\n", 1)[-1])

            buffered = BufferedRecord(
                timestamp=record.created,
                level=record.levelname,
                logger_name=record.name,
                message=message,
                module=record.module or "",
                line=record.lineno or 0,
                exc_text=exc_text,
            )
            with self._lock:
                self._records.append(buffered)
                self._evict_locked(record.created)
        except Exception:
            # A logging handler must never raise -- it would break the call
            # site that was only trying to log something.
            self.handleError(record)

    def _evict_locked(self, now: float) -> None:
        cutoff = now - self._config.max_age_s
        while self._records and self._records[0].timestamp < cutoff:
            self._records.popleft()
        while len(self._records) > self._config.max_records:
            self._records.popleft()
            self._dropped += 1

    # ── read ──────────────────────────────────────────────────────────
    def records_in_window(self, start_s: float, end_s: float) -> list[BufferedRecord]:
        with self._lock:
            return [r for r in self._records if start_s <= r.timestamp <= end_s]

    def snapshot(self, lookback_s: float | None = None) -> list[BufferedRecord]:
        """Everything in the ring, or the last *lookback_s* seconds of it."""
        now = time.time()
        if lookback_s is None:
            with self._lock:
                return list(self._records)
        return self.records_in_window(now - lookback_s, now)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            oldest = self._records[0].timestamp if self._records else None
            return {
                "buffered": len(self._records),
                "dropped": self._dropped,
                "oldest_age_s": round(time.time() - oldest, 1) if oldest else 0.0,
                "max_age_s": self._config.max_age_s,
                "max_records": self._config.max_records,
            }

    # ── flush ─────────────────────────────────────────────────────────
    def flush_incident(
        self,
        incident_id: str,
        *,
        lookback_s: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> Path | None:
        """Write the recent window to a JSONL file. Returns the path, or None.

        Does NOT clear the ring: two incidents seconds apart should both get
        their surrounding context, and the age bound reclaims it anyway.
        """
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", incident_id)[:120]
        if not safe_id:
            logger.warning("Refusing to flush incident with an empty id")
            return None

        records = self.snapshot(lookback_s)
        if not records:
            logger.debug("Incident %s: nothing buffered to flush", safe_id)
            return None

        try:
            self._config.output_dir.mkdir(parents=True, exist_ok=True)
            out = self._config.output_dir / f"{int(time.time())}_{safe_id}.jsonl"
            # Write-then-rename so a reader never sees a partial file.
            tmp = out.with_suffix(".jsonl.tmp")
            with tmp.open("w") as fh:
                header = {
                    "incident_id": safe_id,
                    "flushed_at": time.time(),
                    "record_count": len(records),
                    "stats": self.stats(),
                }
                if context:
                    header["context"] = json.loads(redact(json.dumps(context)))
                fh.write(json.dumps(header) + "\n")
                for r in records:
                    fh.write(json.dumps(r.as_dict()) + "\n")
            tmp.replace(out)
            self._prune_old_incidents()
            logger.info("Incident %s: wrote %d records -> %s", safe_id, len(records), out)
            return out
        except OSError as exc:
            logger.warning("Incident %s: could not write flush file: %s", safe_id, exc)
            return None

    def _prune_old_incidents(self) -> None:
        """Keep at most max_incidents_on_disk files, oldest removed first."""
        try:
            files = sorted(
                self._config.output_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
            )
            excess = len(files) - self._config.max_incidents_on_disk
            for p in files[:max(0, excess)]:
                p.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Incident prune failed: %s", exc)
