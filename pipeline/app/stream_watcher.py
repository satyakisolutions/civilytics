# pipeline/app/stream_watcher.py
"""Dynamic stream watcher — manages camera lifecycle with probing and auto-reconnect.

Responsibilities:
- Register camera sources (from env, API, or config)
- Probe RTSP streams before adding to pipeline (fail-fast if unreachable)
- Monitor active streams via CameraHealth
- Auto-reconnect downed streams with exponential backoff
- Re-add cameras that come back online after being down
"""
import logging
import socket
import time
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from app.health import CameraHealth
from app.source_manager import SourceManager

logger = logging.getLogger(__name__)


class StreamState(str, Enum):
    PENDING = "pending"          # Registered but not yet added
    PROBING = "probing"          # Checking if RTSP endpoint is reachable
    ACTIVE = "active"            # Streaming in pipeline
    DOWN = "down"                # Stream lost, awaiting reconnect
    RECONNECTING = "reconnecting"  # Attempting to reconnect
    FAILED = "failed"            # Exceeded max retries, manual intervention needed


@dataclass
class StreamEntry:
    camera_id: str
    uri: str
    state: StreamState = StreamState.PENDING
    retry_count: int = 0
    max_retries: int = 10
    last_attempt: float = 0.0
    backoff_s: float = 5.0       # Initial backoff (doubles on each retry)
    max_backoff_s: float = 300.0  # Cap at 5 minutes
    added_at: float = 0.0
    error: str = ""

    @property
    def next_retry_at(self) -> float:
        """Monotonic time when next retry is allowed."""
        backoff = min(self.backoff_s * (2 ** self.retry_count), self.max_backoff_s)
        return self.last_attempt + backoff

    @property
    def is_retryable(self) -> bool:
        return self.retry_count < self.max_retries


class StreamWatcher:
    """Manages dynamic stream addition, probing, and reconnection."""

    def __init__(
        self,
        source_manager: SourceManager,
        health: CameraHealth,
        poll_interval_s: float = 10.0,
        probe_timeout_s: float = 5.0,
    ):
        self._source_manager = source_manager
        self._health = health
        self._poll_interval_s = poll_interval_s
        self._probe_timeout_s = probe_timeout_s
        self._streams: dict[str, StreamEntry] = {}

    def register_source(self, camera_id: str, uri: str) -> StreamEntry:
        """Register a camera source for management.

        If the stream is reachable, it's added to the pipeline immediately.
        If not, it's queued for periodic retry.
        """
        entry = StreamEntry(camera_id=camera_id, uri=uri)
        self._streams[camera_id] = entry

        if self._probe_rtsp(uri):
            self._add_to_pipeline(entry)
        else:
            entry.state = StreamState.DOWN
            entry.last_attempt = time.monotonic()
            entry.error = "Initial probe failed — will retry"
            logger.warning(
                "Camera %s (%s) unreachable — queued for retry",
                camera_id, uri,
            )

        return entry

    def unregister_source(self, camera_id: str) -> None:
        """Remove a camera from management and pipeline."""
        entry = self._streams.pop(camera_id, None)
        if entry and entry.state == StreamState.ACTIVE:
            try:
                self._source_manager.remove_source(camera_id)
            except Exception:
                logger.exception("Error removing source %s", camera_id)
        logger.info("Unregistered camera %s", camera_id)

    def poll(self) -> dict[str, StreamState]:
        """Periodic poll — check health, attempt reconnections.

        Called from the GLib main loop every poll_interval_s seconds.
        Returns current state of all managed streams.
        """
        now = time.monotonic()

        # Check active streams for timeouts
        timed_out = self._health.check_health()
        for cam_id in timed_out:
            entry = self._streams.get(cam_id)
            if entry and entry.state == StreamState.ACTIVE:
                logger.warning("Camera %s timed out — marking as DOWN", cam_id)
                entry.state = StreamState.DOWN
                entry.last_attempt = now
                entry.retry_count = 0  # Reset for reconnection attempts
                try:
                    self._source_manager.remove_source(cam_id)
                except Exception:
                    logger.exception("Error removing timed-out source %s", cam_id)

        # Attempt reconnection for down/pending streams
        for entry in self._streams.values():
            if entry.state in (StreamState.DOWN, StreamState.PENDING):
                if not entry.is_retryable:
                    if entry.state != StreamState.FAILED:
                        entry.state = StreamState.FAILED
                        logger.error(
                            "Camera %s FAILED after %d retries — manual intervention required",
                            entry.camera_id, entry.max_retries,
                        )
                    continue

                if now < entry.next_retry_at:
                    continue  # Backoff not elapsed yet

                entry.state = StreamState.RECONNECTING
                entry.last_attempt = now

                backoff = min(entry.backoff_s * (2 ** entry.retry_count), entry.max_backoff_s)
                logger.info(
                    "Attempting reconnect for %s (retry %d/%d, backoff %.0fs)",
                    entry.camera_id, entry.retry_count + 1, entry.max_retries, backoff,
                )

                if self._probe_rtsp(entry.uri):
                    self._add_to_pipeline(entry)
                else:
                    entry.state = StreamState.DOWN
                    entry.retry_count += 1
                    entry.error = f"Probe failed (attempt {entry.retry_count})"

        return {cam_id: e.state for cam_id, e in self._streams.items()}

    def get_status(self) -> dict[str, dict]:
        """Get detailed status of all managed streams."""
        return {
            cam_id: {
                "state": entry.state.value,
                "uri": entry.uri,
                "retry_count": entry.retry_count,
                "error": entry.error,
                "added_at": entry.added_at,
            }
            for cam_id, entry in self._streams.items()
        }

    @property
    def active_count(self) -> int:
        return sum(1 for e in self._streams.values() if e.state == StreamState.ACTIVE)

    @property
    def down_count(self) -> int:
        return sum(1 for e in self._streams.values() if e.state in (StreamState.DOWN, StreamState.FAILED))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _probe_rtsp(self, uri: str) -> bool:
        """Quick TCP probe to check if an RTSP endpoint is reachable."""
        try:
            parsed = urlparse(uri)
            host = parsed.hostname or "localhost"
            port = parsed.port or 554
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._probe_timeout_s)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception as e:
            logger.debug("RTSP probe failed for %s: %s", uri, e)
            return False

    def _add_to_pipeline(self, entry: StreamEntry) -> None:
        """Add a stream to the DeepStream pipeline."""
        try:
            self._source_manager.add_source(entry.camera_id, entry.uri)
            entry.state = StreamState.ACTIVE
            entry.added_at = time.monotonic()
            entry.retry_count = 0
            entry.error = ""
            logger.info("Camera %s is now ACTIVE", entry.camera_id)
        except Exception as e:
            entry.state = StreamState.DOWN
            entry.retry_count += 1
            entry.error = str(e)
            logger.exception("Failed to add camera %s to pipeline", entry.camera_id)
