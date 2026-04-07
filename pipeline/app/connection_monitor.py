# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Camera connection monitor -- health checking, reconnection, and status publishing.

Enhances the existing StreamWatcher with:
- Periodic RTSP probe health checks (TCP + RTSP OPTIONS)
- Connection state machine: CONNECTING -> ACTIVE -> DEGRADED -> DOWN -> RECONNECTING
- Kafka status publishing so backend/frontend track camera health
- Output stream lifecycle tied to input stream state
- Graceful degradation: when a camera goes down, its output stream is paused

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

import logging
import socket
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import urlparse

import orjson

from app.health import CameraHealth
from app.kafka_producer import PipelineKafkaProducer
from app.output_manager import OutputStreamManager
from app.source_manager import SourceManager

logger = logging.getLogger(__name__)


class ConnectionState(str, Enum):
    """State machine for camera connections."""

    CONNECTING = "connecting"
    ACTIVE = "active"
    DEGRADED = "degraded"       # frames arriving but with gaps
    DOWN = "down"               # no frames, awaiting reconnect
    RECONNECTING = "reconnecting"
    FAILED = "failed"           # exceeded max retries
    REMOVING = "removing"       # being cleaned up


@dataclass
class CameraConnection:
    """Tracks the full state of a single camera connection."""

    camera_id: str
    uri: str
    source_id: Optional[int] = None
    state: ConnectionState = ConnectionState.CONNECTING
    retry_count: int = 0
    max_retries: int = 10
    backoff_s: float = 5.0
    max_backoff_s: float = 300.0
    last_attempt: float = 0.0
    last_probe_latency_ms: float = 0.0
    last_state_change: float = 0.0
    error: str = ""

    @property
    def next_retry_at(self) -> float:
        """Monotonic time when the next retry is allowed."""
        backoff = min(self.backoff_s * (2 ** self.retry_count), self.max_backoff_s)
        return self.last_attempt + backoff

    @property
    def is_retryable(self) -> bool:
        return self.retry_count < self.max_retries


