"""Violation-time face capture: per-track head-crop ring buffer.

The raw (pre-nvosd) capture branch in pipeline.py calls FaceCropBuffer.add()
for every tracked person at <= 1/WW_FACE_CAPTURE_INTERVAL_S fps. Nothing
leaves process memory unless a violation calls begin_incident(); the
flusher thread then writes that incident's crops to the private faces
volume and publishes one wastewatch.faces.requests message per incident
id. Crops of people who never violate are overwritten or evicted within
seconds and never touch disk.

Stdlib only, so it is unit-testable outside the DeepStream container.
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §5.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

RING_SIZE = 6
MAX_INCIDENT_CROPS = 12
STALE_AFTER_S = 5.0
MIN_CROP_PX = 48
TOPIC_FACES_REQUESTS = "wastewatch.faces.requests"

TrackKey = tuple  # (camera_id: str, track_id: int)


@dataclass
class Incident:
    incident_id: str
    camera_id: str
    track_id: int
    detected_at: float
    close_at: float
    crops: list = field(default_factory=list)
    # Later alerts for the same person while this incident is still open:
    # (incident_id, detected_at). Each gets its own request and folder.
    alias_ids: list = field(default_factory=list)


def head_crop_box(
    left: float, top: float, width: float, height: float, frame_w: int, frame_h: int
) -> tuple[int, int, int, int] | None:
    """Pixel box (x1, y1, x2, y2) of a person's head region, or None if too small.

    Top half of the person bbox, padded 10% of the bbox width on each side,
    clamped to the frame.
    """
    pad = 0.10 * width
    x1 = max(0, int(left - pad))
    x2 = min(int(frame_w), int(left + width + pad))
    y1 = max(0, int(top))
    y2 = min(int(frame_h), int(top + 0.5 * height))
    if x2 - x1 < MIN_CROP_PX or y2 - y1 < MIN_CROP_PX:
        return None
    return x1, y1, x2, y2


class FaceCropBuffer:
    """Thread-safe per-(camera_id, track_id) ring of head-crop JPEGs."""

    def __init__(
        self,
        ring_size: int = RING_SIZE,
        max_incident_crops: int = MAX_INCIDENT_CROPS,
        postroll_s: float = 2.0,
        stale_after_s: float = STALE_AFTER_S,
    ) -> None:
        self._lock = threading.Lock()
        self._ring_size = ring_size
        self._max_incident_crops = max_incident_crops
        self._postroll_s = postroll_s
        self._stale_after_s = stale_after_s
        self._rings: dict[TrackKey, deque] = {}
        self._last_seen: dict[TrackKey, float] = {}
        self._open: dict[TrackKey, Incident] = {}

    def add(self, camera_id: str, track_id: int, ts: float, jpeg: bytes) -> None:
        key = (camera_id, track_id)
        with self._lock:
            ring = self._rings.get(key)
            if ring is None:
                ring = deque(maxlen=self._ring_size)
                self._rings[key] = ring
            ring.append(jpeg)
            self._last_seen[key] = ts
            incident = self._open.get(key)
            if incident is not None and len(incident.crops) < self._max_incident_crops:
                incident.crops.append(jpeg)

    def begin_incident(
        self, incident_id: str, camera_id: str, track_id: int, now: float, detected_at: float
    ) -> None:
        key = (camera_id, track_id)
        with self._lock:
            existing = self._open.get(key)
            if existing is not None:
                existing.alias_ids.append((incident_id, detected_at))
                return
            seed = list(self._rings.get(key, ()))[-self._max_incident_crops:]
            self._open[key] = Incident(
                incident_id=incident_id,
                camera_id=camera_id,
                track_id=track_id,
                detected_at=detected_at,
                close_at=now + self._postroll_s,
                crops=seed,
            )

    def pop_due(self, now: float) -> list[Incident]:
        with self._lock:
            due = [key for key, inc in self._open.items() if inc.close_at <= now]
            return [self._open.pop(key) for key in due]

    def evict_stale(self, now: float) -> None:
        with self._lock:
            stale = [
                key for key, seen in self._last_seen.items()
                if now - seen > self._stale_after_s and key not in self._open
            ]
            for key in stale:
                self._rings.pop(key, None)
                self._last_seen.pop(key, None)

    def tracked_count(self) -> int:
        with self._lock:
            return len(self._rings)


def ensure_faces_dirs(base_dir: Path) -> None:
    """Create incoming/ and subjects/ world-writable.

    The pipeline runs as root while the backend image runs as appuser; both
    must be able to create and delete inside these folders.
    """
    for sub in ("incoming", "subjects"):
        d = Path(base_dir) / sub
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o777)


def write_incident_crops(base_dir: Path, incident_id: str, crops: list) -> list[str]:
    d = Path(base_dir) / "incoming" / incident_id
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o777)
    paths: list[str] = []
    for i, data in enumerate(crops):
        p = d / f"{i:02d}.jpg"
        p.write_bytes(data)
        os.chmod(p, 0o666)
        paths.append(str(p))
    return paths


def flush_due(
    buffer: FaceCropBuffer,
    now: float,
    base_dir: Path,
    publish: Callable[[str, dict], None],
) -> int:
    """Write + publish every incident whose post-roll ended. Returns requests published."""
    sent = 0
    for incident in buffer.pop_due(now):
        ids = [(incident.incident_id, incident.detected_at), *incident.alias_ids]
        for incident_id, detected_at in ids:
            paths = write_incident_crops(base_dir, incident_id, incident.crops) if incident.crops else []
            publish(incident.camera_id, {
                "incident_id": incident_id,
                "camera_id": incident.camera_id,
                "person_track_id": incident.track_id,
                "detected_at": detected_at,
                "crops": paths,
            })
            sent += 1
    return sent


def run_flusher(
    buffer: FaceCropBuffer,
    base_dir: Path,
    publish: Callable[[str, dict], None],
    stop: threading.Event,
    interval_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    on_error: Callable[[], None] | None = None,
) -> None:
    """Flusher thread body: evict idle tracks and flush closed incidents until `stop` is set."""
    while not stop.wait(interval_s):
        now = clock()
        try:
            buffer.evict_stale(now)
            flush_due(buffer, now, base_dir, publish)
        except Exception:
            if on_error is not None:
                on_error()
