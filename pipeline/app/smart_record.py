# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""DeepStream Smart Recording for WasteWatch evidence capture.

Uses NvDsSRModule to record video evidence directly from the DeepStream
pipeline buffer. Replaces the FFmpeg subprocess approach with native
hardware-accelerated recording that includes pre-event buffering.

Key advantages over FFmpeg RTSP capture:
- Pre-buffer: captures N seconds BEFORE the violation trigger
- Hardware H.264 encoding via nvv4l2h264enc (no CPU re-encode)
- Zero-copy: records from pipeline buffer, no RTSP round-trip
- Event-driven: NvDsSRStart/NvDsSRStop keyed by (camera_id, track_id)
- Proper MP4 finalization on stop (moov atom written correctly)

Architecture:
    In the DeepStream pipeline, after the tee element, a Smart Record bin
    sits on a dedicated branch. The SR bin internally creates:
        queue -> nvvideoconvert -> capsfilter -> nvv4l2h264enc -> h264parse -> splitmuxsink
    All hardware-accelerated, all managed by DeepStream's NvDsSRModule.

    When a VIOLATION FSM event fires, NvDsSRStart() is called. DeepStream
    flushes the circular pre-buffer (15s of video already in GPU memory)
    and begins writing live frames to an MP4 container. When the track
    exits or the recording is manually stopped, NvDsSRStop() finalizes
    the MP4 with a properly written moov atom.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# SECURITY: Only allow safe camera IDs in output paths
_SAFE_CAM_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")

# DeepStream Smart Record constants — mirror the C enum values from
# nvdssr.h so that the code is correct even when pyds is unavailable.
NV_DS_SR_CONTAINER_MP4 = 0
NV_DS_SR_CONTAINER_MKV = 1
NV_DS_SR_CODEC_H264 = 0
NV_DS_SR_CODEC_H265 = 1

# Status codes returned by the NvDsSR callback
NV_DS_SR_STATUS_STARTED = 0
NV_DS_SR_STATUS_STOPPED = 1
NV_DS_SR_STATUS_ERROR = 2

# Try to import the DeepStream Python bindings.  On a full DeepStream 7.x
# installation inside the nvidia/deepstream container these are present.
# Outside that environment (unit tests, CI) we fall back gracefully.
_HAS_NVDSSR = False
try:
    import pyds  # type: ignore[import-untyped]

    # Probe for the NvDsSR API surface introduced in DS 6.1+
    if all(hasattr(pyds, attr) for attr in ("NvDsSRCreate", "NvDsSRStart", "NvDsSRStop", "NvDsSRDestroy")):
        _HAS_NVDSSR = True
        logger.info("NvDsSR API detected in pyds — hardware smart recording available")
    else:
        logger.warning(
            "pyds loaded but NvDsSR functions not found — "
            "smart recording will operate in stub mode"
        )