class ConnectionMonitor:
    """Manages camera lifecycle: probing, connection, health, and reconnection.

    Coordinates between SourceManager (input side) and OutputStreamManager
    (output side) to ensure each camera has both an input stream feeding
    the inference pipeline and a per-camera annotated output stream
    published to MediaMTX.

    Publishes camera status changes to Kafka so the backend and frontend
    can track camera health in real time.
    """

    def __init__(
        self,
        source_manager: SourceManager,
        output_manager: OutputStreamManager,
        health: CameraHealth,
        kafka_producer: PipelineKafkaProducer,
        poll_interval_s: float = 10.0,
    ) -> None:
        self._source_manager = source_manager
        self._output_manager = output_manager
        self._health = health
        self._kafka_producer = kafka_producer
        self._poll_interval_s = poll_interval_s
        self._connections: dict[str, CameraConnection] = {}
        self._probe_timeout_s: float = 5.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_camera(self, camera_id: str, uri: str) -> ConnectionState:
        """Register a new camera and attempt to bring it online.

        1. Probes the RTSP endpoint to check reachability.
        2. If reachable: adds input source + output branch, sets ACTIVE.
        3. If unreachable: sets DOWN, queues for periodic retry.

        Args:
            camera_id: Logical camera identifier (e.g. "cam-01").
            uri: Full RTSP URI for the camera stream.

        Returns:
            The initial ConnectionState after registration.
        """
        conn = CameraConnection(
            camera_id=camera_id,
            uri=uri,
            last_state_change=time.monotonic(),
        )
        self._connections[camera_id] = conn

        reachable, latency_ms = self._probe_rtsp_options(uri)
        conn.last_probe_latency_ms = latency_ms
        conn.last_attempt = time.monotonic()

        if reachable:
            self._bring_online(conn)
        else:
            conn.state = ConnectionState.DOWN
            conn.last_state_change = time.monotonic()
            conn.error = "Initial RTSP probe failed -- will retry"
            logger.warning(
                "Camera %s (%s) unreachable -- queued for retry", camera_id, uri
            )
            self._publish_status(camera_id, conn.state.value, conn.error)

        return conn.state

    def unregister_camera(self, camera_id: str) -> None:
        """Remove a camera completely from management and pipeline.

        Tears down both the input source and output branch, publishes
        an offline status to Kafka, and removes tracking state.
        """
        conn = self._connections.pop(camera_id, None)
        if conn is None:
            logger.warning("unregister_camera: unknown camera %s", camera_id)
            return

        conn.state = ConnectionState.REMOVING
        conn.last_state_change = time.monotonic()

        # Remove output branch first (depends on demux being fed)
        if conn.source_id is not None:
            self._output_manager.remove_output_branch(camera_id)

        # Remove input source
        if conn.state != ConnectionState.DOWN and conn.source_id is not None:
            try:
                self._source_manager.remove_source(camera_id)
            except Exception:
                logger.exception("Error removing source %s", camera_id)

        self._publish_status(camera_id, "offline")
        logger.info("Unregistered camera %s", camera_id)

    def poll(self) -> dict[str, str]:
        """Periodic health check and reconnection loop.

        Called from the GLib main loop. Checks CameraHealth for
        timeouts, attempts reconnections with exponential backoff,
        and publishes status changes to Kafka.

        Returns:
            Dict mapping camera_id to current state string.
        """
        now = time.monotonic()

        # --- Check for timed-out cameras ----------------------------------
        timed_out = self._health.check_health()
        for cam_id in timed_out:
            conn = self._connections.get(cam_id)
            if conn is None:
                continue

            if conn.state == ConnectionState.ACTIVE:
                # First timeout: mark as degraded
                logger.warning(
                    "Camera %s frame timeout -- marking DEGRADED", cam_id
                )
                conn.state = ConnectionState.DEGRADED
                conn.last_state_change = now
                self._publish_status(cam_id, conn.state.value, "Frame timeout")

            elif conn.state == ConnectionState.DEGRADED:
                # Already degraded and still timing out: go DOWN
                logger.warning(
                    "Camera %s still no frames -- marking DOWN, removing "
                    "from pipeline",
                    cam_id,
                )
                self._take_offline(conn)

        # --- Check degraded cameras for recovery --------------------------
        for conn in self._connections.values():
            if conn.state == ConnectionState.DEGRADED:
                # If CameraHealth shows this camera is healthy again
                # (it was NOT in the timed_out list), promote back to ACTIVE
                if conn.camera_id not in timed_out:
                    last_frame = self._health._last_frame.get(conn.camera_id, 0.0)
                    if last_frame > conn.last_state_change:
                        logger.info(
                            "Camera %s recovered -- marking ACTIVE",
                            conn.camera_id,
                        )
                        conn.state = ConnectionState.ACTIVE
                        conn.last_state_change = now
                        conn.error = ""
                        self._publish_status(conn.camera_id, conn.state.value)

        # --- Attempt reconnection for DOWN cameras ------------------------
        for conn in self._connections.values():
            if conn.state not in (ConnectionState.DOWN, ConnectionState.CONNECTING):
                continue

            if not conn.is_retryable:
                if conn.state != ConnectionState.FAILED:
                    conn.state = ConnectionState.FAILED
                    conn.last_state_change = now
                    logger.error(
                        "Camera %s FAILED after %d retries -- "
                        "manual intervention required",
                        conn.camera_id,
                        conn.max_retries,
                    )
                    self._publish_status(
                        conn.camera_id,
                        conn.state.value,
                        f"Exceeded {conn.max_retries} retries",
                    )
                continue

            if now < conn.next_retry_at:
                continue  # Backoff not elapsed

            conn.state = ConnectionState.RECONNECTING
            conn.last_state_change = now
            conn.last_attempt = now

            backoff = min(
                conn.backoff_s * (2 ** conn.retry_count), conn.max_backoff_s
            )
            logger.info(
                "Attempting reconnect for %s (retry %d/%d, backoff %.0fs)",
                conn.camera_id,
                conn.retry_count + 1,
                conn.max_retries,
                backoff,
            )

            reachable, latency_ms = self._probe_rtsp_options(conn.uri)
            conn.last_probe_latency_ms = latency_ms

            if reachable:
                self._bring_online(conn)
            else:
                conn.state = ConnectionState.DOWN
                conn.last_state_change = now
                conn.retry_count += 1
                conn.error = f"Probe failed (attempt {conn.retry_count})"
                self._publish_status(
                    conn.camera_id, conn.state.value, conn.error
                )

        return {
            cam_id: conn.state.value
            for cam_id, conn in self._connections.items()
        }

    def get_all_status(self) -> list[dict]:
        """Return complete status for all managed cameras.

        Each entry includes input/output URLs, health state, retry info,
        and probe latency.
        """
        result: list[dict] = []
        for cam_id, conn in self._connections.items():
            entry = {
                "camera_id": cam_id,
                "state": conn.state.value,
                "input_url": conn.uri,
                "output_url": self._output_manager.get_output_url(cam_id),
                "webrtc_url": self._output_manager.get_webrtc_url(cam_id),
                "source_id": conn.source_id,
                "rtp_port": (
                    self._output_manager._rtp_base_port + conn.source_id
                    if conn.source_id is not None
                    else None
                ),
                "latency_ms": conn.last_probe_latency_ms,
                "retry_count": conn.retry_count,
                "error": conn.error,
                "last_state_change": conn.last_state_change,
            }
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # RTSP probing
    # ------------------------------------------------------------------

    def _probe_rtsp_options(
        self, uri: str, timeout_s: float = 5.0
    ) -> tuple[bool, float]:
        """Enhanced RTSP probe that sends an actual OPTIONS request.

        Goes beyond a simple TCP connect by sending an RTSP OPTIONS
        request and verifying the server responds with 200 OK.  This
        catches cases where the TCP port is open but the RTSP server
        is not serving the requested stream.

        Args:
            uri: The full RTSP URI to probe.
            timeout_s: Socket timeout in seconds.

        Returns:
            Tuple of (success, latency_ms).  On failure latency_ms is 0.
        """
        parsed = urlparse(uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or 554

        start = time.monotonic()
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout_s)

            result = sock.connect_ex((host, port))
            if result != 0:
                return False, 0.0

            # Send RTSP OPTIONS request
            options_request = (
                f"OPTIONS {uri} RTSP/1.0\r\n"
                f"CSeq: 1\r\n"
                f"\r\n"
            )
            sock.sendall(options_request.encode("utf-8"))

            # Read response (up to 4 KB is more than enough for OPTIONS)
            response = b""
            try:
                response = sock.recv(4096)
            except socket.timeout:
                logger.debug(
                    "RTSP OPTIONS timeout for %s (got TCP connect but no response)",
                    uri,
                )
                # TCP connected but no RTSP response — still partially reachable
                latency_ms = (time.monotonic() - start) * 1000
                return False, latency_ms

            latency_ms = (time.monotonic() - start) * 1000

            # Check for valid RTSP response
            response_str = response.decode("utf-8", errors="replace")
            if "RTSP/1.0 200" in response_str:
                logger.debug(
                    "RTSP probe OK for %s (%.1fms)", uri, latency_ms
                )
                return True, latency_ms

            # Some servers return other codes (e.g. 401 Unauthorized) which
            # still indicate the server is alive and serving RTSP.
            if response_str.startswith("RTSP/1.0"):
                logger.debug(
                    "RTSP probe got non-200 response for %s: %s (%.1fms)",
                    uri,
                    response_str.split("\r\n")[0],
                    latency_ms,
                )
                # Server is alive, even if auth is needed
                return True, latency_ms

            logger.debug(
                "RTSP probe: unexpected response from %s: %r",
                uri,
                response_str[:200],
            )
            return False, latency_ms

        except Exception as exc:
            logger.debug("RTSP probe failed for %s: %s", uri, exc)
            return False, 0.0

        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bring_online(self, conn: CameraConnection) -> None:
        """Add both input source and output branch for a camera.

        On success sets state to ACTIVE and publishes status.
        On failure sets state to DOWN for retry.
        """
        try:
            # Add input source (returns the pad/source_id)
            source_id = self._source_manager.add_source(conn.camera_id, conn.uri)
            conn.source_id = source_id

            # Add per-camera output branch
            ok = self._output_manager.add_output_branch(conn.camera_id, source_id)
            if not ok:
                logger.warning(
                    "Output branch failed for %s -- input stream will still "
                    "be processed but no per-camera RTSP output",
                    conn.camera_id,
                )

            conn.state = ConnectionState.ACTIVE
            conn.last_state_change = time.monotonic()
            conn.retry_count = 0
            conn.error = ""

            logger.info(
                "Camera %s is now ACTIVE (source_id=%d, output=%s)",
                conn.camera_id,
                source_id,
                self._output_manager.get_output_url(conn.camera_id),
            )
            self._publish_status(conn.camera_id, conn.state.value)

        except Exception as exc:
            conn.state = ConnectionState.DOWN
            conn.last_state_change = time.monotonic()
            conn.retry_count += 1
            conn.error = str(exc)
            logger.exception(
                "Failed to bring camera %s online", conn.camera_id
            )
            self._publish_status(conn.camera_id, conn.state.value, conn.error)

    def _take_offline(self, conn: CameraConnection) -> None:
        """Remove a camera from the pipeline and mark it DOWN for retry."""
        # Remove output branch
        self._output_manager.remove_output_branch(conn.camera_id)

        # Remove input source
        try:
            self._source_manager.remove_source(conn.camera_id)
        except Exception:
            logger.exception(
                "Error removing source %s during take_offline",
                conn.camera_id,
            )

        conn.state = ConnectionState.DOWN
        conn.last_state_change = time.monotonic()
        conn.last_attempt = time.monotonic()
        conn.retry_count = 0  # Reset for reconnection
        conn.error = "Stream went down -- attempting reconnection"

        self._publish_status(conn.camera_id, conn.state.value, conn.error)

    def _publish_status(
        self,
        camera_id: str,
        state: str,
        error: str = "",
    ) -> None:
        """Publish camera status to Kafka topic wastewatch.camera.status.

        The backend and frontend subscribe to this topic to track camera
        health in real time and update the dashboard accordingly.
        """
        conn = self._connections.get(camera_id)

        message = {
            "camera_id": camera_id,
            "state": state,
            "input_url": conn.uri if conn else "",
            "output_url": self._output_manager.get_output_url(camera_id),
            "webrtc_url": self._output_manager.get_webrtc_url(camera_id),
            "latency_ms": conn.last_probe_latency_ms if conn else 0.0,
            "retry_count": conn.retry_count if conn else 0,
            "error": error,
            "timestamp": time.time(),
        }

        try:
            self._kafka_producer._producer.produce(
                topic="wastewatch.camera.status",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(message),
            )
            self._kafka_producer.poll()
        except Exception:
            logger.exception(
                "Failed to publish status for camera %s", camera_id
            )
