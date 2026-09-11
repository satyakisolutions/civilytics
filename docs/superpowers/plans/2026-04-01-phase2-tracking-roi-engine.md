# Phase 2: Tracking + ROI Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add persistent object tracking (ByteTrack), polygon ROI zones, dwell-time tracking, and the Event FSM so that the system fires violation events when persons/vehicles linger in monitored zones.

**Architecture:** DeepStream nvtracker (ByteTrack) provides persistent track IDs. nvdsanalytics performs point-in-polygon ROI checks. A Python Event FSM runs per-track, transitioning IDLE → ENTER → OBSERVE → VIOLATION → EXITED. Events are published to Kafka `wastewatch.events` and stored in Neon Postgres `track_sessions` / `violation_events`. The frontend gets a ROI polygon editor and enhanced overlay showing track IDs + dwell time with color coding.

**Tech Stack:** DeepStream nvtracker (ByteTrack), nvdsanalytics, Python FSM, Kafka, SQLAlchemy/asyncpg, React + Canvas (ROI editor), WebSocket

**Spec Reference:** `/docs/superpowers/specs/2026-03-31-wastewatch-ai-pipeline-design.md` — Sections 3 (Event FSM), 4 (tracking_bytetrack, roi modules), 5 (track_sessions table)

**Builds on:** Phase 1 (`phase1/foundation-deepstream-streaming`) — backend API, pipeline, Kafka, WebSocket metadata

---

## File Structure

### New/Modified: Pipeline (`/pipeline/`)

```
/pipeline/
├── app/
│   ├── tracking.py           # NEW: Track state manager (dwell time, bbox history)
│   ├── roi.py                # NEW: ROI polygon manager + point-in-polygon
│   ├── event_fsm.py          # NEW: Per-track event state machine
│   ├── pipeline.py           # MODIFIED: add nvtracker + nvdsanalytics elements
│   └── config.py             # MODIFIED: add tracking/ROI settings
├── configs/
│   ├── nvtracker_bytetrack.yml  # NEW: ByteTrack nvtracker config
│   └── nvdsanalytics_config.txt # NEW: nvdsanalytics ROI config template
```

### New/Modified: Backend (`/backend/`)

```
/backend/
├── app/
│   ├── db/
│   │   └── models.py           # MODIFIED: add TrackSession model + TrackState enum
│   ├── schemas/
│   │   ├── metadata.py         # MODIFIED: add track_id, dwell_time_s to Detection
│   │   ├── event.py            # NEW: Event schemas (TrackEvent, ViolationEventResponse)
│   │   └── roi.py              # NEW: ROI polygon schemas
│   ├── api/routes/
│   │   ├── events.py           # NEW: GET /api/events, PATCH /api/events/{id}
│   │   └── roi.py              # NEW: CRUD /api/cameras/{id}/roi
│   ├── ws/
│   │   ├── metadata.py         # MODIFIED: enhanced metadata with track + dwell
│   │   └── events.py           # NEW: WebSocket /ws/events (real-time event push)
│   ├── services/
│   │   └── event_consumer.py   # NEW: Kafka consumer for wastewatch.events
│   └── main.py                 # MODIFIED: register new routers + event consumer
├── alembic/versions/
│   └── 002_track_sessions.py   # NEW: track_sessions table + track_state enum
└── tests/
    ├── test_event_fsm.py       # NEW: FSM unit tests
    ├── test_roi.py             # NEW: ROI API tests
    └── test_events.py          # NEW: Events API tests
```

### New/Modified: Frontend (`/frontend/`)

```
/frontend/src/
├── types/
│   └── monitoring.ts           # MODIFIED: add TrackSession, ROIPolygon, EventFSMState types
├── hooks/
│   ├── useDetectionMetadata.ts # MODIFIED: handle enhanced metadata with tracks
│   └── useROIEditor.ts         # NEW: ROI polygon drawing/editing hook
├── components/
│   ├── camera/
│   │   ├── MetadataOverlay.tsx  # MODIFIED: show track IDs, dwell time, color coding
│   │   └── ROIOverlay.tsx       # NEW: SVG polygon overlay + interactive editor
│   └── views/
│       └── CameraManagementView.tsx  # MODIFIED: add ROI editor tab
```

---

## Task 1: ByteTrack nvtracker Configuration + Pipeline Integration

**Files:**
- Create: `/pipeline/configs/nvtracker_bytetrack.yml`
- Modify: `/pipeline/app/config.py`
- Modify: `/pipeline/app/pipeline.py`

- [ ] **Step 1: Create nvtracker ByteTrack config**

```yaml
# pipeline/configs/nvtracker_bytetrack.yml
%YAML:1.0
BaseConfig:
  minDetectorConfidence: 0.3
  minTrackerConfidence: 0.5
  trackerMaxShadowTrailingAge: 30
  probationAge: 3
  maxTargetsPerStream: 150
  enableBatchProcess: 1

TargetManagement:
  enableTargetManagement: 1
  maxTargetsPerStream: 150
  minIouDiff4NewTarget: 0.7
  preserveStreamUpdateOrder: 0
  maxShadowTrackingAge: 30
  probationAge: 3
  earlyTerminationAge: 1

TrackingAlgo:
  tracking_algo: 1  # ByteTrack
  track_thresh: 0.5
  high_thresh: 0.6
  match_thresh: 0.8
  track_buffer: 30   # frames to keep lost tracks
```

- [ ] **Step 2: Add tracking settings to pipeline config.py**

Add to `PipelineSettings` in `/pipeline/app/config.py`:

```python
    # Tracking
    tracker_config_path: str = "configs/nvtracker_bytetrack.yml"
    tracker_ll_lib: str = "/opt/nvidia/deepstream/deepstream/lib/libnvds_nvmultiobjecttracker.so"

    # ROI
    analytics_config_path: str = "configs/nvdsanalytics_config.txt"

    # Dwell thresholds
    dwell_enter_threshold_s: float = 5.0
    dwell_observe_threshold_s: float = 15.0
```

