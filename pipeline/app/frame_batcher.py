# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Conditional frame batcher for VLM analysis — per-camera isolated.

Each camera has its own dict of track batchers. The buffer/pipeline logic
is identical across all cameras — only the data differs. Cameras are fully
isolated: one camera's state never touches another's.

Structure:
    _cameras: dict[str, CameraBatchState]
        Each camera owns:
            tracks: dict[int, TrackBatchState]  — per-track frame buffer
            config: same interval/batch_size for all

Frame collection triggers (per-track):
    - FSM enters OBSERVE/VIOLATION/ALERTING/ESCALATED
    - Action SGIE detects "dumping_waste"

Sampling: 1 frame every 5s, 12 frames per batch (60s window).
Frames from different cameras are NEVER mixed.
Frames from different tracks within the same camera are NEVER mixed.
"""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FrameSample:
    """A single sampled frame for VLM batching."""
    camera_id: str
    track_id: int
    frame_num: int
    timestamp_ms: int
    detections: list[dict]
    sampled_at: float


@dataclass
class TrackBatchState:
    """Per-track frame buffer within a camera."""
    track_id: int
    frames: list[FrameSample] = field(default_factory=list)
    last_sample_at: float = 0.0
    started_at: float = 0.0
    fsm_state: str = "idle"


@dataclass
class CameraBatchState:
    """Per-camera batch state — owns all track batchers for this camera."""
    camera_id: str
    tracks: dict[int, TrackBatchState] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class FrameBatcher:
    """Per-camera isolated frame batcher for multi-stream VLM analysis.

    Same pipeline logic for every camera. Different data per camera.
    Each camera is a dict entry. Each track within a camera is a nested dict entry.

    Parameters
    ----------
    sample_interval_s:
        Minimum seconds between sampled frames per track (default 5).
    batch_size:
        Frames per batch before yielding to VLM (default 12).
        At 5s interval: 12 frames = 60s window.
    batch_timeout_s:
        Max seconds before yielding a partial batch (default 90).
    trigger_states:
        FSM states that activate frame collection.
    """

    def __init__(
        self,
        sample_interval_s: float = 5.0,
        batch_size: int = 12,
        batch_timeout_s: float = 90.0,
        trigger_states: set[str] | None = None,
    ) -> None:
        self._interval = sample_interval_s
        self._batch_size = batch_size
        self._timeout = batch_timeout_s
        self._trigger_states = trigger_states or {
            "observe", "violation", "alerting", "escalated",
        }

        # Per-camera isolated state
        # Each camera has its own dict of track batchers
        self._cameras: dict[str, CameraBatchState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_collect(
        self, fsm_state: str | None, action_label: str | None = None,
    ) -> bool:
        """Check if this FSM state / action warrants frame collection."""
        if action_label == "dumping_waste":
            return True
        return fsm_state is not None and fsm_state in self._trigger_states

    def on_frame(
        self,
        camera_id: str,
        track_id: int,
        frame_num: int,
        timestamp_ms: int,
        fsm_state: str | None,
        detections: list[dict],
        action_label: str | None = None,
    ) -> list[FrameSample] | None:
        """Process a frame. Returns a batch if ready, None otherwise.

        Called from the pad probe on every frame. Same logic for every camera.
        Data is isolated by camera_id → track_id dict nesting.
        """
        if track_id is None:
            return None

        # Should we collect for this track?
        if not self.should_collect(fsm_state, action_label):
            # Not collecting — but check if we have a stale batcher to timeout
            return self._check_track_timeout(camera_id, track_id)

        now = time.monotonic()

        # Get or create camera state
        cam = self._cameras.get(camera_id)
        if cam is None:
            cam = CameraBatchState(camera_id=camera_id)
            self._cameras[camera_id] = cam

        # Get or create track state within this camera
        track = cam.tracks.get(track_id)
        if track is None:
            track = TrackBatchState(
                track_id=track_id,
                started_at=now,
            )
            cam.tracks[track_id] = track
            logger.info(
                "FrameBatcher: %s/track-%d started (state=%s)",
                camera_id, track_id, fsm_state,
            )

        track.fsm_state = fsm_state or "unknown"

        # Sampling interval check — skip if too soon
        if now - track.last_sample_at < self._interval:
            return self._check_track_timeout(camera_id, track_id)

        # Sample this frame
        sample = FrameSample(
            camera_id=camera_id,
            track_id=track_id,
            frame_num=frame_num,
            timestamp_ms=timestamp_ms,
            detections=detections,
            sampled_at=now,
        )
        track.frames.append(sample)
        track.last_sample_at = now

        logger.debug(
            "FrameBatcher: %s/track-%d sampled frame %d (%d/%d)",
            camera_id, track_id, frame_num,
            len(track.frames), self._batch_size,
        )

        # Batch full?
        if len(track.frames) >= self._batch_size:
            return self._yield_track_batch(camera_id, track_id)

        return None

    def on_track_exit(
        self, camera_id: str, track_id: int,
    ) -> list[FrameSample] | None:
        """Track exited — flush partial batch for final VLM analysis."""
        cam = self._cameras.get(camera_id)
        if cam is None:
            return None
        track = cam.tracks.get(track_id)
        if track is None:
            return None
        if not track.frames:
            cam.tracks.pop(track_id, None)
            self._cleanup_empty_camera(camera_id)
            return None
        return self._yield_track_batch(camera_id, track_id)

    def on_camera_removed(self, camera_id: str) -> list[list[FrameSample]]:
        """Camera removed — flush all track batches for this camera.

        Called when a camera goes offline or is unregistered.
        Returns list of partial batches (one per track that had frames).
        """
        cam = self._cameras.pop(camera_id, None)
        if cam is None:
            return []
        batches = []
        for track in cam.tracks.values():
            if track.frames:
                batches.append(track.frames)
                logger.info(
                    "FrameBatcher: camera %s removed, flushed %d frames for track-%d",
                    camera_id, len(track.frames), track.track_id,
                )
        return batches

    def prune_stale(self) -> list[tuple[str, int, list[FrameSample]]]:
        """Prune timed-out track batchers across all cameras.

        Returns list of (camera_id, track_id, frames) for each pruned batch.
        """
        now = time.monotonic()
        results = []
        empty_cameras = []

        for camera_id, cam in self._cameras.items():
            stale_tracks = []
            for track_id, track in cam.tracks.items():
                if now - track.started_at > self._timeout:
                    if track.frames:
                        results.append((camera_id, track_id, list(track.frames)))
                    stale_tracks.append(track_id)

            for track_id in stale_tracks:
                del cam.tracks[track_id]
                logger.info(
                    "FrameBatcher: pruned %s/track-%d (timeout)",
                    camera_id, track_id,
                )

            if not cam.tracks:
                empty_cameras.append(camera_id)

        for camera_id in empty_cameras:
            del self._cameras[camera_id]

        return results

    # ------------------------------------------------------------------
    # Status / Monitoring
    # ------------------------------------------------------------------

    @property
    def active_cameras(self) -> int:
        return len(self._cameras)

    @property
    def active_tracks(self) -> int:
        return sum(len(cam.tracks) for cam in self._cameras.values())

    def get_status(self) -> dict[str, list[dict]]:
        """Per-camera status with track details.

        Returns:
            {camera_id: [{track_id, frames_collected, fsm_state, age_s}, ...]}
        """
        now = time.monotonic()
        status: dict[str, list[dict]] = {}
        for camera_id, cam in self._cameras.items():
            status[camera_id] = [
                {
                    "track_id": t.track_id,
                    "frames": len(t.frames),
                    "batch_size": self._batch_size,
                    "fsm_state": t.fsm_state,
                    "age_s": round(now - t.started_at, 1),
                }
                for t in cam.tracks.values()
            ]
        return status

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _yield_track_batch(
        self, camera_id: str, track_id: int,
    ) -> list[FrameSample]:
        """Pop a track's frames and return them as a batch."""
        cam = self._cameras[camera_id]
        track = cam.tracks.pop(track_id)
        frames = track.frames
        logger.info(
            "FrameBatcher: %s/track-%d yielding %d frames (%.1fs window)",
            camera_id, track_id, len(frames),
            time.monotonic() - track.started_at,
        )
        self._cleanup_empty_camera(camera_id)
        return frames

    def _check_track_timeout(
        self, camera_id: str, track_id: int,
    ) -> list[FrameSample] | None:
        """Check if a specific track batcher has timed out."""
        cam = self._cameras.get(camera_id)
        if cam is None:
            return None
        track = cam.tracks.get(track_id)
        if track is None:
            return None
        if time.monotonic() - track.started_at > self._timeout and track.frames:
            return self._yield_track_batch(camera_id, track_id)
        return None

    def _cleanup_empty_camera(self, camera_id: str) -> None:
        """Remove camera dict entry if it has no active tracks."""
        cam = self._cameras.get(camera_id)
        if cam is not None and not cam.tracks:
            del self._cameras[camera_id]
