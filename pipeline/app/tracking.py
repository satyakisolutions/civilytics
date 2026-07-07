# pipeline/app/tracking.py
"""Per-camera track state management with dwell time tracking."""
import time
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_MAX_BBOX_HISTORY = 50


# Number of consecutive frames outside ROI before we consider the object
# truly exited. Prevents jittery detections (bbox flicker, tracker miss)
# from resetting dwell time on a single missed frame.
_ROI_EXIT_HYSTERESIS = 5


@dataclass
class TrackInfo:
    track_id: int
    camera_id: str
    class_name: str
    first_seen: float  # monotonic time
    last_seen: float
    in_roi: bool = False
    roi_entered_at: float | None = None
    _outside_roi_count: int = 0  # hysteresis counter
    bbox_history: deque = field(default_factory=lambda: deque(maxlen=_MAX_BBOX_HISTORY))

    @property
    def age_s(self) -> float:
        return self.last_seen - self.first_seen

    @property
    def dwell_time_s(self) -> float:
        if self.roi_entered_at is None:
            return 0.0
        return self.last_seen - self.roi_entered_at

    @property
    def speed_px_s(self) -> float:
        """Recent bbox-center speed in pixels/sec (average over last ~2s of samples)."""
        if len(self.bbox_history) < 2:
            return 0.0
        recent = list(self.bbox_history)[-5:]  # last ~2.5s of samples
        dt = recent[-1].get("t", 0) - recent[0].get("t", 0)
        if dt <= 0:
            return 0.0
        def center(b):
            return (b.get("x", 0) + b.get("w", 0) / 2.0,
                    b.get("y", 0) + b.get("h", 0) / 2.0)
        cx0, cy0 = center(recent[0])
        cx1, cy1 = center(recent[-1])
        return ((cx1 - cx0) ** 2 + (cy1 - cy0) ** 2) ** 0.5 / dt

    @property
    def bbox_area(self) -> float:
        """Current bbox area in px^2 — proxy for distance-to-camera (larger = closer)."""
        if not self.bbox_history:
            return 0.0
        last = self.bbox_history[-1]
        return float(last.get("w", 0)) * float(last.get("h", 0))

    def update(self, bbox: dict, in_roi: bool):
        now = time.monotonic()
        self.last_seen = now
        # Sample bbox history every ~0.5s (deque auto-evicts oldest when full)
        if not self.bbox_history or (now - self.bbox_history[-1].get("t", 0)) > 0.5:
            self.bbox_history.append({"t": round(now, 2), **bbox})
        # ROI transitions with hysteresis — don't exit on a single missed frame
        if in_roi:
            self._outside_roi_count = 0
            if not self.in_roi:
                self.roi_entered_at = now
                self.in_roi = True
        else:
            self._outside_roi_count += 1
            if self.in_roi and self._outside_roi_count >= _ROI_EXIT_HYSTERESIS:
                self.in_roi = False
                self.roi_entered_at = None


class TrackStateManager:
    def __init__(self, lost_timeout_s: float = 30.0):
        self._tracks: dict[tuple[str, int], TrackInfo] = {}
        self._lost_timeout_s = lost_timeout_s

    def update_track(self, camera_id: str, track_id: int, class_name: str, bbox: dict, in_roi: bool) -> TrackInfo:
        key = (camera_id, track_id)
        now = time.monotonic()
        if key not in self._tracks:
            self._tracks[key] = TrackInfo(track_id=track_id, camera_id=camera_id, class_name=class_name, first_seen=now, last_seen=now)
        track = self._tracks[key]
        track.update(bbox, in_roi)
        return track

    def get_track(self, camera_id: str, track_id: int) -> TrackInfo | None:
        return self._tracks.get((camera_id, track_id))

    def prune_lost(self) -> list[TrackInfo]:
        now = time.monotonic()
        lost = []
        to_remove = []
        for key, track in self._tracks.items():
            if now - track.last_seen > self._lost_timeout_s:
                lost.append(track)
                to_remove.append(key)
        for key in to_remove:
            del self._tracks[key]
        return lost

    def active_tracks(self, camera_id: str | None = None) -> list[TrackInfo]:
        if camera_id:
            return [t for t in self._tracks.values() if t.camera_id == camera_id]
        return list(self._tracks.values())
