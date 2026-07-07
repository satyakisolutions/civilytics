"""Synchronous gRPC client wrapping VLM analysis calls.

DeepStream pad probes run on GStreamer's streaming thread. Blocking that
thread for even a second is catastrophic — the buffer probe loop backs up
and the whole pipeline freezes. So this client's ``submit`` method hands
the RPC off to a small thread pool and returns immediately; the caller
does not await the result (the response lands on Kafka `vlm.results`
and the alert consumer handles it there).

On repeated failure the client short-circuits for 30 s so a dead worker
does not keep filling the thread pool with doomed calls.
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import grpc

from app.grpc_gen import vlm_pb2, vlm_pb2_grpc

logger = logging.getLogger(__name__)


class VLMGrpcClient:
    def __init__(
        self,
        target: str | None = None,
        workers: int = 4,
        timeout_s: float = 90.0,
    ) -> None:
        self._target = target or os.environ.get("WW_VLM_GRPC_TARGET", "vlm-worker:50061")
        self._timeout_s = timeout_s
        self._channel: grpc.Channel | None = None
        self._stub: vlm_pb2_grpc.VLMServiceStub | None = None
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vlm-grpc")
        self._consecutive_failures = 0
        self._breaker_until = 0.0
        self._max_consecutive = 5
        self._breaker_duration_s = 30.0

    def _ensure_channel(self) -> None:
        with self._lock:
            if self._channel is None:
                self._channel = grpc.insecure_channel(
                    self._target,
                    options=[
                        ("grpc.max_receive_message_length", 16 * 1024 * 1024),
                        ("grpc.max_send_message_length", 16 * 1024 * 1024),
                    ],
                )
                self._stub = vlm_pb2_grpc.VLMServiceStub(self._channel)
                logger.info("VLM gRPC channel opened: %s", self._target)

    def submit(
        self,
        camera_id: str,
        track_id: int,
        class_name: str,
        dwell_time_s: float,
        path_summary: str,
        trigger_bbox: dict | None = None,
        jpeg_bytes: bytes | None = None,
    ) -> None:
        """Fire-and-forget RPC. Returns immediately."""
        now = time.monotonic()
        if now < self._breaker_until:
            # Circuit open — drop silently (already emitted an alert when it tripped)
            return

        frames = [jpeg_bytes] if jpeg_bytes else []
        bbox = None
        if trigger_bbox:
            bbox = vlm_pb2.BoundingBox(
                x=float(trigger_bbox.get("x", 0)),
                y=float(trigger_bbox.get("y", 0)),
                w=float(trigger_bbox.get("w", 0)),
                h=float(trigger_bbox.get("h", 0)),
            )
        # DeepStream emits unsigned 64-bit track IDs that can exceed
        # int64 max (e.g. 1.1e19). Truncate to int63 so the signed
        # proto field accepts it; full id is preserved in track_session_id.
        _tid_full = int(track_id) if track_id is not None else 0
        _tid = _tid_full & 0x7FFFFFFFFFFFFFFF
        req = vlm_pb2.AnalyzeRequest(
            camera_id=camera_id,
            track_id=_tid,
            track_session_id=f"{camera_id}_{_tid_full}",
            zone_name=camera_id,
            dwell_time_s=float(dwell_time_s),
            class_name=class_name or "unknown",
            path_summary=path_summary or "",
            trigger_bbox=bbox,
            frames_jpeg=frames,
            frame_timestamps=[time.time()] if frames else [],
            request_id=f"{camera_id}-{track_id}-{int(time.time()*1000)}",
        )

        self._pool.submit(self._call, req)

    def _call(self, req: "vlm_pb2.AnalyzeRequest") -> None:
        try:
            self._ensure_channel()
            assert self._stub is not None
            self._stub.Analyze(req, timeout=self._timeout_s)
            with self._lock:
                self._consecutive_failures = 0
        except grpc.RpcError as exc:
            with self._lock:
                self._consecutive_failures += 1
                failures = self._consecutive_failures
                if failures >= self._max_consecutive:
                    self._breaker_until = time.monotonic() + self._breaker_duration_s
            logger.warning("VLM gRPC failure (%d consecutive): %s",
                           failures, exc.code() if hasattr(exc, "code") else exc)
            if failures >= self._max_consecutive:
                logger.error("VLM gRPC circuit opened for %.0fs after %d failures",
                             self._breaker_duration_s, failures)
        except Exception:
            logger.exception("VLM gRPC unexpected error")

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception:
                pass