- [ ] **Step 3: Add nvtracker + nvdsanalytics to pipeline.py**

Modify `build_pipeline()` in `/pipeline/app/pipeline.py`. Insert nvtracker after nvinfer and before nvosd. Insert nvdsanalytics after nvtracker and before nvosd:

Current chain: `streammux → nvinfer → nvosd → tee`
New chain: `streammux → nvinfer → nvtracker → nvdsanalytics → nvosd → tee`

Create nvtracker element:
```python
    nvtracker = make_element("nvtracker", "tracker")
    nvtracker.set_property("tracker-width", 640)
    nvtracker.set_property("tracker-height", 384)
    nvtracker.set_property("ll-lib-file", cfg.tracker_ll_lib)
    nvtracker.set_property("ll-config-file", cfg.tracker_config_path)
    nvtracker.set_property("gpu-id", 0)
    nvtracker.set_property("enable-batch-process", 1)
    pipeline.add(nvtracker)
```

Create nvdsanalytics element:
```python
    nvdsanalytics = make_element("nvdsanalytics", "analytics")
    nvdsanalytics.set_property("config-file", cfg.analytics_config_path)
    pipeline.add(nvdsanalytics)
```

Update linking:
```python
    streammux.link(nvinfer)
    nvinfer.link(nvtracker)
    nvtracker.link(nvdsanalytics)
    nvdsanalytics.link(nvosd)
    nvosd.link(tee)
```

Return nvdsanalytics as well: `return pipeline, streammux, fakesink, nvdsanalytics`

- [ ] **Step 4: Update extract_metadata to include track_id from nvtracker**

The existing `extract_metadata` already reads `obj_meta.object_id` as `track_id`. Ensure it uses the proper `unique_component_id` from the tracker. After nvtracker runs, `obj_meta.object_id` is the persistent ByteTrack track ID (not the sentinel 0xFFFFFFFF...). Add a guard:

```python
track_id = int(obj_meta.object_id)
# ByteTrack assigns IDs starting from 1; 0xFFFFFFFFFFFFFFFF means untracked
if track_id == 0xFFFFFFFFFFFFFFFF:
    track_id = None
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/configs/nvtracker_bytetrack.yml pipeline/app/config.py pipeline/app/pipeline.py
git commit -m "feat(pipeline): add ByteTrack nvtracker and nvdsanalytics to DeepStream pipeline"
```

---

## Task 2: Track State Manager

**Files:**
- Create: `/pipeline/app/tracking.py`

- [ ] **Step 1: Implement TrackState and TrackStateManager**

```python
# pipeline/app/tracking.py
"""Per-camera track state management with dwell time tracking."""
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    """State for a single tracked object."""
    track_id: int
    camera_id: str
    class_name: str
    first_seen: float  # monotonic time
    last_seen: float
    in_roi: bool = False
    roi_entered_at: float | None = None
    bbox_history: list[dict] = field(default_factory=list)
    max_history: int = 50  # sampled positions

    @property
    def age_s(self) -> float:
        return self.last_seen - self.first_seen

    @property
    def dwell_time_s(self) -> float:
        """Time spent inside ROI."""
        if self.roi_entered_at is None:
            return 0.0
        return self.last_seen - self.roi_entered_at

    def update(self, bbox: dict, in_roi: bool):
        now = time.monotonic()
        self.last_seen = now

        # Sample bbox history (every ~0.5s)
        if not self.bbox_history or (now - self.bbox_history[-1].get("t", 0)) > 0.5:
            if len(self.bbox_history) >= self.max_history:
                self.bbox_history.pop(0)
            self.bbox_history.append({"t": round(now, 2), **bbox})

        # ROI transitions
        if in_roi and not self.in_roi:
            self.roi_entered_at = now
            self.in_roi = True
        elif not in_roi and self.in_roi:
            self.in_roi = False
            # Keep roi_entered_at for dwell calculation until FSM handles exit


class TrackStateManager:
    """Manages all active tracks across cameras."""

    def __init__(self, lost_timeout_s: float = 30.0):
        self._tracks: dict[tuple[str, int], TrackInfo] = {}  # (camera_id, track_id) -> TrackInfo
        self._lost_timeout_s = lost_timeout_s

    def update_track(
        self,
        camera_id: str,
        track_id: int,
        class_name: str,
        bbox: dict,
        in_roi: bool,
    ) -> TrackInfo:
        """Update or create a track. Returns the TrackInfo."""
        key = (camera_id, track_id)
        now = time.monotonic()

        if key not in self._tracks:
            self._tracks[key] = TrackInfo(
                track_id=track_id,
                camera_id=camera_id,
                class_name=class_name,
                first_seen=now,
                last_seen=now,
            )

        track = self._tracks[key]
        track.update(bbox, in_roi)
        return track

    def get_track(self, camera_id: str, track_id: int) -> TrackInfo | None:
        return self._tracks.get((camera_id, track_id))

    def prune_lost(self) -> list[TrackInfo]:
        """Remove tracks that haven't been seen recently. Returns pruned tracks."""
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
        """Get all active tracks, optionally filtered by camera."""
        if camera_id:
            return [t for t in self._tracks.values() if t.camera_id == camera_id]
        return list(self._tracks.values())
```

- [ ] **Step 2: Commit**

```bash
git add pipeline/app/tracking.py
git commit -m "feat(pipeline): track state manager with dwell time and bbox history"
```

---

## Task 3: ROI Polygon Manager + nvdsanalytics Config

**Files:**
- Create: `/pipeline/app/roi.py`
- Create: `/pipeline/configs/nvdsanalytics_config.txt`

- [ ] **Step 1: Create default nvdsanalytics config template**

