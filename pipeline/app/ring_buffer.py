"""Per-camera bounded ring buffer of timestamped frames.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
section 8. A latest-frame-only cache (as used by the existing trigger-VLM
path's frame_jpeg_cache) cannot supply pre-event video -- this buffer keeps
a bounded window so an event's pre-roll can be reconstructed after the
fact, without holding unbounded memory.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedFrame:
    timestamp: float
    jpeg_bytes: bytes


class CameraRingBuffer:
    """Fixed-duration ring buffer of frames for one camera.

    Frames older than `max_age_s` are dropped on each append, so memory
    stays bounded regardless of append rate.
    """

    def __init__(self, max_age_s: float = 12.0) -> None:
        self._max_age_s = max_age_s
        self._frames: deque[BufferedFrame] = deque()

    def append(self, timestamp: float, jpeg_bytes: bytes) -> None:
        self._frames.append(BufferedFrame(timestamp=timestamp, jpeg_bytes=jpeg_bytes))
        self._evict_older_than(timestamp - self._max_age_s)

    def _evict_older_than(self, cutoff: float) -> None:
        while self._frames and self._frames[0].timestamp < cutoff:
            self._frames.popleft()

    def frames_in_window(self, start_s: float, end_s: float) -> list[BufferedFrame]:
        """Return buffered frames with start_s <= timestamp <= end_s, in order."""
        return [f for f in self._frames if start_s <= f.timestamp <= end_s]

    def pre_roll(self, event_timestamp: float, pre_roll_s: float) -> list[BufferedFrame]:
        return self.frames_in_window(event_timestamp - pre_roll_s, event_timestamp)

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def oldest_timestamp(self) -> float | None:
        return self._frames[0].timestamp if self._frames else None

    @property
    def newest_timestamp(self) -> float | None:
        return self._frames[-1].timestamp if self._frames else None
