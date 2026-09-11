# backend/app/evidence/stream_recorder.py
"""Smart recording — captures video clips from inference output RTSP streams.

When a VIOLATION event is detected, the recorder starts capturing the
inference output stream from MediaMTX. When the track EXITS or a timeout
is reached, recording stops and the clip is saved for evidence.

The inference output stream (not raw camera feed) is recorded because it
contains the annotated video with bounding boxes and track IDs overlaid
by the DeepStream pipeline.
"""
import asyncio
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

RECORDING_DIR = Path("/tmp/wastewatch/recordings")
RECORDING_DIR.mkdir(parents=True, exist_ok=True)

# SECURITY: Only allow safe camera IDs in paths and URLs
_SAFE_CAM_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


@dataclass
class ActiveRecording:
    camera_id: str
    track_id: int
    rtsp_url: str
    output_path: Path
    process: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.monotonic)
    max_duration_s: float = 120.0  # Hard cap: 2 minutes per clip

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_expired(self) -> bool:
        return self.elapsed_s > self.max_duration_s


class StreamRecorder:
    """Records video clips from MediaMTX inference output streams.

    Uses ffmpeg to capture RTSP streams as MP4 clips. Each recording
    is keyed by (camera_id, track_id) so concurrent violations on
    different tracks are recorded independently.

    The pre_buffer_s and post_buffer_s parameters control how much
    context is captured around the violation event.
    """

    def __init__(
        self,
        mediamtx_rtsp_url: str = "rtsp://localhost:8554",
        pre_buffer_s: int = 10,
        post_buffer_s: int = 15,
        max_concurrent: int = 4,
    ):
        self._mediamtx_rtsp_url = mediamtx_rtsp_url.rstrip("/")
        self._pre_buffer_s = pre_buffer_s
        self._post_buffer_s = post_buffer_s
        self._max_concurrent = max_concurrent
        self._active: dict[tuple[str, int], ActiveRecording] = {}
        self._lock = asyncio.Lock()

    async def start_recording(self, camera_id: str, track_id: int) -> str | None:
        """Start recording the inference output stream for a violation.

        Returns the output file path, or None if recording couldn't start.
        """
        key = (camera_id, track_id)

        async with self._lock:
            if key in self._active:
                logger.debug("Already recording %s/track-%d", camera_id, track_id)
                return str(self._active[key].output_path)

            if len(self._active) >= self._max_concurrent:
                logger.warning(
                    "Max concurrent recordings (%d) reached — skipping %s/track-%d",
                    self._max_concurrent, camera_id, track_id,
                )
                return None

        # SECURITY: Validate camera_id to prevent RTSP URL manipulation / path traversal
        if not _SAFE_CAM_ID.match(camera_id):
            logger.error("Invalid camera_id for recording: %r", camera_id)
            return None

        # Build RTSP URL for the inference output stream
        # Pipeline publishes annotated video to MediaMTX as cam-XX-out
        stream_name = f"{camera_id}-out"
        rtsp_url = f"{self._mediamtx_rtsp_url}/{stream_name}"

        # Output path
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        date_dir = RECORDING_DIR / camera_id / time.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        output_path = date_dir / f"track-{track_id}_{ts}.mp4"

        # Start ffmpeg process
        cmd = [
            "ffmpeg",
            "-y",                           # Overwrite output
            "-rtsp_transport", "tcp",       # TCP for reliability
            "-i", rtsp_url,                 # Input: inference RTSP stream
            "-t", str(self._pre_buffer_s + self._post_buffer_s + 60),  # Max duration safety
            "-c:v", "copy",                 # No re-encoding (already H.264)
            "-movflags", "+faststart",      # Web-friendly MP4
            "-an",                          # No audio
            str(output_path),
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("ffmpeg not found — cannot record video clips")
            return None
        except Exception:
            logger.exception("Failed to start recording for %s/track-%d", camera_id, track_id)
            return None

        recording = ActiveRecording(
            camera_id=camera_id,
            track_id=track_id,
            rtsp_url=rtsp_url,
            output_path=output_path,
            process=process,
        )

        async with self._lock:
            self._active[key] = recording

        logger.info(
            "Started recording %s/track-%d → %s",
            camera_id, track_id, output_path,
        )
        return str(output_path)

    async def stop_recording(self, camera_id: str, track_id: int) -> str | None:
        """Stop recording and return the clip file path.

        Waits post_buffer_s after being called before actually stopping,
        to capture the tail end of the event.
        """
        key = (camera_id, track_id)

        async with self._lock:
            recording = self._active.get(key)
            if recording is None:
                return None

        # Wait for post-buffer to capture exit context
        await asyncio.sleep(self._post_buffer_s)

        return await self._terminate_recording(key)

    async def stop_all(self) -> None:
        """Stop all active recordings (called on shutdown)."""
        async with self._lock:
            keys = list(self._active.keys())
        for key in keys:
            await self._terminate_recording(key)

    async def cleanup_expired(self) -> list[str]:
        """Terminate recordings that exceeded max_duration_s. Returns paths of saved clips."""
        saved = []
        async with self._lock:
            expired_keys = [
                k for k, r in self._active.items() if r.is_expired
            ]
        for key in expired_keys:
            path = await self._terminate_recording(key)
            if path:
                saved.append(path)
        return saved

    @property
    def active_count(self) -> int:
        return len(self._active)

    def get_active_recordings(self) -> list[dict]:
        return [
            {
                "camera_id": r.camera_id,
                "track_id": r.track_id,
                "elapsed_s": round(r.elapsed_s, 1),
                "output_path": str(r.output_path),
            }
            for r in self._active.values()
        ]

    async def _terminate_recording(self, key: tuple[str, int]) -> str | None:
        async with self._lock:
            recording = self._active.pop(key, None)
        if recording is None:
            return None

        if recording.process and recording.process.poll() is None:
            # Send SIGINT for graceful ffmpeg shutdown (writes moov atom)
            recording.process.terminate()
            try:
                recording.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                recording.process.kill()
                recording.process.wait()

        output_path = recording.output_path
        if output_path.exists() and output_path.stat().st_size > 0:
            logger.info(
                "Saved recording %s/track-%d (%.1fs) → %s",
                recording.camera_id, recording.track_id,
                recording.elapsed_s, output_path,
            )
            return str(output_path)
        else:
            logger.warning(
                "Recording %s/track-%d produced empty file",
                recording.camera_id, recording.track_id,
            )
            return None