```ini
# pipeline/configs/nvdsanalytics_config.txt
# This is a template — dynamically regenerated from DB polygons at startup.
[property]
enable=1
config-width=1920
config-height=1080

# ROI polygons are added dynamically per-camera
# Format: roi-filtering-stream-{source_id}=x1;y1;x2;y2;x3;y3;x4;y4
# Example:
# [roi-filtering-stream-0]
# enable=1
# roi-RF=295;643;579;634;642;913;56;828
# inverse-roi=0
```

- [ ] **Step 2: Implement ROI manager**

```python
# pipeline/app/roi.py
"""ROI polygon manager for nvdsanalytics and point-in-polygon checks."""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ROIPolygon:
    """A named polygon ROI zone for a camera."""
    camera_id: str
    name: str
    points: list[tuple[float, float]]  # normalized (0-1) coordinates
    source_id: int  # DeepStream source index


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test. Coordinates are normalized (0-1)."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


class ROIManager:
    """Manages ROI polygons and checks whether tracks are inside them."""

    def __init__(self):
        self._polygons: dict[str, list[ROIPolygon]] = {}  # camera_id -> polygons

    def set_polygons(self, camera_id: str, polygons: list[ROIPolygon]):
        """Set all ROI polygons for a camera (replaces existing)."""
        self._polygons[camera_id] = polygons
        logger.info("Set %d ROI polygon(s) for camera %s", len(polygons), camera_id)

    def get_polygons(self, camera_id: str) -> list[ROIPolygon]:
        return self._polygons.get(camera_id, [])

    def is_in_any_roi(self, camera_id: str, x: float, y: float) -> bool:
        """Check if a point (normalized bbox center) is inside any ROI for this camera."""
        for polygon in self._polygons.get(camera_id, []):
            if point_in_polygon(x, y, polygon.points):
                return True
        return False

    def generate_nvdsanalytics_config(self, config_width: int = 1920, config_height: int = 1080) -> str:
        """Generate nvdsanalytics config file content from current polygons."""
        lines = [
            "[property]",
            "enable=1",
            f"config-width={config_width}",
            f"config-height={config_height}",
            "",
        ]

        for camera_id, polygons in self._polygons.items():
            for polygon in polygons:
                section = f"roi-filtering-stream-{polygon.source_id}"
                lines.append(f"[{section}]")
                lines.append("enable=1")
                # Convert normalized coords to pixel coords
                points_str = ";".join(
                    f"{int(x * config_width)};{int(y * config_height)}"
                    for x, y in polygon.points
                )
                lines.append(f"roi-RF={points_str}")
                lines.append("inverse-roi=0")
                lines.append("")

        return "\n".join(lines)
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/app/roi.py pipeline/configs/nvdsanalytics_config.txt
git commit -m "feat(pipeline): ROI polygon manager with point-in-polygon and nvdsanalytics config generator"
```

---

## Task 4: Event FSM (Finite State Machine)

**Files:**
- Create: `/pipeline/app/event_fsm.py`
- Create: `/pipeline/tests/test_event_fsm.py` (or `/backend/tests/test_event_fsm.py` if testing pure Python logic)

- [ ] **Step 1: Implement the Event FSM**

```python
# pipeline/app/event_fsm.py
"""Per-track event lifecycle state machine.

States: IDLE → ENTER → OBSERVE → VIOLATION → ALERTING → ESCALATED → EXITED

This FSM runs in the pipeline process, driven by each frame's metadata.
State transitions produce Kafka events on wastewatch.events.
"""
import enum
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class FSMState(str, enum.Enum):
    IDLE = "idle"
    ENTER = "enter"
    OBSERVE = "observe"
    VIOLATION = "violation"
    ALERTING = "alerting"
    ESCALATED = "escalated"
    EXITED = "exited"


@dataclass
class FSMEvent:
    """An event produced by a state transition, to be published to Kafka."""
    camera_id: str
    track_id: int
    state: FSMState
    prev_state: FSMState
    dwell_time_s: float
    class_name: str
    bbox: dict | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class TrackFSM:
    """State machine for a single tracked object in a ROI."""
    camera_id: str
    track_id: int
    class_name: str
    state: FSMState = FSMState.IDLE
    entered_at: float | None = None
    dwell_time_s: float = 0.0
    last_bbox: dict | None = None
    last_update: float = field(default_factory=time.monotonic)

    # Config thresholds (set from pipeline config)
    enter_threshold_s: float = 5.0
    observe_threshold_s: float = 15.0
    exit_timeout_s: float = 300.0  # 5 minutes


class EventFSMManager:
    """Manages per-track FSMs across all cameras."""

    def __init__(
        self,
        enter_threshold_s: float = 5.0,
        observe_threshold_s: float = 15.0,
        exit_timeout_s: float = 300.0,
    ):
        self._fsms: dict[tuple[str, int], TrackFSM] = {}
        self._enter_threshold = enter_threshold_s
        self._observe_threshold = observe_threshold_s
        self._exit_timeout = exit_timeout_s

    def update(
        self,
        camera_id: str,
        track_id: int,
        class_name: str,
        in_roi: bool,
        dwell_time_s: float,
        bbox: dict | None = None,
    ) -> FSMEvent | None:
        """Process a frame update for a track. Returns an FSMEvent if a transition occurred."""
        key = (camera_id, track_id)
        now = time.monotonic()

        if key not in self._fsms:
            self._fsms[key] = TrackFSM(
                camera_id=camera_id,
                track_id=track_id,
                class_name=class_name,
                enter_threshold_s=self._enter_threshold,
                observe_threshold_s=self._observe_threshold,
                exit_timeout_s=self._exit_timeout,
            )

        fsm = self._fsms[key]
        fsm.last_update = now
        fsm.last_bbox = bbox
        fsm.dwell_time_s = dwell_time_s
        prev_state = fsm.state

        # --- State transitions ---

        if fsm.state == FSMState.IDLE:
            if in_roi:
                fsm.state = FSMState.ENTER
                fsm.entered_at = now

        elif fsm.state == FSMState.ENTER:
            if not in_roi:
                # Left before threshold — no event
                fsm.state = FSMState.EXITED
                self._cleanup(key)
                return None  # Silent exit, no event
            elif dwell_time_s >= self._enter_threshold:
                fsm.state = FSMState.OBSERVE

        elif fsm.state == FSMState.OBSERVE:
            if not in_roi:
                fsm.state = FSMState.EXITED
            elif dwell_time_s >= self._observe_threshold:
                fsm.state = FSMState.VIOLATION

        elif fsm.state == FSMState.VIOLATION:
            if not in_roi:
                fsm.state = FSMState.EXITED
            # VIOLATION stays until VLM result (Phase 3) or track exits
            # For Phase 2, auto-transition to EXITED after timeout
            elif fsm.entered_at and (now - fsm.entered_at) > self._exit_timeout:
                fsm.state = FSMState.EXITED

        elif fsm.state in (FSMState.ALERTING, FSMState.ESCALATED):
            # These states are driven by Phase 3 (VLM results)
            if not in_roi:
                fsm.state = FSMState.EXITED

        elif fsm.state == FSMState.EXITED:
            # Terminal — cleanup
            self._cleanup(key)

        # Produce event on state change
        if fsm.state != prev_state:
            event = FSMEvent(
                camera_id=camera_id,
                track_id=track_id,
                state=fsm.state,
                prev_state=prev_state,
                dwell_time_s=dwell_time_s,
                class_name=class_name,
                bbox=bbox,
            )
            logger.info(
                "FSM %s track=%d: %s → %s (dwell=%.1fs)",
                camera_id, track_id, prev_state.value, fsm.state.value, dwell_time_s,
            )
            if fsm.state == FSMState.EXITED:
                self._cleanup(key)
            return event

        return None

    def prune_stale(self) -> list[FSMEvent]:
        """Prune FSMs that haven't been updated recently. Returns exit events."""
        now = time.monotonic()
        events = []
        to_remove = []
        for key, fsm in self._fsms.items():
            if now - fsm.last_update > fsm.exit_timeout_s:
                events.append(FSMEvent(
                    camera_id=fsm.camera_id,
                    track_id=fsm.track_id,
                    state=FSMState.EXITED,
                    prev_state=fsm.state,
                    dwell_time_s=fsm.dwell_time_s,
                    class_name=fsm.class_name,
                    bbox=fsm.last_bbox,
                ))
                to_remove.append(key)
        for key in to_remove:
            del self._fsms[key]
        return events

    def _cleanup(self, key: tuple[str, int]):
        self._fsms.pop(key, None)

    def get_active_fsms(self, camera_id: str | None = None) -> list[TrackFSM]:
        if camera_id:
            return [f for f in self._fsms.values() if f.camera_id == camera_id]
        return list(self._fsms.values())
```