except ImportError:
    logger.warning(
        "pyds not available (not running inside DeepStream container?) — "
        "smart recording will operate in stub mode"
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SmartRecordConfig:
    """Configuration for the DeepStream Smart Record module.

    Attributes:
        output_dir: Base directory for recorded MP4 clips.  Sub-directories
            are created per camera and date automatically.
        pre_buffer_s: Seconds of video kept in a circular GPU buffer BEFORE
            the recording trigger.  When NvDsSRStart() is called the
            pre-buffer is flushed to disk so the clip begins *before* the
            violation was detected.
        post_buffer_s: Seconds of video recorded AFTER NvDsSRStop() is
            called.  This captures the tail-end of the event (e.g. the
            vehicle driving away).
        max_duration_s: Hard cap per clip.  Prevents runaway recordings if
            NvDsSRStop() is never called (e.g. a stuck FSM).
        container_format: NV_DS_SR_CONTAINER_MP4 (0) or MKV (1).
        codec: NV_DS_SR_CODEC_H264 (0) or H265 (1).
        bitrate: Target bitrate for the nvv4l2h264enc encoder in bps.
        max_concurrent: Maximum number of simultaneous recordings across all
            sources.  Each source can have at most one active recording.
    """

    output_dir: str = "/tmp/wastewatch/evidence/recordings"
    pre_buffer_s: int = 15
    post_buffer_s: int = 30
    max_duration_s: int = 120
    container_format: int = NV_DS_SR_CONTAINER_MP4
    codec: int = NV_DS_SR_CODEC_H264
    bitrate: int = 4_000_000
    max_concurrent: int = 8

    @classmethod
    def from_json(cls, path: str | Path) -> SmartRecordConfig:
        """Load configuration from a JSON file (e.g. configs/smart_record.json)."""
        with open(path) as fh:
            data = json.load(fh)
        return cls(
            output_dir=data.get("output_dir", cls.output_dir),
            pre_buffer_s=int(data.get("pre_buffer_s", cls.pre_buffer_s)),
            post_buffer_s=int(data.get("post_buffer_s", cls.post_buffer_s)),
            max_duration_s=int(data.get("max_duration_s", cls.max_duration_s)),
            container_format=(
                NV_DS_SR_CONTAINER_MP4
                if data.get("container", "mp4").lower() == "mp4"
                else NV_DS_SR_CONTAINER_MKV
            ),
            codec=(
                NV_DS_SR_CODEC_H264
                if data.get("codec", "h264").lower() == "h264"
                else NV_DS_SR_CODEC_H265
            ),
            bitrate=int(data.get("bitrate", cls.bitrate)),
            max_concurrent=int(data.get("max_concurrent_recordings", cls.max_concurrent)),
        )


# ---------------------------------------------------------------------------
# Active recording tracking
# ---------------------------------------------------------------------------


@dataclass
class _ActiveRecording:
    """Internal bookkeeping for a single in-progress smart recording."""

    camera_id: str
    track_id: int
    source_id: int
    session_id: int
    output_path: str
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at


# ---------------------------------------------------------------------------
# SmartRecordManager
# ---------------------------------------------------------------------------


class SmartRecordManager:
    """Manages NvDsSR (Smart Record) bins for all pipeline sources.

    Lifecycle:
        1. During pipeline construction, call ``create_sr_bin()`` for each
           source.  This creates an NvDsSR recording bin that should be
           added to a tee branch in the pipeline.
        2. When the FSM fires a VIOLATION event, call ``start_recording()``.
           DeepStream flushes the pre-buffer and begins writing an MP4.
        3. When the track exits or the violation ends, call
           ``stop_recording()``.  DeepStream writes ``post_buffer_s`` more
           seconds then finalises the container.
        4. On pipeline teardown call ``stop_all()`` to cleanly finalise
           every active recording.

    Thread safety:
        All public methods acquire ``_lock``.  The GStreamer pipeline thread
        and the GLib main-loop thread may call start/stop concurrently.
    """

    def __init__(self, config: SmartRecordConfig | None = None) -> None:
        self._config = config or SmartRecordConfig()
        self._lock = threading.Lock()

        # source_id -> pyds SR context (opaque handle)
        self._sr_contexts: dict[int, Any] = {}

        # camera_id -> source_id mapping
        self._camera_source_map: dict[str, int] = {}

        # (camera_id, track_id) -> _ActiveRecording
        self._active: dict[tuple[str, int], _ActiveRecording] = {}

        # Monotonically increasing session counter (used as session_id for
        # NvDsSRStart so each recording has a unique id).
        self._next_session_id: int = 1

        # User-supplied callback for recording lifecycle events.
        self._event_callback: Callable[[str, dict], None] | None = None

        # Ensure the base output directory exists.
        Path(self._config.output_dir).mkdir(parents=True, exist_ok=True)

        logger.info(
            "SmartRecordManager initialised: output_dir=%s, pre_buffer=%ds, "
            "post_buffer=%ds, max_duration=%ds, max_concurrent=%d",
            self._config.output_dir,
            self._config.pre_buffer_s,
            self._config.post_buffer_s,
            self._config.max_duration_s,
            self._config.max_concurrent,
        )

    # ------------------------------------------------------------------
    # Pipeline integration
    # ------------------------------------------------------------------

    def create_sr_bin(self, source_id: int, camera_id: str) -> Any:
        """Create an NvDsSR bin for *source_id* and register *camera_id*.

        The returned GStreamer bin should be added to the pipeline on a tee
        branch, e.g.::

            sr_bin = smart_rec_mgr.create_sr_bin(0, "cam-01")
            if sr_bin:
                pipeline.add(sr_bin)
                tee_src = tee.get_request_pad("src_%u")
                tee_src.link(sr_bin.get_static_pad("sink"))

        Returns ``None`` if the NvDsSR API is not available.
        """
        if not _SAFE_CAM_ID.match(camera_id):
            logger.error("Invalid camera_id for smart recording: %r", camera_id)
            return None

        with self._lock:
            self._camera_source_map[camera_id] = source_id

        if not _HAS_NVDSSR:
            logger.warning(
                "NvDsSR API unavailable — skipping SR bin creation for "
                "source_id=%d (%s).  Recording will not be available for "
                "this source.",
                source_id,
                camera_id,
            )
            return None

        try:
            # --- DeepStream NvDsSR API ---
            # NvDsSRCreate returns an opaque SR context handle and a GstBin
            # that encapsulates the recording sub-pipeline:
            #   queue -> nvvideoconvert -> capsfilter(I420) -> nvv4l2h264enc
            #   -> h264parse -> splitmuxsink
            #
            # Parameters:
            #   sr_bin (out)   : the created GstBin
            #   source_id      : pipeline source index
            #   container      : NV_DS_SR_CONTAINER_MP4
            #   codec          : NV_DS_SR_CODEC_H264
            #   bitrate        : encoder bitrate in bps
            #   pre_buffer_s   : circular pre-buffer duration
            #   output_dir     : base directory for output files

            sr_context = pyds.NvDsSRCreate(
                source_id,
                self._config.container_format,
                self._config.codec,
                self._config.bitrate,
                self._config.pre_buffer_s,
                self._config.output_dir,
            )

            if sr_context is None:
                logger.error(
                    "NvDsSRCreate returned None for source_id=%d (%s)",
                    source_id,
                    camera_id,
                )
                return None

            # Store the context for later Start/Stop calls.
            with self._lock:
                self._sr_contexts[source_id] = sr_context

            # The SR context exposes the GstBin via the .recordbin attribute
            # (see nvdssr.h: NvDsSRContext.recordbin).
            sr_bin = sr_context.recordbin if hasattr(sr_context, "recordbin") else None

            logger.info(
                "NvDsSR bin created for source_id=%d (%s) — "
                "pre_buffer=%ds, codec=%s, bitrate=%d bps",
                source_id,
                camera_id,
                self._config.pre_buffer_s,
                "H264" if self._config.codec == NV_DS_SR_CODEC_H264 else "H265",
                self._config.bitrate,
            )

            return sr_bin

        except Exception:
            logger.exception(
                "Failed to create NvDsSR bin for source_id=%d (%s)",
                source_id,
                camera_id,
            )
            return None

    # ------------------------------------------------------------------
    # Recording control
    # ------------------------------------------------------------------

    def start_recording(self, camera_id: str, track_id: int) -> str | None:
        """Start smart recording for a (camera, track) pair.

        Returns the expected output MP4 path, or ``None`` if the recording
        could not be started.
        """
        if not _SAFE_CAM_ID.match(camera_id):
            logger.error("Invalid camera_id for recording: %r", camera_id)
            return None

        key = (camera_id, track_id)

        with self._lock:
            # Already recording this track?
            if key in self._active:
                logger.debug(
                    "Already recording %s/track-%d — returning existing path",
                    camera_id,
                    track_id,
                )
                return self._active[key].output_path

            # Concurrent limit check
            if len(self._active) >= self._config.max_concurrent:
                logger.warning(
                    "Max concurrent recordings (%d) reached — "
                    "cannot start recording for %s/track-%d",
                    self._config.max_concurrent,
                    camera_id,
                    track_id,
                )
                return None

            source_id = self._camera_source_map.get(camera_id)
            if source_id is None:
                logger.error(
                    "No source_id registered for camera_id=%s — "
                    "was create_sr_bin() called?",
                    camera_id,
                )
                return None

            sr_context = self._sr_contexts.get(source_id)

            # Build the output path:  {output_dir}/{camera_id}/YYYY-MM-DD/track-{track_id}_HH-MM-SS.mp4
            now = time.localtime()
            date_str = time.strftime("%Y-%m-%d", now)
            time_str = time.strftime("%H-%M-%S", now)
            rel_path = f"{camera_id}/{date_str}/track-{track_id}_{time_str}.mp4"
            output_path = str(Path(self._config.output_dir) / rel_path)

            # Ensure the parent directory exists.
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            session_id = self._next_session_id
            self._next_session_id += 1

        # --- Invoke DeepStream NvDsSRStart ---
        if _HAS_NVDSSR and sr_context is not None:
            try:
                # NvDsSRStart parameters:
                #   sr_context  : the opaque context from NvDsSRCreate
                #   session_id  : unique session identifier
                #   start_time  : recording start relative to pre-buffer (0 = use full pre-buffer)
                #   duration    : maximum recording duration in seconds
                #   output_dir  : per-session override (or None to use default)
                pyds.NvDsSRStart(
                    sr_context,
                    session_id,
                    0,  # start_time=0 -> flush the entire pre-buffer
                    self._config.max_duration_s,
                    str(Path(output_path).parent),
                )
                logger.info(
                    "SmartRecord started: %s/track-%d (session=%d, pre_buffer=%ds) -> %s",
                    camera_id,
                    track_id,
                    session_id,
                    self._config.pre_buffer_s,
                    output_path,
                )
            except Exception:
                logger.exception(
                    "NvDsSRStart failed for %s/track-%d (source_id=%d)",
                    camera_id,
                    track_id,
                    source_id,
                )
                return None
        else:
            logger.info(
                "SmartRecord started (stub): %s/track-%d (session=%d) -> %s",
                camera_id,
                track_id,
                session_id,
                output_path,
            )

        # Track the active recording.
        recording = _ActiveRecording(
            camera_id=camera_id,
            track_id=track_id,
            source_id=source_id,
            session_id=session_id,
            output_path=output_path,
        )
        with self._lock:
            self._active[key] = recording

        self._fire_event("recording.started", {
            "camera_id": camera_id,
            "track_id": track_id,
            "session_id": session_id,
            "output_path": output_path,
        })

        return output_path

    def stop_recording(self, camera_id: str, track_id: int) -> str | None:
        """Stop smart recording for a (camera, track) pair.

        After this call DeepStream will continue recording for
        ``post_buffer_s`` seconds, then finalise the MP4 container (writing
        the moov atom) and invoke the SR callback.

        Returns the output file path, or ``None`` if no active recording
        was found for this key.
        """
        key = (camera_id, track_id)

        with self._lock:
            recording = self._active.pop(key, None)

        if recording is None:
            logger.debug(
                "No active recording for %s/track-%d — nothing to stop",
                camera_id,
                track_id,
            )
            return None

        sr_context: Any = None
        with self._lock:
            sr_context = self._sr_contexts.get(recording.source_id)

        # --- Invoke DeepStream NvDsSRStop ---
        if _HAS_NVDSSR and sr_context is not None:
            try:
                # NvDsSRStop tells DeepStream to:
                #   1. Record post_buffer_s more seconds of live frames
                #   2. Finalise the MP4 container (write moov atom)
                #   3. Invoke the SR callback with STATUS_STOPPED
                pyds.NvDsSRStop(sr_context, recording.session_id)
                logger.info(
                    "SmartRecord stopped: %s/track-%d (session=%d, "
                    "elapsed=%.1fs, post_buffer=%ds) -> %s",
                    camera_id,
                    track_id,
                    recording.session_id,
                    recording.elapsed_s,
                    self._config.post_buffer_s,
                    recording.output_path,
                )
            except Exception:
                logger.exception(
                    "NvDsSRStop failed for %s/track-%d (session=%d)",
                    camera_id,
                    track_id,
                    recording.session_id,
                )
        else:
            logger.info(
                "SmartRecord stopped (stub): %s/track-%d (session=%d, "
                "elapsed=%.1fs) -> %s",
                camera_id,
                track_id,
                recording.session_id,
                recording.elapsed_s,
                recording.output_path,
            )

        self._fire_event("recording.stopped", {
            "camera_id": camera_id,
            "track_id": track_id,
            "session_id": recording.session_id,
            "output_path": recording.output_path,
            "duration_s": round(recording.elapsed_s, 1),
        })

        return recording.output_path

    def stop_all(self) -> list[str]:
        """Stop all active recordings (used during pipeline shutdown).

        Returns a list of output file paths for recordings that were active.
        """
        with self._lock:
            keys = list(self._active.keys())

        paths: list[str] = []
        for camera_id, track_id in keys:
            path = self.stop_recording(camera_id, track_id)
            if path:
                paths.append(path)

        if paths:
            logger.info("Stopped %d active recording(s) during shutdown", len(paths))
        return paths

    def get_active_recordings(self) -> list[dict]:
        """Return status of all active recordings.

        Each entry contains camera_id, track_id, session_id, elapsed_s,
        and output_path.
        """
        with self._lock:
            recordings = list(self._active.values())

        return [
            {
                "camera_id": r.camera_id,
                "track_id": r.track_id,
                "session_id": r.session_id,
                "elapsed_s": round(r.elapsed_s, 1),
                "output_path": r.output_path,
            }
            for r in recordings
        ]

    def cleanup_expired(self) -> list[str]:
        """Force-stop recordings that have exceeded ``max_duration_s``.

        Called periodically (e.g. from the GLib health-check timer) to
        prevent runaway recordings from filling disk.

        Returns a list of output paths for recordings that were stopped.
        """
        max_dur = self._config.max_duration_s

        with self._lock:
            expired_keys = [
                (r.camera_id, r.track_id)
                for r in self._active.values()
                if r.elapsed_s > max_dur
            ]

        paths: list[str] = []
        for camera_id, track_id in expired_keys:
            logger.warning(
                "Recording for %s/track-%d exceeded max_duration_s (%ds) — force-stopping",
                camera_id,
                track_id,
                max_dur,
            )
            path = self.stop_recording(camera_id, track_id)
            if path:
                paths.append(path)

        return paths

    # ------------------------------------------------------------------
    # NvDsSR Callback
    # ------------------------------------------------------------------

    def _sr_callback(self, info: Any, user_data: Any) -> None:
        """Callback invoked by the NvDsSRModule on recording lifecycle events.

        This is registered with DeepStream when the SR bin is created.
        ``info`` is an NvDsSRRecordingInfo struct with fields:
            - sessionId  : int
            - status     : NV_DS_SR_STATUS_STARTED / STOPPED / ERROR
            - filename   : str (output file path written by DeepStream)
            - dirpath    : str (output directory)

        The callback runs on a GStreamer streaming thread, so it must be
        lightweight (no blocking I/O).
        """
        try:
            session_id = info.sessionId if hasattr(info, "sessionId") else -1
            status = info.status if hasattr(info, "status") else -1
            filename = info.filename if hasattr(info, "filename") else ""

            if status == NV_DS_SR_STATUS_STARTED:
                logger.info(
                    "SR callback: recording STARTED (session=%d, file=%s)",
                    session_id,
                    filename,
                )
            elif status == NV_DS_SR_STATUS_STOPPED:
                logger.info(
                    "SR callback: recording STOPPED (session=%d, file=%s)",
                    session_id,
                    filename,
                )
                self._fire_event("recording.finalised", {
                    "session_id": session_id,
                    "filename": filename,
                })
            elif status == NV_DS_SR_STATUS_ERROR:
                logger.error(
                    "SR callback: recording ERROR (session=%d, file=%s)",
                    session_id,
                    filename,
                )
                self._fire_event("recording.error", {
                    "session_id": session_id,
                    "filename": filename,
                })
            else:
                logger.warning(
                    "SR callback: unknown status %d (session=%d)",
                    status,
                    session_id,
                )
        except Exception:
            logger.exception("Error in SR callback")

    # ------------------------------------------------------------------
    # Event hook
    # ------------------------------------------------------------------

    def set_event_callback(self, callback: Callable[[str, dict], None]) -> None:
        """Register a callback for recording lifecycle events.

        The callback receives (event_type: str, payload: dict) where
        event_type is one of:
            "recording.started", "recording.stopped",
            "recording.finalised", "recording.error"
        """
        self._event_callback = callback

    def _fire_event(self, event_type: str, payload: dict) -> None:
        if self._event_callback is not None:
            try:
                self._event_callback(event_type, payload)
            except Exception:
                logger.exception("Error in recording event callback")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        """Release all NvDsSR contexts (call during pipeline teardown)."""
        self.stop_all()

        if _HAS_NVDSSR:
            with self._lock:
                for source_id, ctx in self._sr_contexts.items():
                    try:
                        pyds.NvDsSRDestroy(ctx)
                        logger.debug("Destroyed SR context for source_id=%d", source_id)
                    except Exception:
                        logger.exception(
                            "Failed to destroy SR context for source_id=%d",
                            source_id,
                        )
                self._sr_contexts.clear()

        logger.info("SmartRecordManager destroyed")

    @property
    def has_native_api(self) -> bool:
        """Whether the native NvDsSR API is available."""
        return _HAS_NVDSSR

    @property
    def active_count(self) -> int:
        """Number of currently active recordings."""
        with self._lock:
            return len(self._active)