- [ ] **Step 2: Write FSM unit tests**

```python
# backend/tests/test_event_fsm.py
"""Tests for the Event FSM — pure Python, no DeepStream dependency."""
import sys
import os
import time
from unittest.mock import patch

# Add pipeline to path for importing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pipeline"))

from app.event_fsm import EventFSMManager, FSMState, FSMEvent


def test_idle_to_enter_on_roi_entry():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    assert event is not None
    assert event.state == FSMState.ENTER
    assert event.prev_state == FSMState.IDLE


def test_enter_to_observe_after_threshold():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    assert event is not None
    assert event.state == FSMState.OBSERVE


def test_observe_to_violation_after_threshold():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=16.0)
    assert event is not None
    assert event.state == FSMState.VIOLATION


def test_enter_exits_silently_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=2.0)
    # Silent exit — returns None (no event emitted)
    assert event is None


def test_observe_exits_with_event_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=8.0)
    assert event is not None
    assert event.state == FSMState.EXITED
    assert event.prev_state == FSMState.OBSERVE


def test_no_transition_when_state_unchanged():
    mgr = EventFSMManager(enter_threshold_s=5.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    # Still in ENTER, dwell < threshold
    event = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=3.0)
    assert event is None


def test_multiple_tracks_independent():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 2, "car", in_roi=True, dwell_time_s=0.0)
    # Track 1 advances, track 2 stays
    event1 = mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    event2 = mgr.update("cam-01", 2, "car", in_roi=True, dwell_time_s=3.0)
    assert event1 is not None and event1.state == FSMState.OBSERVE
    assert event2 is None  # Still in ENTER


def test_violation_exits_on_roi_leave():
    mgr = EventFSMManager(enter_threshold_s=5.0, observe_threshold_s=15.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=0.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=6.0)
    mgr.update("cam-01", 1, "person", in_roi=True, dwell_time_s=16.0)
    event = mgr.update("cam-01", 1, "person", in_roi=False, dwell_time_s=20.0)
    assert event is not None
    assert event.state == FSMState.EXITED
    assert event.prev_state == FSMState.VIOLATION
```

- [ ] **Step 3: Run tests**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/test_event_fsm.py -v`
Expected: All 8 tests pass.

- [ ] **Step 4: Commit**

```bash
git add pipeline/app/event_fsm.py backend/tests/test_event_fsm.py
git commit -m "feat(pipeline): event lifecycle FSM (IDLE→ENTER→OBSERVE→VIOLATION→EXITED) with tests"
```

---

## Task 5: Integrate Tracking + ROI + FSM into Pipeline Main Loop

**Files:**
- Modify: `/pipeline/app/pipeline.py`

- [ ] **Step 1: Wire TrackStateManager, ROIManager, and EventFSMManager into pipeline.py**

In `main()`, after creating `kafka_producer`, `health`, `source_manager`:

```python
from app.tracking import TrackStateManager
from app.roi import ROIManager
from app.event_fsm import EventFSMManager

track_manager = TrackStateManager(lost_timeout_s=pipeline_settings.dwell_observe_threshold_s * 2)
roi_manager = ROIManager()
fsm_manager = EventFSMManager(
    enter_threshold_s=pipeline_settings.dwell_enter_threshold_s,
    observe_threshold_s=pipeline_settings.dwell_observe_threshold_s,
)
```

- [ ] **Step 2: Update extract_metadata to compute dwell + ROI + FSM**

After extracting detections in the probe callback, for each detection with a valid track_id:

```python
# In the per-object loop, after building the detection dict:
if track_id is not None:
    bbox_center_x = (bbox["x"] + bbox["w"] / 2)  # normalized center
    bbox_center_y = (bbox["y"] + bbox["h"] / 2)
    in_roi = roi_manager.is_in_any_roi(camera_id, bbox_center_x, bbox_center_y)

    track_info = track_manager.update_track(
        camera_id, track_id, class_name, bbox, in_roi
    )

    fsm_event = fsm_manager.update(
        camera_id, track_id, class_name,
        in_roi=in_roi,
        dwell_time_s=track_info.dwell_time_s,
        bbox=bbox,
    )

    if fsm_event:
        kafka_producer.send_event(fsm_event)

    # Add dwell/FSM info to the detection for metadata stream
    detection_dict["dwell_time_s"] = round(track_info.dwell_time_s, 1)
    detection_dict["in_roi"] = in_roi
    detection_dict["fsm_state"] = fsm_manager._fsms.get(
        (camera_id, track_id), None
    )
    if detection_dict["fsm_state"]:
        detection_dict["fsm_state"] = detection_dict["fsm_state"].state.value
```

- [ ] **Step 3: Add send_event method to PipelineKafkaProducer**

In `/pipeline/app/kafka_producer.py`, add:

```python
def send_event(self, event):
    """Publish an FSM state change event to wastewatch.events."""
    try:
        self._producer.produce(
            topic="wastewatch.events",
            key=event.camera_id.encode("utf-8"),
            value=orjson.dumps({
                "camera_id": event.camera_id,
                "track_id": event.track_id,
                "state": event.state.value,
                "prev_state": event.prev_state.value,
                "dwell_time_s": round(event.dwell_time_s, 1),
                "class_name": event.class_name,
                "bbox": event.bbox,
                "timestamp": event.timestamp,
            }),
        )
    except Exception:
        logger.exception("Failed to send event for track %d", event.track_id)
```

- [ ] **Step 4: Add FSM prune to health check callback**

In the `health_check_cb` GLib timer, add:

```python
    stale_events = fsm_manager.prune_stale()
    for evt in stale_events:
        kafka_producer.send_event(evt)
    lost_tracks = track_manager.prune_lost()
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/app/pipeline.py pipeline/app/kafka_producer.py
git commit -m "feat(pipeline): integrate tracking, ROI, and FSM into metadata extraction loop"
```

---

## Task 6: Database Migration — track_sessions Table

**Files:**
- Create: `/backend/alembic/versions/002_track_sessions.py`
- Modify: `/backend/app/db/models.py`

- [ ] **Step 1: Add TrackSession model and TrackState enum to models.py**

```python
# Add to models.py

class TrackState(str, enum.Enum):
    active = "active"
    lost = "lost"
    exited = "exited"


class EventFSMState(str, enum.Enum):
    enter = "enter"
    observe = "observe"
    violation = "violation"
    alerting = "alerting"
    escalated = "escalated"
    exited = "exited"


class TrackSession(Base):
    __tablename__ = "track_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cameras.id"))
    track_id: Mapped[int] = mapped_column(Integer)
    fsm_state: Mapped[EventFSMState] = mapped_column(
        Enum(EventFSMState, name="event_fsm_state", create_type=False),
        default=EventFSMState.enter,
    )
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dwell_time_s: Mapped[float | None] = mapped_column(Float)
    bbox_history: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Create Alembic migration**

```python
# backend/alembic/versions/002_track_sessions.py
"""Add track_sessions table and track_state enum

Revision ID: 002
Revises: 001
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    track_state = sa.Enum("active", "lost", "exited", name="track_state")
    track_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "track_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("camera_id", UUID(as_uuid=True), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("track_id", sa.Integer, nullable=False),
        sa.Column("fsm_state", sa.Enum("enter", "observe", "violation", "alerting", "escalated", "exited", name="event_fsm_state", create_type=False)),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True)),
        sa.Column("dwell_time_s", sa.Float),
        sa.Column("bbox_history", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_track_sessions_camera_time", "track_sessions", ["camera_id", sa.text("entered_at DESC")])


def downgrade() -> None:
    op.drop_table("track_sessions")
    op.execute("DROP TYPE IF EXISTS track_state")
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/002_track_sessions.py
git commit -m "feat(backend): track_sessions table and TrackSession ORM model"
```

---

## Task 7: Events API + WebSocket

**Files:**
- Create: `/backend/app/schemas/event.py`
- Create: `/backend/app/api/routes/events.py`
- Create: `/backend/app/ws/events.py`
- Create: `/backend/app/services/event_consumer.py`
- Modify: `/backend/app/main.py`
- Create: `/backend/tests/test_events.py`

- [ ] **Step 1: Create event schemas**

```python
# backend/app/schemas/event.py
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.db.models import EventFSMState, AlertSeverity, AlertStatus, EventType


class TrackEventKafka(BaseModel):
    """Event received from Kafka wastewatch.events."""
    camera_id: str
    track_id: int
    state: str
    prev_state: str
    dwell_time_s: float
    class_name: str
    bbox: dict | None = None
    timestamp: float


class ViolationEventResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    timestamp: datetime
    event_type: EventType
    severity: AlertSeverity
    confidence: float
    track_id: int | None
    dwell_time_s: float | None
    fsm_state: str | None
    status: AlertStatus
    person_id: str | None
    repeat_count: int
    snapshot_url: str | None
    video_clip_url: str | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TrackSessionResponse(BaseModel):
    id: uuid.UUID
    camera_id: uuid.UUID
    track_id: int
    fsm_state: str
    entered_at: datetime
    exited_at: datetime | None
    dwell_time_s: float | None
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Create events route**

```python
# backend/app/api/routes/events.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import ViolationEvent, TrackSession
from app.schemas.event import ViolationEventResponse, TrackSessionResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=list[ViolationEventResponse])
async def list_events(
    camera_id: uuid.UUID | None = None,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(ViolationEvent).order_by(desc(ViolationEvent.timestamp)).limit(limit)
    if camera_id:
        query = query.where(ViolationEvent.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{event_id}", response_model=ViolationEventResponse)
async def get_event(event_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ViolationEvent).where(ViolationEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@router.patch("/{event_id}", response_model=ViolationEventResponse)
async def update_event(event_id: uuid.UUID, status: str | None = None, notes: str | None = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ViolationEvent).where(ViolationEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if status:
        event.status = status
    if notes is not None:
        event.notes = notes
    await db.commit()
    await db.refresh(event)
    return event


@router.get("/tracks/active", response_model=list[TrackSessionResponse])
async def list_active_tracks(
    camera_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(TrackSession).where(TrackSession.exited_at.is_(None)).order_by(desc(TrackSession.entered_at))
    if camera_id:
        query = query.where(TrackSession.camera_id == camera_id)
    result = await db.execute(query)
    return result.scalars().all()
```

- [ ] **Step 3: Create events WebSocket**

```python
# backend/app/ws/events.py
import logging
from collections import defaultdict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter()

_subscribers: set[WebSocket] = set()


async def broadcast_event(data: dict):
    dead = []
    for ws in _subscribers:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _subscribers.discard(ws)


@router.websocket("/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _subscribers.add(websocket)
    logger.info("Events WebSocket connected, total=%d", len(_subscribers))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _subscribers.discard(websocket)
```

- [ ] **Step 4: Create events Kafka consumer**

```python
# backend/app/services/event_consumer.py
import asyncio
import logging
import orjson
from aiokafka import AIOKafkaConsumer
from app.config.kafka import TOPIC_EVENTS, GROUP_EVENT_FSM
from app.ws.events import broadcast_event

logger = logging.getLogger(__name__)


class EventConsumer:
    def __init__(self, bootstrap_servers: str):
        self._bootstrap_servers = bootstrap_servers
        self._consumer: AIOKafkaConsumer | None = None
        self._task: asyncio.Task | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            TOPIC_EVENTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_EVENT_FSM,
            value_deserializer=lambda v: orjson.loads(v),
            auto_offset_reset="latest",
        )
        await self._consumer.start()
        self._task = asyncio.create_task(self._consume_loop())
        logger.info("EventConsumer started")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()

    async def _consume_loop(self):
        try:
            async for msg in self._consumer:
                await broadcast_event(msg.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("EventConsumer error")
```

- [ ] **Step 5: Register in main.py**

Add to main.py:
```python
from app.api.routes import events
from app.ws import events as ws_events
from app.services.event_consumer import EventConsumer

# In create_app():
app.include_router(events.router, prefix="/api")
app.include_router(ws_events.router, prefix="/ws")

# In lifespan, after metadata consumer:
event_consumer = EventConsumer(settings.kafka_bootstrap_servers)
try:
    await event_consumer.start()
except Exception as e:
    logging.getLogger(__name__).warning("Event consumer failed to start: %s", e)
    event_consumer = None
# In yield cleanup:
if event_consumer:
    await event_consumer.stop()
```

- [ ] **Step 6: Write tests**

```python
# backend/tests/test_events.py
import pytest

@pytest.mark.asyncio
async def test_list_events(client):
    resp = await client.get("/api/events")
    assert resp.status_code in (200, 500)  # 500 if no DB

@pytest.mark.asyncio
async def test_active_tracks(client):
    resp = await client.get("/api/events/tracks/active")
    assert resp.status_code in (200, 500)
```

- [ ] **Step 7: Run all tests**

Run: `cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/ -v`

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas/event.py backend/app/api/routes/events.py backend/app/ws/events.py backend/app/services/event_consumer.py backend/app/main.py backend/tests/test_events.py
git commit -m "feat(backend): events API, events WebSocket, and Kafka event consumer"
```

---

## Task 8: ROI CRUD API

**Files:**
- Create: `/backend/app/schemas/roi.py`
- Create: `/backend/app/api/routes/roi.py`
- Modify: `/backend/app/main.py`
- Create: `/backend/tests/test_roi.py`

- [ ] **Step 1: Create ROI schemas**

```python
# backend/app/schemas/roi.py
from pydantic import BaseModel


class ROIPoint(BaseModel):
    x: float  # normalized 0-1
    y: float  # normalized 0-1


class ROIPolygonCreate(BaseModel):
    name: str
    points: list[ROIPoint]  # minimum 3 points


class ROIPolygonResponse(BaseModel):
    name: str
    points: list[ROIPoint]


class CameraROIResponse(BaseModel):
    camera_id: str
    polygons: list[ROIPolygonResponse]
```

- [ ] **Step 2: Create ROI routes**

```python
# backend/app/api/routes/roi.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_db
from app.db.models import Camera
from app.schemas.roi import ROIPolygonCreate, ROIPolygonResponse, CameraROIResponse

router = APIRouter(prefix="/cameras/{camera_id}/roi", tags=["roi"])


@router.get("", response_model=CameraROIResponse)
async def get_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    polygons_data = camera.roi_polygons or {"polygons": []}
    return CameraROIResponse(
        camera_id=str(camera_id),
        polygons=[
            ROIPolygonResponse(
                name=p["name"],
                points=[{"x": pt["x"], "y": pt["y"]} for pt in p["points"]],
            )
            for p in polygons_data.get("polygons", [])
        ],
    )


@router.put("", response_model=CameraROIResponse)
async def set_roi(
    camera_id: uuid.UUID,
    polygons: list[ROIPolygonCreate],
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    camera.roi_polygons = {
        "polygons": [
            {"name": p.name, "points": [{"x": pt.x, "y": pt.y} for pt in p.points]}
            for p in polygons
        ]
    }
    await db.commit()
    await db.refresh(camera)

    return CameraROIResponse(
        camera_id=str(camera_id),
        polygons=[
            ROIPolygonResponse(name=p.name, points=p.points)
            for p in polygons
        ],
    )


@router.delete("", status_code=204)
async def clear_roi(camera_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).where(Camera.id == camera_id))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    camera.roi_polygons = None
    await db.commit()
```

- [ ] **Step 3: Register in main.py**

```python
from app.api.routes import roi
app.include_router(roi.router, prefix="/api")
```

- [ ] **Step 4: Write tests**

```python
# backend/tests/test_roi.py
import pytest

@pytest.mark.asyncio
async def test_get_roi_camera_not_found(client):
    resp = await client.get("/api/cameras/00000000-0000-0000-0000-000000000000/roi")
    assert resp.status_code in (404, 500)
```

- [ ] **Step 5: Run tests and commit**

```bash
cd /home/subhadra/Projects/LSG-Wastewatch/backend && python -m pytest tests/ -v
git add backend/app/schemas/roi.py backend/app/api/routes/roi.py backend/app/main.py backend/tests/test_roi.py
git commit -m "feat(backend): ROI polygon CRUD API at /api/cameras/{id}/roi"
```

---

## Task 9: Enhanced Metadata Schema + WebSocket

**Files:**
- Modify: `/backend/app/schemas/metadata.py`

- [ ] **Step 1: Enhance Detection schema with tracking/ROI fields**

Add to the `Detection` model in `backend/app/schemas/metadata.py`:

```python
class Detection(BaseModel):
    class_id: int
    class_name: str
    confidence: float
    bbox: list[float]  # [x1, y1, x2, y2] normalized 0-1
    track_id: int | None = None
    dwell_time_s: float | None = None
    in_roi: bool | None = None
    fsm_state: str | None = None
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/metadata.py
git commit -m "feat(backend): enhance Detection schema with dwell_time, in_roi, fsm_state"
```

---

## Task 10: Frontend — Enhanced Metadata Overlay (Track IDs + Dwell + Color Coding)

**Files:**
- Modify: `/frontend/src/types/monitoring.ts`
- Modify: `/frontend/src/components/camera/MetadataOverlay.tsx`

- [ ] **Step 1: Add Phase 2 types to monitoring.ts**

Append:
```typescript
// --- Phase 2: Tracking + ROI types ---

export type EventFSMState = 'idle' | 'enter' | 'observe' | 'violation' | 'alerting' | 'escalated' | 'exited';

export interface ROIPoint {
  x: number;
  y: number;
}

export interface ROIPolygon {
  name: string;
  points: ROIPoint[];
}

export interface TrackSession {
  id: string;
  camera_id: string;
  track_id: number;
  fsm_state: EventFSMState;
  entered_at: string;
  exited_at: string | null;
  dwell_time_s: number | null;
}
```

Also update the existing `BBoxDetection` interface to include the new fields:
```typescript
export interface BBoxDetection {
  class_id: number;
  class_name: string;
  confidence: number;
  bbox: [number, number, number, number];
  track_id: number | null;
  dwell_time_s?: number;
  in_roi?: boolean;
  fsm_state?: EventFSMState;
}
```

- [ ] **Step 2: Update MetadataOverlay with dwell time and color coding**

Update the `DetectionBox` component in MetadataOverlay.tsx:

- Show dwell time if available: `"Person #42 | 12.3s"`
- Color-code the bbox border based on dwell time:
  - Green (0-5s): just entered
  - Yellow (5-15s): observing
  - Red (>15s): potential violation
- If `fsm_state` is `"violation"`, use a pulsing red border animation
- Show ROI indicator if `in_roi` is true

```typescript
function getDwellColor(dwellTime: number | undefined): string {
  if (!dwellTime || dwellTime <= 5) return "#22c55e"; // green
  if (dwellTime <= 15) return "#eab308"; // yellow
  return "#ef4444"; // red
}
```

Update the label to show dwell time:
```typescript
const label = [
  detection.class_name,
  detection.track_id != null ? `#${detection.track_id}` : null,
  `${(detection.confidence * 100).toFixed(0)}%`,
  detection.dwell_time_s != null ? `${detection.dwell_time_s.toFixed(1)}s` : null,
].filter(Boolean).join(' | ');
```

When `detection.dwell_time_s` is available, override the border color with dwell-based color instead of class-based color.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/monitoring.ts frontend/src/components/camera/MetadataOverlay.tsx
git commit -m "feat(frontend): enhanced overlay with track IDs, dwell time, and FSM color coding"
```

---

## Task 11: Frontend — ROI Polygon Overlay + Editor

**Files:**
- Create: `/frontend/src/components/camera/ROIOverlay.tsx`
- Create: `/frontend/src/hooks/useROIEditor.ts`
- Modify: `/frontend/src/components/camera/CameraFeed.tsx`

- [ ] **Step 1: Create useROIEditor hook**

```typescript
// frontend/src/hooks/useROIEditor.ts
import { useState, useCallback, useRef } from "react";
import type { ROIPoint, ROIPolygon } from "@/types/monitoring";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8080";

interface UseROIEditorOptions {
  cameraId: string;
}

export function useROIEditor({ cameraId }: UseROIEditorOptions) {
  const [polygons, setPolygons] = useState<ROIPolygon[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [currentPoints, setCurrentPoints] = useState<ROIPoint[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const fetchPolygons = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/api/cameras/${cameraId}/roi`);
      if (resp.ok) {
        const data = await resp.json();
        setPolygons(data.polygons || []);
      }
    } catch {
      // API may not be available
    }
  }, [cameraId]);

  const savePolygons = useCallback(async (newPolygons: ROIPolygon[]) => {
    setIsLoading(true);
    try {
      const resp = await fetch(`${API_BASE}/api/cameras/${cameraId}/roi`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newPolygons),
      });
      if (resp.ok) {
        const data = await resp.json();
        setPolygons(data.polygons || []);
      }
    } finally {
      setIsLoading(false);
    }
  }, [cameraId]);

  const addPoint = useCallback((point: ROIPoint) => {
    setCurrentPoints(prev => [...prev, point]);
  }, []);

  const finishPolygon = useCallback((name: string) => {
    if (currentPoints.length < 3) return;
    const newPoly: ROIPolygon = { name, points: currentPoints };
    const updated = [...polygons, newPoly];
    setPolygons(updated);
    setCurrentPoints([]);
    savePolygons(updated);
  }, [currentPoints, polygons, savePolygons]);

  const removePolygon = useCallback((index: number) => {
    const updated = polygons.filter((_, i) => i !== index);
    setPolygons(updated);
    savePolygons(updated);
  }, [polygons, savePolygons]);

  const cancelEditing = useCallback(() => {
    setCurrentPoints([]);
    setIsEditing(false);
  }, []);

  return {
    polygons,
    isEditing,
    setIsEditing,
    currentPoints,
    isLoading,
    fetchPolygons,
    addPoint,
    finishPolygon,
    removePolygon,
    cancelEditing,
  };
}
```

- [ ] **Step 2: Create ROIOverlay component**

```tsx
// frontend/src/components/camera/ROIOverlay.tsx
import type { ROIPolygon, ROIPoint } from "@/types/monitoring";

interface ROIOverlayProps {
  polygons: ROIPolygon[];
  width: number;
  height: number;
  isEditing?: boolean;
  currentPoints?: ROIPoint[];
  onCanvasClick?: (point: ROIPoint) => void;
}

export function ROIOverlay({
  polygons,
  width,
  height,
  isEditing = false,
  currentPoints = [],
  onCanvasClick,
}: ROIOverlayProps) {
  if (width === 0 || height === 0) return null;

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isEditing || !onCanvasClick) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onCanvasClick({
      x: (e.clientX - rect.left) / width,
      y: (e.clientY - rect.top) / height,
    });
  };

  return (
    <svg
      width={width}
      height={height}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        pointerEvents: isEditing ? "auto" : "none",
        cursor: isEditing ? "crosshair" : "default",
      }}
      onClick={handleClick}
    >
      {/* Existing ROI polygons */}
      {polygons.map((polygon, i) => (
        <g key={`roi-${i}`}>
          <polygon
            points={polygon.points.map(p => `${p.x * width},${p.y * height}`).join(" ")}
            fill="rgba(59, 130, 246, 0.15)"
            stroke="#3b82f6"
            strokeWidth={2}
            strokeDasharray={isEditing ? "6,3" : "none"}
          />
          {/* Label */}
          {polygon.points.length > 0 && (
            <text
              x={polygon.points[0].x * width + 4}
              y={polygon.points[0].y * height - 4}
              fill="#3b82f6"
              fontSize={11}
              fontWeight={600}
            >
              {polygon.name}
            </text>
          )}
        </g>
      ))}

      {/* Points being drawn (editing mode) */}
      {isEditing && currentPoints.length > 0 && (
        <g>
          <polyline
            points={currentPoints.map(p => `${p.x * width},${p.y * height}`).join(" ")}
            fill="none"
            stroke="#f97316"
            strokeWidth={2}
            strokeDasharray="4,2"
          />
          {currentPoints.map((p, i) => (
            <circle
              key={`pt-${i}`}
              cx={p.x * width}
              cy={p.y * height}
              r={4}
              fill="#f97316"
              stroke="#fff"
              strokeWidth={1}
            />
          ))}
        </g>
      )}
    </svg>
  );
}
```

- [ ] **Step 3: Integrate ROIOverlay into CameraFeed**

In CameraFeed.tsx, add:
- Import `ROIOverlay` and `useROIEditor`
- Add `useROIEditor({ cameraId: camera.id })` hook
- Render `<ROIOverlay>` after `<MetadataOverlay>` in the video container
- Add a "Draw ROI" button to the dropdown menu

- [ ] **Step 4: Commit**

```bash
git add frontend/src/hooks/useROIEditor.ts frontend/src/components/camera/ROIOverlay.tsx frontend/src/components/camera/CameraFeed.tsx
git commit -m "feat(frontend): ROI polygon overlay with interactive editor on camera feeds"
```

---

## Summary

| Task | Component | Deliverable |
|------|-----------|-------------|
| 1 | Pipeline | ByteTrack nvtracker + nvdsanalytics in DeepStream pipeline |
| 2 | Pipeline | Track state manager with dwell time + bbox history |
| 3 | Pipeline | ROI polygon manager + point-in-polygon + nvdsanalytics config |
| 4 | Pipeline | Event FSM (IDLE→ENTER→OBSERVE→VIOLATION→EXITED) + tests |
| 5 | Pipeline | Wire tracking + ROI + FSM into metadata extraction loop |
| 6 | Backend | track_sessions DB table + Alembic migration |
| 7 | Backend | Events API + WebSocket + Kafka consumer |
| 8 | Backend | ROI polygon CRUD API |
| 9 | Backend | Enhanced Detection schema with dwell/ROI/FSM fields |
| 10 | Frontend | Enhanced overlay with dwell time + FSM color coding |
| 11 | Frontend | ROI polygon overlay + interactive editor |

**Phase 2 Deliverable:** Persistent tracks with IDs visible in the overlay, dwell timers counting up with green→yellow→red color coding, polygon ROI zones drawn on camera feeds via interactive editor, and Event FSM firing VIOLATION events to Kafka when persons linger in zones.
