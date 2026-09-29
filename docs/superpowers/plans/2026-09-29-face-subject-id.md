# Face-Based Repeat-Violator Subject IDs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a violation alert fires, capture the violator's face from a clean (pre-overlay) frame, assign an anonymous subject ID (`S-001`), link repeat violations by the same person to that ID, and show the result on the dashboard with an operator review/correction workflow.

**Architecture:**
- **Pipeline.** The DeepStream pipeline gains a clean-frame branch before `nvdsosd`. It keeps a per-track ring buffer of head crops in memory. On every violation alert it writes that person's crops to a private volume and publishes `wastewatch.faces.requests` with a new `incident_id`, which also goes on the alert itself.
- **`subject-worker`.** A new CPU worker runs SCRFD, a quality gate and ArcFace on the crops. It matches them against a Qdrant index, applies automatic / review / new rules and stores everything in Postgres.
- **API and dashboard.** The API joins subject data onto `GET /api/alerts` and exposes `/api/subjects/*` for the new dashboard view.

**Tech Stack:**
- Pipeline: DeepStream 7.0 / GStreamer, Python 3.10.
- Backend: FastAPI + SQLAlchemy 2 async + Alembic, Python 3.12.
- Messaging and storage: aiokafka, qdrant-client, Postgres.
- Face models: onnxruntime (CPU) + OpenCV, SCRFD `det_10g.onnx` and ArcFace `w600k_r50.onnx` from InsightFace buffalo_l.
- Frontend: React 18 + Vite + TypeScript + shadcn/ui + vitest.

**Spec:** `docs/superpowers/specs/2026-09-29-face-subject-id-design.md`

## Global Constraints

- Work on branch `feat/face-subject-id`. Commit as `darshan-satyaki <darshan.m@satyaki.co.in>`, which is already set in the repo-local git config. **Never `git push`.**
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Pipeline code runs on Python 3.10.** Don't use `match`; `X | None` is fine.
- `pipeline/app/pipeline.py` imports `pyds`/`gi`, so it cannot be imported in tests. All testable logic goes in pure modules.
- Matching thresholds are inclusive lower bounds: `score >= WW_FACE_MATCH_AUTO` (default 0.55) means auto, and `score >= WW_FACE_MATCH_REVIEW` (default 0.40) means review.
- Display code is `f"S-{code_num:03d}"`. Codes are never reused.
- UI copy is neutral: "Subject", "Repeat", "incidents". Never "offender" or "culprit" in any user-facing string.
- **Face images:**
  - They only ever live under `WW_FACES_PRIVATE_DIR` (default `/app/faces_private`), in `incoming/<incident_id>/` or `subjects/<subject_id>/`.
  - They must never be written under `/app/evidence` or any `StaticFiles` mount.
- Every `/api/subjects*` route depends on `require_admin` and writes a `subject_access_log` row.
- **Env vars and defaults** (spec §12):

  | Var | Default |
  |---|---|
  | `WW_FACE_CAPTURE_ENABLED` | `0` in code |
  | `WW_FACE_CAPTURE_INTERVAL_S` | `0.5` |
  | `WW_FACE_POSTROLL_S` | `2.0` |
  | `WW_FACE_MIN_DET` | `0.6` |
  | `WW_FACE_MIN_PX` | `40` |
  | `WW_FACE_MIN_SHARPNESS` | `60` |
  | `WW_FACE_MAX_YAW` | `0.45` |
  | `WW_FACE_MATCH_AUTO` | `0.55` |
  | `WW_FACE_MATCH_REVIEW` | `0.40` |
  | `WW_SUBJECT_MAX_FACES` | `10` |
  | `WW_FACE_KEEP_PER_INCIDENT` | `3` |
  | `WW_SUBJECT_RETENTION_DAYS` | `90` |
  | `WW_FACE_INCOMING_RETENTION_DAYS` | `7` |
  | `WW_FACE_DET_MODEL` | `/app/models/face/det_10g.onnx` |
  | `WW_FACE_REC_MODEL` | `/app/models/face/w600k_r50.onnx` |
  | `WW_FACES_PRIVATE_DIR` | `/app/faces_private` |
  | `WW_QDRANT_URL` | `http://qdrant:6333` |
  | `WW_FACE_COLLECTION` | `offender_faces` |

- Kafka topic `wastewatch.faces.requests`; consumer group `wastewatch.group.subject-worker`.
- **Commands.** Backend commands run from `backend/` with the venv from Task 1: `.venv/Scripts/python` on Windows, `.venv/bin/python` on Linux. The plan writes `.venv/Scripts/python`.
  - Pipeline tests: `cd pipeline && ../backend/.venv/Scripts/python -m pytest tests/<file> -v`.
  - Frontend commands run from `frontend/`.

## Review Focus

1. **Two alerts for the same person within the 2 s post-roll** (e.g. two bags dropped back to back). Each `incident_id` needs its own crop folder and its own `face_incidents` row, and the second one must attach to the first one's subject, not be lost. *Pinned by `test_flush_due_writes_each_incident_to_its_own_folder` (Task 2) and `test_same_face_twice_is_auto_repeat` (Task 8).*
2. **Kafka redelivers a request, or the worker restarts mid-processing.** The same `incident_id` must never be counted twice. *Pinned by `test_duplicate_incident_is_ignored` (Task 8) and `test_consume_loop_commits_after_each_message` (Task 11).*
3. **A `faces.requests` payload whose `crops` point outside `incoming/<incident_id>/`** (e.g. `../../etc/passwd` or another incident's folder). Those paths must be ignored, never read, moved or deleted. *Pinned by `test_from_payload_drops_paths_outside_incident_folder` (Task 6) and `test_handle_ignores_traversal_paths` (Task 11).*
4. **An operator acts on a stale review.** This covers a double click, two operators, or a subject that was merged or deleted in the meantime. The action must return 409/404 and change nothing. *Pinned by `test_merge_twice_conflicts`, `test_merge_when_source_no_longer_active_conflicts` and `test_merge_after_candidate_deleted_is_not_found` (Task 9), plus `test_merge_route_conflict_is_409` (Task 13).*
5. **Qdrant is down while an incident is processed.** The incident must still be stored (faces kept with `qdrant_synced=false`) and indexed later by the resync. *Pinned by `test_qdrant_failure_keeps_face_unsynced` (Task 8) and `test_resync_indexes_unsynced_faces` (Task 10).*

---

### Task 1: Backend dev environment and dependencies

**Files:**
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `backend/.venv` with the deps every later backend and pipeline test uses.

- [ ] **Step 1: Add runtime and dev dependencies**

In `backend/pyproject.toml`, add to `dependencies` (after `"Pillow>=10.0.0",`):

```toml
    "onnxruntime>=1.20.0",
    "opencv-python-headless>=4.10.0",
    "numpy>=1.26.0",
```

and to `[project.optional-dependencies] dev`:

```toml
    "aiosqlite>=0.20.0",
```

- [ ] **Step 2: Create the venv with the packages the tests need**

`sentence-transformers` (torch) and `piper-tts` are deliberately left out. They are heavy, and nothing in this feature needs them.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip
.venv/Scripts/python -m pip install fastapi "uvicorn[standard]" "sqlalchemy[asyncio]>=2.0.36" asyncpg alembic aiokafka pydantic-settings orjson redis httpx "python-jose[cryptography]" "pydantic[email]" aiofiles psutil "qdrant-client>=1.12.0" Pillow "onnxruntime>=1.20.0" "opencv-python-headless>=4.10.0" "numpy>=1.26.0" tzdata pytest pytest-asyncio aiosqlite
```

Expected: ends with `Successfully installed ...`.

- [ ] **Step 3: Record the baseline**

```bash
cd backend
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -25
```

Expected: most tests pass. Some existing tests fail or error because they need packages we didn't install or a live service. One known pre-existing failure is `tests/test_event_fsm.py::test_force_violation_overrides_dwell_fsm_and_persists_marker`. Copy the list of failing test ids into the commit message body, so later tasks can tell pre-existing failures from new ones.

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore(backend): add onnxruntime/opencv/numpy and aiosqlite for face subject IDs

Baseline failures before this feature (pre-existing):
<paste failing test ids from Step 3>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pipeline face-crop buffer (pure module)

**Files:**
- Create: `pipeline/app/face_capture.py`
- Test: `pipeline/tests/test_face_capture.py`

**Interfaces:**
- Consumes: nothing.
- Produces (used by Task 3):
  - `head_crop_box(left: float, top: float, width: float, height: float, frame_w: int, frame_h: int) -> tuple[int, int, int, int] | None`
  - `class FaceCropBuffer(ring_size=6, max_incident_crops=12, postroll_s=2.0, stale_after_s=5.0)`, with methods:
    - `.add(camera_id: str, track_id: int, ts: float, jpeg: bytes) -> None`
    - `.begin_incident(incident_id: str, camera_id: str, track_id: int, now: float, detected_at: float) -> None`
    - `.pop_due(now: float) -> list[Incident]`
    - `.evict_stale(now: float) -> None`
    - `.tracked_count() -> int`
  - `@dataclass Incident(incident_id, camera_id, track_id, detected_at, close_at, crops: list[bytes], alias_ids: list[tuple[str, float]])`
  - `ensure_faces_dirs(base_dir: Path) -> None`
  - `write_incident_crops(base_dir: Path, incident_id: str, crops: list[bytes]) -> list[str]`
  - `flush_due(buffer, now: float, base_dir: Path, publish: Callable[[str, dict], None]) -> int`
  - `run_flusher(buffer, base_dir: Path, publish, stop: threading.Event, interval_s: float = 0.5, clock=time.monotonic, on_error=None) -> None`
  - `TOPIC_FACES_REQUESTS = "wastewatch.faces.requests"`
  - Payload published per incident id: `{"incident_id": str, "camera_id": str, "person_track_id": int, "detected_at": float, "crops": list[str]}`

- [ ] **Step 1: Write the failing tests**

Create `pipeline/tests/test_face_capture.py`:

```python
"""Tests for the violation-time face-crop buffer (pipeline/app/face_capture.py)."""
import threading
import time
from pathlib import Path

from app.face_capture import (
    FaceCropBuffer,
    ensure_faces_dirs,
    flush_due,
    head_crop_box,
    run_flusher,
)


def _jpeg(n: int) -> bytes:
    return f"jpeg-{n}".encode()


# ── head_crop_box ───────────────────────────────────────────────────

def test_head_crop_box_pads_sides_and_takes_top_half():
    assert head_crop_box(100, 50, 200, 400, 1920, 1080) == (80, 50, 320, 250)


def test_head_crop_box_clamps_to_frame():
    assert head_crop_box(0, 0, 200, 400, 150, 1080) == (0, 0, 150, 200)


def test_head_crop_box_rejects_too_small():
    assert head_crop_box(10, 10, 30, 60, 1920, 1080) is None


# ── FaceCropBuffer ─────────────────────────────────────────────────

def test_ring_keeps_only_last_six_crops():
    b = FaceCropBuffer()
    for i in range(10):
        b.add("cam-01", 7, float(i), _jpeg(i))
    b.begin_incident("inc-1", "cam-01", 7, now=10.0, detected_at=1000.0)
    [inc] = b.pop_due(12.0)
    assert inc.crops == [_jpeg(i) for i in range(4, 10)]


def test_incident_collects_postroll_crops_until_closed():
    b = FaceCropBuffer(postroll_s=2.0)
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=1.0, detected_at=1000.0)
    b.add("cam-01", 7, 1.5, _jpeg(1))
    assert b.pop_due(2.9) == []
    b.add("cam-01", 7, 2.5, _jpeg(2))
    [inc] = b.pop_due(3.0)
    assert inc.crops == [_jpeg(0), _jpeg(1), _jpeg(2)]
    assert inc.detected_at == 1000.0
    assert inc.camera_id == "cam-01" and inc.track_id == 7


def test_incident_crop_cap():
    b = FaceCropBuffer(max_incident_crops=12)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    for i in range(20):
        b.add("cam-01", 7, 0.05 * i, _jpeg(i))
    [inc] = b.pop_due(5.0)
    assert len(inc.crops) == 12


def test_incident_for_unseen_track_has_no_crops():
    b = FaceCropBuffer()
    b.begin_incident("inc-1", "cam-01", 99, now=0.0, detected_at=1.0)
    [inc] = b.pop_due(5.0)
    assert inc.crops == []


def test_second_incident_on_same_track_becomes_alias():
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    b.begin_incident("inc-2", "cam-01", 7, now=0.5, detected_at=1.5)
    [inc] = b.pop_due(5.0)
    assert inc.incident_id == "inc-1"
    assert inc.alias_ids == [("inc-2", 1.5)]


def test_tracks_are_isolated_by_camera():
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(1))
    b.add("cam-02", 7, 0.0, _jpeg(2))
    b.begin_incident("inc-1", "cam-02", 7, now=0.0, detected_at=1.0)
    [inc] = b.pop_due(5.0)
    assert inc.crops == [_jpeg(2)]


def test_evict_stale_drops_idle_tracks_but_keeps_open_incidents():
    b = FaceCropBuffer(stale_after_s=5.0, postroll_s=10.0)
    b.add("cam-01", 1, 0.0, _jpeg(1))
    b.add("cam-01", 2, 0.0, _jpeg(2))
    b.begin_incident("inc-1", "cam-01", 2, now=0.0, detected_at=1.0)
    b.evict_stale(6.0)
    assert b.tracked_count() == 1


# ── flushing ───────────────────────────────────────────────────────

def test_flush_due_writes_each_incident_to_its_own_folder(tmp_path: Path):
    b = FaceCropBuffer()
    b.add("cam-01", 7, 0.0, _jpeg(0))
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=100.0)
    b.begin_incident("inc-2", "cam-01", 7, now=0.1, detected_at=100.1)
    sent: list[tuple[str, dict]] = []
    n = flush_due(b, 5.0, tmp_path, lambda key, payload: sent.append((key, payload)))
    assert n == 2
    assert [p["incident_id"] for _, p in sent] == ["inc-1", "inc-2"]
    assert [p["detected_at"] for _, p in sent] == [100.0, 100.1]
    for key, p in sent:
        assert key == "cam-01"
        assert p["camera_id"] == "cam-01" and p["person_track_id"] == 7
        assert len(p["crops"]) == 1
        crop = Path(p["crops"][0])
        assert crop.read_bytes() == _jpeg(0)
        assert crop.parent == tmp_path / "incoming" / p["incident_id"]


def test_flush_due_publishes_empty_crops_without_writing(tmp_path: Path):
    b = FaceCropBuffer()
    b.begin_incident("inc-1", "cam-01", 99, now=0.0, detected_at=1.0)
    sent: list[dict] = []
    flush_due(b, 5.0, tmp_path, lambda key, payload: sent.append(payload))
    assert sent[0]["crops"] == []
    assert not (tmp_path / "incoming" / "inc-1").exists()


def test_nothing_is_written_without_an_incident(tmp_path: Path):
    b = FaceCropBuffer()
    for i in range(20):
        b.add("cam-01", 1, float(i), _jpeg(i))
    assert flush_due(b, 100.0, tmp_path, lambda k, p: None) == 0
    assert not (tmp_path / "incoming").exists()


def test_ensure_faces_dirs_creates_incoming_and_subjects(tmp_path: Path):
    ensure_faces_dirs(tmp_path)
    assert (tmp_path / "incoming").is_dir()
    assert (tmp_path / "subjects").is_dir()


def test_run_flusher_publishes_and_stops(tmp_path: Path):
    b = FaceCropBuffer(postroll_s=0.0)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    stop = threading.Event()
    sent: list[dict] = []
    t = threading.Thread(
        target=run_flusher,
        args=(b, tmp_path, lambda k, p: sent.append(p), stop),
        kwargs={"interval_s": 0.01, "clock": lambda: 10.0},
    )
    t.start()
    deadline = time.monotonic() + 2.0
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(2.0)
    assert sent and sent[0]["incident_id"] == "inc-1"
    assert not t.is_alive()


def test_run_flusher_survives_publish_errors(tmp_path: Path):
    b = FaceCropBuffer(postroll_s=0.0)
    b.begin_incident("inc-1", "cam-01", 7, now=0.0, detected_at=1.0)
    stop = threading.Event()
    errors: list[int] = []

    def boom(key, payload):
        raise RuntimeError("kafka down")

    t = threading.Thread(
        target=run_flusher,
        args=(b, tmp_path, boom, stop),
        kwargs={"interval_s": 0.01, "clock": lambda: 10.0, "on_error": lambda: errors.append(1)},
    )
    t.start()
    deadline = time.monotonic() + 2.0
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    t.join(2.0)
    assert errors and not t.is_alive()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd pipeline && ../backend/.venv/Scripts/python -m pytest tests/test_face_capture.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'app.face_capture'`.

- [ ] **Step 3: Implement `pipeline/app/face_capture.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd pipeline && ../backend/.venv/Scripts/python -m pytest tests/test_face_capture.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/app/face_capture.py pipeline/tests/test_face_capture.py
git commit -m "feat(pipeline): per-track head-crop buffer for violation-time face capture

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Wire face capture into the DeepStream pipeline

**Files:**
- Modify: `pipeline/app/pipeline.py`:
  - imports near L18-50;
  - module constants after the `_alert_last_fired_at` block (~L223);
  - `_fire_waste_alert_async` (~L317-395);
  - new probe functions placed right after `capture_frame_probe`;
  - `build_pipeline` OSD/link section (~L2506-2640);
  - `main()` after the OSD decoration probe is attached (~L2925).

**Interfaces:**
- Consumes (Task 2): `FaceCropBuffer`, `head_crop_box`, `ensure_faces_dirs`, `run_flusher`, `TOPIC_FACES_REQUESTS`.
- Produces:
  - Alerts on `wastewatch.alerts` now carry `incident_id: str` (uuid4) and `person_track_id: int`.
  - With `WW_FACE_CAPTURE_ENABLED=1`, `wastewatch.faces.requests` messages are published (payload as in Task 2) and crop files are written under `WW_FACES_PRIVATE_DIR/incoming/<incident_id>/`.

This task can't be unit-tested: `pipeline.py` needs `pyds` and a GPU. All the logic lives in Task 2's tested module. Verification here is a syntax check now, plus the live checklist in Task 16.

- [ ] **Step 1: Add imports**

In `pipeline/app/pipeline.py`, add `import uuid` to the stdlib imports (after `import time`) and `from collections import OrderedDict` after `from pathlib import Path`. Add to the `from app...` imports:

```python
from app.face_capture import (
    TOPIC_FACES_REQUESTS,
    FaceCropBuffer,
    ensure_faces_dirs,
    head_crop_box,
    run_flusher,
)
```

- [ ] **Step 2: Add module constants**

Insert these directly after the line `_alert_last_fired_at: dict[str, float] = {}`:

```python
# Violation-time face capture (docs/superpowers/specs/2026-09-29-face-subject-id-design.md).
# Off by default: with WW_FACE_CAPTURE_ENABLED unset the raw branch is never
# built and _face_buffer stays None, so behaviour is exactly as before.
_FACE_CAPTURE_ENABLED = os.environ.get("WW_FACE_CAPTURE_ENABLED", "0") == "1"
_FACE_CAPTURE_INTERVAL_S = float(os.environ.get("WW_FACE_CAPTURE_INTERVAL_S", "0.5"))
_FACE_POSTROLL_S = float(os.environ.get("WW_FACE_POSTROLL_S", "2.0"))
_FACES_PRIVATE_DIR = Path(os.environ.get("WW_FACES_PRIVATE_DIR", "/app/faces_private"))
_UNTRACKED_OBJECT_ID = 0xFFFFFFFFFFFFFFFF
_face_buffer: FaceCropBuffer | None = (
    FaceCropBuffer(postroll_s=_FACE_POSTROLL_S) if _FACE_CAPTURE_ENABLED else None
)
```

- [ ] **Step 3: Hook `_fire_waste_alert_async`**

Directly after `_alert_last_fired_at[camera_id] = now_monotonic` (still inside the function, after the `with _alert_dedup_lock:` block), insert:

```python
    # One incident per alert that survives dedup. The id rides on the alert
    # and on the face request so the API can join them (spec D6).
    incident_id = str(uuid.uuid4())
    person_track_id = dumped_by_track_id if dumped_by_track_id is not None else track_id
    if _face_buffer is not None:
        _face_buffer.begin_incident(
            incident_id, camera_id, person_track_id,
            now=now_monotonic, detected_at=detected_at,
        )
```

In `_publish`, add two keyword arguments to the `kafka_producer.send_alert(...)` call, after `detected_at=detected_at,`:

```python
            incident_id=incident_id,
            person_track_id=person_track_id,
```

- [ ] **Step 4: Add the probe functions**

Insert these after the end of `capture_frame_probe`:

```python
def _iter_frame_metas(batch_meta):
    frame_list = batch_meta.frame_meta_list
    while frame_list is not None:
        try:
            yield pyds.NvDsFrameMeta.cast(frame_list.data)
        except StopIteration:
            return
        try:
            frame_list = frame_list.next
        except StopIteration:
            return


def _iter_person_objs(frame_meta):
    obj_list = frame_meta.obj_meta_list
    while obj_list is not None:
        try:
            obj = pyds.NvDsObjectMeta.cast(obj_list.data)
        except StopIteration:
            return
        if COCO_CLASS_MAP.get(int(obj.class_id)) == "person":
            yield obj
        try:
            obj_list = obj_list.next
        except StopIteration:
            return


def _snapshot_person_boxes(batch_meta, source_manager) -> list:
    """[(batch_id, camera_id, [(track_id, left, top, width, height), ...]), ...]

    Taken on the streaming thread BEFORE nvosd's decorate_for_osd probe runs:
    nvvideoconvert passes the same NvDsBatchMeta downstream, and
    decorate_for_osd mutates object lists, so the raw branch's own thread
    must never walk obj_meta_list itself.
    """
    pad_map = source_manager.pad_to_camera if source_manager is not None else {}
    frames = []
    for frame_meta in _iter_frame_metas(batch_meta):
        camera_id = pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")
        persons = []
        for obj in _iter_person_objs(frame_meta):
            track_id = int(obj.object_id)
            if track_id == _UNTRACKED_OBJECT_ID:
                continue
            r = obj.rect_params
            persons.append((track_id, float(r.left), float(r.top), float(r.width), float(r.height)))
        frames.append((int(frame_meta.batch_id), camera_id, persons))
    return frames


def raw_face_throttle_probe(pad: Gst.Pad, info: Gst.PadProbeInfo, state: dict) -> Gst.PadProbeReturn:
    """On tee_raw's first src pad: DROP unless a capture tick is due and a person is present.

    Runs before the synchronous RGBA conversion, so the conversion itself is
    limited to <= 1/_FACE_CAPTURE_INTERVAL_S fps and only when people exist.
    """
    now = time.monotonic()
    if now - state["last"] < _FACE_CAPTURE_INTERVAL_S:
        return Gst.PadProbeReturn.DROP
    buf = info.get_buffer()
    if buf is None:
        return Gst.PadProbeReturn.DROP
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    if batch_meta is None:
        return Gst.PadProbeReturn.DROP
    frames = _snapshot_person_boxes(batch_meta, state["source_manager"])
    if not any(persons for _, _, persons in frames):
        return Gst.PadProbeReturn.DROP
    state["last"] = now
    with state["lock"]:
        snapshots = state["snapshots"]
        snapshots[buf.pts] = frames
        while len(snapshots) > 4:
            snapshots.popitem(last=False)
    return Gst.PadProbeReturn.OK


def raw_face_probe(pad: Gst.Pad, info: Gst.PadProbeInfo, state: dict) -> Gst.PadProbeReturn:
    """On fakesink_raw's sink pad: crop each snapshotted person's head from the clean frame."""
    buffer = _face_buffer
    buf = info.get_buffer()
    if buffer is None or buf is None:
        return Gst.PadProbeReturn.OK
    with state["lock"]:
        frames = state["snapshots"].pop(buf.pts, None)
    if not frames:
        return Gst.PadProbeReturn.OK
    success, map_info = buf.map(Gst.MapFlags.READ)
    if not success:
        return Gst.PadProbeReturn.OK
    try:
        import io
        import numpy as np
        from PIL import Image

        w = pipeline_settings.muxer_output_width
        h = pipeline_settings.muxer_output_height
        frame_size = w * h * 4
        raw = map_info.data
        now = time.monotonic()
        for batch_id, camera_id, persons in frames:
            offset = batch_id * frame_size
            if offset + frame_size > len(raw):
                continue
            frame = np.frombuffer(raw, dtype=np.uint8, count=frame_size, offset=offset).reshape((h, w, 4))
            for track_id, left, top, width, height in persons:
                box = head_crop_box(left, top, width, height, w, h)
                if box is None:
                    continue
                x1, y1, x2, y2 = box
                out = io.BytesIO()
                Image.fromarray(frame[y1:y2, x1:x2, :3]).save(out, format="JPEG", quality=90)
                buffer.add(camera_id, track_id, now, out.getvalue())
    except Exception:
        logger.exception("raw_face_probe failed")
    finally:
        buf.unmap(map_info)
    return Gst.PadProbeReturn.OK


def _attach_face_capture(pipeline, source_manager, kafka_producer) -> None:
    """Attach the raw-branch probes and start the flusher thread (WW_FACE_CAPTURE_ENABLED=1 only)."""
    tee_raw = pipeline.get_by_name("tee_raw")
    fakesink_raw = pipeline.get_by_name("fakesink_raw")
    if tee_raw is None or fakesink_raw is None or _face_buffer is None:
        logger.error("Face capture enabled but raw branch missing -- face capture disabled")
        return
    ensure_faces_dirs(_FACES_PRIVATE_DIR)
    state = {
        "last": 0.0,
        "snapshots": OrderedDict(),
        "lock": threading.Lock(),
        "source_manager": source_manager,
    }
    tee_raw.get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER, raw_face_throttle_probe, state)
    fakesink_raw.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, raw_face_probe, state)

    def _publish(camera_id: str, payload: dict) -> None:
        kafka_producer.produce(TOPIC_FACES_REQUESTS, key=camera_id, value=payload)

    threading.Thread(
        target=run_flusher,
        args=(_face_buffer, _FACES_PRIVATE_DIR, _publish, threading.Event()),
        kwargs={"on_error": lambda: logger.exception("Face flusher tick failed")},
        daemon=True,
        name="face-flusher",
    ).start()
    logger.info("Face capture enabled: raw pre-OSD branch + flusher (dir=%s)", _FACES_PRIVATE_DIR)
```

- [ ] **Step 5: Build the raw branch in `build_pipeline`**

Directly after `pipeline.add(nvosd)` (and before the `# --- Tee — splits stream ...` comment), insert:

```python
    # --- Raw (pre-OSD) face-capture branch ---------------------------------
    # nvdsosd rasterises boxes/privacy rectangles IN PLACE on the shared
    # surface, and tee does not copy buffers -- so the clean copy must be
    # made before nvosd sees the buffer. tee pushes to its src pads in
    # request order on the streaming thread; the raw branch is requested
    # FIRST and has no queue before nvvideoconvert, so the RGBA conversion
    # (which writes a new buffer) completes before nvosd draws. The queue
    # after the conversion moves the Python crop work to its own thread.
    tee_raw = None
    if _FACE_CAPTURE_ENABLED:
        tee_raw = make_element("tee", "tee_raw")
        nvconv_raw = make_element("nvvideoconvert", "nvconv_raw")
        capsfilter_raw = make_element("capsfilter", "capsfilter_raw")
        capsfilter_raw.set_property("caps", Gst.Caps.from_string("video/x-raw, format=RGBA"))
        queue_raw = make_element("queue", "queue_raw")
        queue_raw.set_property("leaky", 2)  # 2 = downstream (drop old)
        queue_raw.set_property("max-size-buffers", 1)
        queue_raw.set_property("max-size-bytes", 0)
        queue_raw.set_property("max-size-time", 0)
        fakesink_raw = make_element("fakesink", "fakesink_raw")
        fakesink_raw.set_property("sync", False)
        fakesink_raw.set_property("async", False)
        for elem in (tee_raw, nvconv_raw, capsfilter_raw, queue_raw, fakesink_raw):
            pipeline.add(elem)
    osd_input = tee_raw if tee_raw is not None else nvosd
```

In the "Link main chain" section, change the two links into `nvosd` so they target `osd_input`:

```python
        if not ww_meta_elem.link(osd_input):
            logger.critical("Failed to link wastewatchmeta -> %s", osd_input.get_name())
            sys.exit(1)
```

```python
        if not nvdsanalytics.link(osd_input):
            logger.critical("Failed to link nvdsanalytics -> %s", osd_input.get_name())
            sys.exit(1)
```

Then, directly before `if not nvosd.link(tee):`, insert:

```python
    if tee_raw is not None:
        # Request the raw pad FIRST -- see the raw-branch comment above.
        tee_raw_src_face = tee_raw.get_request_pad("src_%u")
        if tee_raw_src_face.link(nvconv_raw.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            logger.critical("Failed to link tee_raw -> nvconv_raw")
            sys.exit(1)
        tee_raw_src_osd = tee_raw.get_request_pad("src_%u")
        if tee_raw_src_osd.link(nvosd.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            logger.critical("Failed to link tee_raw -> nvosd")
            sys.exit(1)
        if not (
            nvconv_raw.link(capsfilter_raw)
            and capsfilter_raw.link(queue_raw)
            and queue_raw.link(fakesink_raw)
        ):
            logger.critical("Failed to link raw face-capture branch")
            sys.exit(1)
```

- [ ] **Step 6: Attach it in `main()`**

In `main()`, directly after the block that attaches `decorate_for_osd` (after `logger.debug("OSD decoration probe attached to nvdsosd.sink")` and its enclosing `if`s), insert at the function's top indentation level:

```python
    # --- Violation-time face capture (raw pre-OSD branch) --------------------
    # Only the Python metadata path calls _fire_waste_alert_async; with the
    # native wastewatchmeta path no alerts fire here, so no faces are taken.
    if _face_buffer is not None:
        _attach_face_capture(pipeline, source_manager, kafka_producer)
```

- [ ] **Step 7: Syntax-check and run the pipeline test suite**

```bash
backend/.venv/Scripts/python -m py_compile pipeline/app/pipeline.py && echo OK
cd pipeline && ../backend/.venv/Scripts/python -m pytest tests -q
```

Expected: `OK`, and all pipeline tests pass (the new face-capture tests included).

- [ ] **Step 8: Commit**

```bash
git add pipeline/app/pipeline.py
git commit -m "feat(pipeline): raw pre-OSD face-capture branch and incident_id on alerts

Behind WW_FACE_CAPTURE_ENABLED (default off). The raw branch is tee_raw's
first pad with a synchronous RGBA conversion so the copy is taken before
nvdsosd draws in place; person boxes are snapshotted on the streaming
thread so the crop thread never walks metadata decorate_for_osd mutates.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Backend data model, migration 008, settings

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/008_subjects.py`
- Modify: `backend/app/config/kafka.py`
- Create: `backend/app/subjects/__init__.py` (empty)
- Create: `backend/app/subjects/config.py`
- Create: `backend/tests/subjects/__init__.py` (empty)
- Create: `backend/tests/subjects/conftest.py`
- Test: `backend/tests/subjects/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `app.db.models.subject_code(code_num: int) -> str`.
  - ORM classes `Subject`, `SubjectFace`, `SubjectIncident`, `FaceIncident`, `SubjectMatchReview`, `SubjectAccessLog`. Field names are exactly as in spec §7, plus `Subject.code_num`, the `Subject.code` property and `SubjectFace.embedding: bytes`.
  - `Alert.incident_id: uuid.UUID | None`.
  - `app.subjects.config.SubjectSettings`, a frozen dataclass with fields `min_det, min_px, min_sharpness, max_yaw, match_auto, match_review, max_faces, keep_per_incident, retention_days, incoming_retention_days, det_model, rec_model, faces_dir, qdrant_url, collection`, and `SubjectSettings.from_env(env: Mapping[str, str] | None = None)`.
  - `app.config.kafka.TOPIC_FACES_REQUESTS` and `GROUP_SUBJECT_WORKER`.
  - Test fixture `session_factory`: an `async_sessionmaker` on a temp-file SQLite DB with the alert and subject tables.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/subjects/__init__.py` (empty) and `backend/tests/subjects/conftest.py`:

```python
"""Shared fixtures for subject-ID tests: SQLite (file) DB with the tables we touch."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    Alert,
    Base,
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)

TABLES = [
    Alert.__table__,
    Subject.__table__,
    SubjectFace.__table__,
    SubjectIncident.__table__,
    FaceIncident.__table__,
    SubjectMatchReview.__table__,
    SubjectAccessLog.__table__,
]


@pytest.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'subjects.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=TABLES))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
```

Create `backend/tests/subjects/test_models.py`:

```python
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    Subject,
    SubjectFace,
    subject_code,
)
from app.subjects.config import SubjectSettings

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)


def test_subject_code_pads_to_three_digits():
    assert subject_code(1) == "S-001"
    assert subject_code(42) == "S-042"
    assert subject_code(1234) == "S-1234"


async def test_subject_and_face_roundtrip(session_factory):
    async with session_factory() as s:
        subject = Subject(code_num=1, first_seen_at=NOW, last_seen_at=NOW)
        s.add(subject)
        await s.flush()
        s.add(SubjectFace(
            subject_id=subject.id, incident_id=uuid.uuid4(), crop_path="/x.jpg",
            quality=0.5, embedding=b"\x00" * 2048,
        ))
        await s.commit()
    async with session_factory() as s:
        loaded = (await s.execute(select(Subject))).scalar_one()
        assert loaded.code == "S-001"
        assert loaded.status == "active"
        assert loaded.incident_count == 0
        face = (await s.execute(select(SubjectFace))).scalar_one()
        assert face.qdrant_synced is False
        assert len(face.embedding) == 2048


async def test_alert_has_incident_id(session_factory):
    iid = uuid.uuid4()
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="t", status=AlertStatus.active, incident_id=iid))
        await s.commit()
    async with session_factory() as s:
        alert = (await s.execute(select(Alert))).scalar_one()
        assert alert.incident_id == iid


def test_settings_defaults_match_spec():
    s = SubjectSettings()
    assert (s.match_auto, s.match_review, s.max_faces, s.retention_days) == (0.55, 0.40, 10, 90)
    assert s.det_model == "/app/models/face/det_10g.onnx"
    assert s.collection == "offender_faces"


def test_settings_from_env_casts_types():
    s = SubjectSettings.from_env({
        "WW_FACE_MATCH_AUTO": "0.6",
        "WW_SUBJECT_MAX_FACES": "5",
        "WW_FACES_PRIVATE_DIR": "/tmp/f",
    })
    assert s.match_auto == 0.6
    assert s.max_faces == 5
    assert s.faces_dir == "/tmp/f"
    assert s.match_review == 0.40


def test_settings_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        SubjectSettings(match_auto=0.3, match_review=0.4)


def test_migration_008_follows_007():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "008_subjects.py"
    spec = importlib.util.spec_from_file_location("mig008", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "008"
    assert mod.down_revision == "007"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_models.py -v`
Expected: ImportError, because `Subject` is not defined in `app.db.models`.

- [ ] **Step 3: Add the models**

In `backend/app/db/models.py`, extend the SQLAlchemy import line to:

```python
from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String, Text, Uuid
```

In `class Alert`, add after the `snapshot_url` column:

```python
    # Pipeline-minted id shared by the alert and its face request
    # (wastewatch.faces.requests); GET /api/alerts joins subject data on it.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
```

Append at the end of the file:

```python
# ── Face-based subject IDs (docs/superpowers/specs/2026-09-29-face-subject-id-design.md) ──
# Generic Uuid/JSON types (not postgresql.*) so the service layer is testable on SQLite.


def subject_code(code_num: int) -> str:
    return f"S-{code_num:03d}"


class Subject(Base):
    """Anonymous person involved in >= 1 violation. Never has a name."""
    __tablename__ = "subjects"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code_num: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # active | merged
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    incident_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cover_face_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def code(self) -> str:
        return subject_code(self.code_num)


class SubjectFace(Base):
    __tablename__ = "subject_faces"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)  # = Qdrant point id
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    crop_path: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[float] = mapped_column(Float, nullable=False)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 512 x float32
    qdrant_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SubjectIncident(Base):
    __tablename__ = "subject_incidents"
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_id: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    match_type: Mapped[str] = mapped_column(String(16), nullable=False)  # new | auto | review_merged | detached
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FaceIncident(Base):
    """One row per processed face request (matched, no_face or deleted)."""
    __tablename__ = "face_incidents"
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # matched | no_face | deleted
    camera_id: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SubjectMatchReview(Base):
    __tablename__ = "subject_match_reviews"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    incident_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    new_subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending | merged | rejected | obsolete
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SubjectAccessLog(Base):
    __tablename__ = "subject_access_log"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # no FK: outlives deletion
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

- [ ] **Step 4: Add the migration**

Create `backend/alembic/versions/008_subjects.py`:

```python
"""Face-based subject IDs: subjects, faces, incidents, reviews, access log; alerts.incident_id

Revision ID: 008
Revises: 007
Create Date: 2026-09-29 00:00:00.000000

See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §7.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def _ts(name: str, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS subject_code_seq START 1")
    op.create_table(
        "subjects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("code_num", sa.Integer(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("merged_into_id", UUID, sa.ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("incident_count", sa.Integer(), nullable=False, server_default="0"),
        _ts("first_seen_at"),
        _ts("last_seen_at"),
        sa.Column("cover_face_id", UUID, nullable=True),
        _ts("created_at", default=True),
    )
    op.create_table(
        "subject_faces",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("incident_id", UUID, nullable=False),
        sa.Column("crop_path", sa.Text(), nullable=False),
        sa.Column("quality", sa.Float(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("qdrant_synced", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_faces_subject_id", "subject_faces", ["subject_id"])
    op.create_index("ix_subject_faces_incident_id", "subject_faces", ["incident_id"])
    op.create_table(
        "subject_incidents",
        sa.Column("incident_id", UUID, primary_key=True),
        sa.Column("subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        _ts("detected_at"),
        sa.Column("match_type", sa.String(16), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=True),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_incidents_subject_id", "subject_incidents", ["subject_id"])
    op.create_table(
        "face_incidents",
        sa.Column("incident_id", UUID, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("camera_id", sa.String(255), nullable=False),
        _ts("detected_at"),
        _ts("created_at", default=True),
    )
    op.create_table(
        "subject_match_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("incident_id", UUID, nullable=False),
        sa.Column("new_subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_subject_id", UUID, sa.ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(255), nullable=True),
        _ts("decided_at", nullable=True),
        _ts("created_at", default=True),
    )
    op.create_index("ix_subject_match_reviews_new_subject_id", "subject_match_reviews", ["new_subject_id"])
    op.create_index("ix_subject_match_reviews_candidate_subject_id", "subject_match_reviews", ["candidate_subject_id"])
    op.create_table(
        "subject_access_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("subject_id", UUID, nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        _ts("at", default=True),
    )
    op.add_column("alerts", sa.Column("incident_id", UUID, nullable=True))
    op.create_index("ix_alerts_incident_id", "alerts", ["incident_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_incident_id", table_name="alerts")
    op.drop_column("alerts", "incident_id")
    op.drop_table("subject_access_log")
    op.drop_table("subject_match_reviews")
    op.drop_table("face_incidents")
    op.drop_table("subject_incidents")
    op.drop_table("subject_faces")
    op.drop_table("subjects")
    op.execute("DROP SEQUENCE IF EXISTS subject_code_seq")
```

- [ ] **Step 5: Add the Kafka constants and settings**

In `backend/app/config/kafka.py`, add after `TOPIC_MISSES = "wastewatch.misses"`:

```python
TOPIC_FACES_REQUESTS = "wastewatch.faces.requests"
```

and after `GROUP_HEALER = ...`:

```python
GROUP_SUBJECT_WORKER = "wastewatch.group.subject-worker"
```

Create `backend/app/subjects/__init__.py` (empty) and `backend/app/subjects/config.py`:

```python
"""Settings for face-based subject IDs (spec §12). Read from WW_* env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Mapping

ENV_VARS: dict[str, str] = {
    "min_det": "WW_FACE_MIN_DET",
    "min_px": "WW_FACE_MIN_PX",
    "min_sharpness": "WW_FACE_MIN_SHARPNESS",
    "max_yaw": "WW_FACE_MAX_YAW",
    "match_auto": "WW_FACE_MATCH_AUTO",
    "match_review": "WW_FACE_MATCH_REVIEW",
    "max_faces": "WW_SUBJECT_MAX_FACES",
    "keep_per_incident": "WW_FACE_KEEP_PER_INCIDENT",
    "retention_days": "WW_SUBJECT_RETENTION_DAYS",
    "incoming_retention_days": "WW_FACE_INCOMING_RETENTION_DAYS",
    "det_model": "WW_FACE_DET_MODEL",
    "rec_model": "WW_FACE_REC_MODEL",
    "faces_dir": "WW_FACES_PRIVATE_DIR",
    "qdrant_url": "WW_QDRANT_URL",
    "collection": "WW_FACE_COLLECTION",
}


@dataclass(frozen=True)
class SubjectSettings:
    min_det: float = 0.6
    min_px: float = 40.0
    min_sharpness: float = 60.0
    max_yaw: float = 0.45
    match_auto: float = 0.55
    match_review: float = 0.40
    max_faces: int = 10
    keep_per_incident: int = 3
    retention_days: int = 90
    incoming_retention_days: int = 7
    det_model: str = "/app/models/face/det_10g.onnx"
    rec_model: str = "/app/models/face/w600k_r50.onnx"
    faces_dir: str = "/app/faces_private"
    qdrant_url: str = "http://qdrant:6333"
    collection: str = "offender_faces"

    def __post_init__(self) -> None:
        if not (0.0 < self.match_review <= self.match_auto <= 1.0):
            raise ValueError(
                f"Need 0 < WW_FACE_MATCH_REVIEW ({self.match_review}) <= "
                f"WW_FACE_MATCH_AUTO ({self.match_auto}) <= 1"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SubjectSettings":
        env = os.environ if env is None else env
        kwargs = {}
        for f in fields(cls):
            raw = env.get(ENV_VARS[f.name])
            if raw is not None:
                kwargs[f.name] = type(f.default)(raw)
        return cls(**kwargs)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_models.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db/models.py backend/alembic/versions/008_subjects.py backend/app/config/kafka.py backend/app/subjects backend/tests/subjects
git commit -m "feat(backend): subject tables, migration 008, alerts.incident_id, subject settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 5: Quality gate and match decision (pure)

**Files:**
- Create: `backend/app/subjects/quality.py`
- Create: `backend/app/subjects/matcher.py`
- Test: `backend/tests/subjects/test_quality.py`
- Test: `backend/tests/subjects/test_matcher.py`

**Interfaces:**
- Consumes: `SubjectSettings` (Task 4).
- Produces:
  - `quality.yaw_proxy(landmarks: np.ndarray) -> float`
  - `quality.laplacian_sharpness(gray: np.ndarray) -> float`
  - `quality.passes_gate(det_score: float, face_width: float, sharpness: float, yaw: float, settings: SubjectSettings) -> bool`
  - `quality.quality_score(det_score: float, face_width: float, sharpness: float, yaw: float) -> float`
  - `@dataclass(frozen=True) matcher.Hit(subject_id: uuid.UUID, face_id: uuid.UUID, score: float)`
  - `@dataclass(frozen=True) matcher.Decision(kind: Literal["auto","review","new"], subject_id: uuid.UUID | None, score: float | None)`
  - `matcher.best_per_subject(hits: Iterable[Hit]) -> dict[uuid.UUID, float]`
  - `matcher.decide(per_subject: dict[uuid.UUID, float], auto: float, review: float) -> Decision`
  - `matcher.faces_over_cap(faces: list[tuple[uuid.UUID, float]], cap: int) -> list[uuid.UUID]` (the ids to evict: everything past the top `cap` by quality)

`matcher.py` must not import numpy or cv2, because the API process imports it.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_quality.py`:

```python
import numpy as np
import pytest

from app.subjects.config import SubjectSettings
from app.subjects.quality import laplacian_sharpness, passes_gate, quality_score, yaw_proxy

S = SubjectSettings()


def _lm(nose_x: float) -> np.ndarray:
    # left eye, right eye, nose, left mouth, right mouth
    return np.array([[30, 40], [70, 40], [nose_x, 60], [35, 80], [65, 80]], dtype=np.float32)


def test_yaw_is_zero_for_frontal_face():
    assert yaw_proxy(_lm(50)) == pytest.approx(0.0)


def test_yaw_grows_as_nose_moves_off_centre():
    assert yaw_proxy(_lm(68)) == pytest.approx(0.45)
    assert yaw_proxy(_lm(32)) == pytest.approx(-0.45)


def test_yaw_degenerate_eyes_is_worst_case():
    lm = _lm(50)
    lm[1] = lm[0]
    assert yaw_proxy(lm) == 1.0


@pytest.mark.parametrize("det,width,sharp,yaw,ok", [
    (0.60, 40.0, 60.0, 0.45, True),
    (0.59, 40.0, 60.0, 0.00, False),
    (0.90, 39.9, 60.0, 0.00, False),
    (0.90, 80.0, 59.9, 0.00, False),
    (0.90, 80.0, 90.0, -0.46, False),
])
def test_gate_boundaries(det, width, sharp, yaw, ok):
    assert passes_gate(det, width, sharp, yaw, S) is ok


def test_quality_score_saturates_and_penalises():
    assert quality_score(0.9, 112, 200, 0.0) == pytest.approx(0.9)
    assert quality_score(0.9, 300, 900, 0.0) == pytest.approx(0.9)
    assert quality_score(0.9, 56, 100, 0.5) == pytest.approx(0.9 * 0.5 * 0.5 * 0.5)


def test_sharpness_flat_vs_textured():
    flat = np.full((112, 112), 128, dtype=np.uint8)
    checker = (np.indices((112, 112)).sum(axis=0) % 2 * 255).astype(np.uint8)
    assert laplacian_sharpness(flat) == 0.0
    assert laplacian_sharpness(checker) > 1000.0
```

`backend/tests/subjects/test_matcher.py`:

```python
import uuid

import pytest

from app.subjects.matcher import Hit, best_per_subject, decide, faces_over_cap

A, B = uuid.uuid4(), uuid.uuid4()


def test_best_per_subject_takes_max():
    hits = [Hit(A, uuid.uuid4(), 0.3), Hit(A, uuid.uuid4(), 0.7), Hit(B, uuid.uuid4(), 0.5)]
    assert best_per_subject(hits) == {A: 0.7, B: 0.5}


def test_decide_no_candidates_is_new():
    d = decide({}, 0.55, 0.40)
    assert (d.kind, d.subject_id, d.score) == ("new", None, None)


@pytest.mark.parametrize("score,kind", [
    (0.3999, "new"), (0.40, "review"), (0.5499, "review"), (0.55, "auto"), (0.99, "auto"),
])
def test_decide_threshold_boundaries(score, kind):
    d = decide({A: score}, 0.55, 0.40)
    assert d.kind == kind
    assert d.score == score
    assert d.subject_id == (None if kind == "new" else A)


def test_decide_picks_best_subject():
    d = decide({A: 0.6, B: 0.9}, 0.55, 0.40)
    assert (d.kind, d.subject_id) == ("auto", B)


def test_faces_over_cap_keeps_highest_quality():
    f1, f2, f3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert faces_over_cap([(f1, 0.1), (f2, 0.9), (f3, 0.5)], 2) == [f1]
    assert faces_over_cap([(f1, 0.1)], 2) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_quality.py tests/subjects/test_matcher.py -v`
Expected: `ModuleNotFoundError: No module named 'app.subjects.quality'` (and `matcher`).

- [ ] **Step 3: Implement**

`backend/app/subjects/quality.py`:

```python
"""Face quality gate and score (spec §6.2 step 3). Pure apart from cv2.Laplacian."""
from __future__ import annotations

import numpy as np

from app.subjects.config import SubjectSettings


def yaw_proxy(landmarks: np.ndarray) -> float:
    """Horizontal nose offset from the eye midpoint, in inter-eye distances.

    0 = frontal, |0.45| ~ 45 degrees. Landmark order (SCRFD): left eye,
    right eye, nose, left mouth corner, right mouth corner.
    """
    left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]
    inter_eye = abs(float(right_eye[0]) - float(left_eye[0]))
    if inter_eye < 1e-6:
        return 1.0
    eye_mid_x = (float(left_eye[0]) + float(right_eye[0])) / 2.0
    return (float(nose[0]) - eye_mid_x) / inter_eye


def laplacian_sharpness(gray: np.ndarray) -> float:
    import cv2

    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def passes_gate(
    det_score: float, face_width: float, sharpness: float, yaw: float, settings: SubjectSettings
) -> bool:
    return (
        det_score >= settings.min_det
        and face_width >= settings.min_px
        and sharpness >= settings.min_sharpness
        and abs(yaw) <= settings.max_yaw
    )


def quality_score(det_score: float, face_width: float, sharpness: float, yaw: float) -> float:
    return (
        det_score
        * min(1.0, face_width / 112.0)
        * min(1.0, sharpness / 200.0)
        * max(0.0, 1.0 - abs(yaw))
    )
```

`backend/app/subjects/matcher.py`:

```python
"""Per-subject scoring and the auto / review / new decision (spec §6.2 steps 6-7).

Pure: no numpy/cv2, so the API process can import it cheaply.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Literal


@dataclass(frozen=True)
class Hit:
    subject_id: uuid.UUID
    face_id: uuid.UUID
    score: float


@dataclass(frozen=True)
class Decision:
    kind: Literal["auto", "review", "new"]
    subject_id: uuid.UUID | None
    score: float | None


def best_per_subject(hits: Iterable[Hit]) -> dict[uuid.UUID, float]:
    best: dict[uuid.UUID, float] = {}
    for hit in hits:
        if hit.score > best.get(hit.subject_id, float("-inf")):
            best[hit.subject_id] = hit.score
    return best


def decide(per_subject: dict[uuid.UUID, float], auto: float, review: float) -> Decision:
    if not per_subject:
        return Decision("new", None, None)
    subject_id, score = max(per_subject.items(), key=lambda kv: (kv[1], str(kv[0])))
    if score >= auto:
        return Decision("auto", subject_id, score)
    if score >= review:
        return Decision("review", subject_id, score)
    return Decision("new", None, score)


def faces_over_cap(faces: list[tuple[uuid.UUID, float]], cap: int) -> list[uuid.UUID]:
    ranked = sorted(faces, key=lambda f: (-f[1], str(f[0])))
    return [face_id for face_id, _ in ranked[cap:]]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_quality.py tests/subjects/test_matcher.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/subjects/quality.py backend/app/subjects/matcher.py backend/tests/subjects/test_quality.py backend/tests/subjects/test_matcher.py
git commit -m "feat(subjects): face quality gate and auto/review/new match decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Face detector, embedder, analyzer, request parsing, model download

**Files:**
- Create: `backend/app/subjects/types.py`
- Create: `backend/app/subjects/detector.py`
- Create: `backend/app/subjects/embedder.py`
- Create: `backend/app/subjects/analyzer.py`
- Create: `scripts/download_face_models.py`
- Test: `backend/tests/subjects/test_types.py`
- Test: `backend/tests/subjects/test_detector.py`
- Test: `backend/tests/subjects/test_embedder.py`
- Test: `backend/tests/subjects/test_analyzer.py`
- Test: `backend/tests/subjects/test_face_models_integration.py` (skipped unless the model files exist)

**Interfaces:**
- Consumes: `SubjectSettings` (Task 4); `yaw_proxy`, `laplacian_sharpness`, `passes_gate`, `quality_score` (Task 5).
- Produces:
  - `types.AnalyzedFace(quality: float, embedding: np.ndarray, face_jpeg: bytes)` (frozen dataclass).
  - `types.FaceRequest(incident_id: uuid.UUID, camera_id: str, detected_at: datetime, crops: tuple[str, ...], person_track_id: int | None = None)` (frozen dataclass).
    - `FaceRequest.from_payload(payload: dict, incoming_dir: Path) -> FaceRequest`. It raises `KeyError`/`ValueError` on a missing or bad `incident_id`, and keeps only crop paths inside `incoming_dir/<incident_id>/`.
  - `detector.FaceDetection(score: float, bbox: np.ndarray, kps: np.ndarray)`.
  - `detector.letterbox(img, size) -> (np.ndarray, float)`.
  - `detector.decode_stride(...)`.
  - `detector.ScrfdDetector(model_path, input_size=(320, 320), det_thresh=0.5)` with `.detect_best(img_bgr) -> FaceDetection | None`.
  - `embedder.ARCFACE_TEMPLATE`, `embedder.alignment_matrix(landmarks) -> np.ndarray`, `embedder.align_face(img_bgr, landmarks) -> np.ndarray (112,112,3)`.
  - `embedder.ArcFaceEmbedder(model_path)` with `.embed(aligned_bgr) -> np.ndarray (512,) L2-normalised`.
  - `analyzer.FaceAnalyzer(detector, embedder, settings)`, callable as `(crop_paths: list[str]) -> list[AnalyzedFace]`, best quality first, at most `settings.keep_per_incident`.
  - `analyzer.face_thumbnail_jpeg(img, bbox, margin=0.3) -> bytes`.

`types.py` must not import cv2 or onnxruntime, because the API imports it via the service.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_types.py`:

```python
import uuid
from datetime import timezone
from pathlib import Path

import pytest

from app.subjects.types import FaceRequest


def test_from_payload_keeps_crops_in_own_folder(tmp_path: Path):
    iid = uuid.uuid4()
    own = tmp_path / str(iid) / "00.jpg"
    req = FaceRequest.from_payload(
        {"incident_id": str(iid), "camera_id": "cam-01", "detected_at": 1790000000.5,
         "person_track_id": 7, "crops": [str(own)]},
        tmp_path,
    )
    assert req.incident_id == iid
    assert req.camera_id == "cam-01"
    assert req.person_track_id == 7
    assert req.crops == (str(own.resolve()),)
    assert req.detected_at.tzinfo == timezone.utc
    assert req.detected_at.timestamp() == pytest.approx(1790000000.5)


def test_from_payload_drops_paths_outside_incident_folder(tmp_path: Path):
    iid = uuid.uuid4()
    other = tmp_path / str(uuid.uuid4()) / "00.jpg"
    traversal = tmp_path / str(iid) / ".." / ".." / "etc" / "passwd"
    req = FaceRequest.from_payload(
        {"incident_id": str(iid), "camera_id": "cam-01", "detected_at": 1.0,
         "crops": [str(other), str(traversal), "/etc/passwd"]},
        tmp_path,
    )
    assert req.crops == ()


def test_from_payload_rejects_missing_or_bad_incident_id(tmp_path: Path):
    with pytest.raises(KeyError):
        FaceRequest.from_payload({"camera_id": "cam-01"}, tmp_path)
    with pytest.raises(ValueError):
        FaceRequest.from_payload({"incident_id": "not-a-uuid"}, tmp_path)
```

`backend/tests/subjects/test_detector.py`:

```python
import numpy as np
import pytest

from app.subjects.detector import decode_stride, letterbox


def test_letterbox_scales_and_pads():
    img = np.full((100, 200, 3), 255, dtype=np.uint8)
    out, scale = letterbox(img, (320, 320))
    assert out.shape == (320, 320, 3)
    assert scale == 1.6
    assert out[:160].min() == 255
    assert out[160:].max() == 0


def test_decode_stride_reconstructs_box_and_landmarks():
    # 2x2 grid, stride 8, 2 anchors -> 8 anchors; anchor 3 sits at centre (8, 0).
    scores = np.zeros((8, 1), dtype=np.float32)
    scores[3] = 0.9
    bbox = np.ones((8, 4), dtype=np.float32)          # distance 1 * stride 8 on every side
    kps = np.zeros((8, 10), dtype=np.float32)         # all landmarks at the anchor centre
    s, b, k = decode_stride(scores, bbox, kps, stride=8, height=2, width=2, num_anchors=2, threshold=0.5)
    assert s.shape == (1,) and float(s[0]) == pytest.approx(0.9)
    assert b[0].tolist() == [0.0, -8.0, 16.0, 8.0]
    assert k.shape == (1, 5, 2)
    assert np.allclose(k[0], [[8.0, 0.0]] * 5)


def test_decode_stride_below_threshold_is_empty():
    s, b, k = decode_stride(
        np.full((8, 1), 0.1, np.float32), np.ones((8, 4), np.float32), np.zeros((8, 10), np.float32),
        stride=8, height=2, width=2, num_anchors=2, threshold=0.5,
    )
    assert len(s) == 0 and b.shape == (0, 4) and k.shape == (0, 5, 2)
```

`backend/tests/subjects/test_embedder.py`:

```python
import cv2
import numpy as np

from app.subjects.embedder import ARCFACE_TEMPLATE, align_face, alignment_matrix


def test_alignment_maps_landmarks_onto_template():
    landmarks = ARCFACE_TEMPLATE * 2.0 + np.array([10.0, 20.0], dtype=np.float32)
    m = alignment_matrix(landmarks)
    mapped = cv2.transform(landmarks.reshape(-1, 1, 2), m).reshape(-1, 2)
    assert np.allclose(mapped, ARCFACE_TEMPLATE, atol=1e-2)


def test_align_face_output_is_112_square():
    img = np.random.default_rng(0).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    out = align_face(img, ARCFACE_TEMPLATE * 2.0)
    assert out.shape == (112, 112, 3)
```

`backend/tests/subjects/test_analyzer.py`:

```python
from pathlib import Path

import cv2
import numpy as np

from app.subjects.analyzer import FaceAnalyzer, face_thumbnail_jpeg
from app.subjects.config import SubjectSettings
from app.subjects.detector import FaceDetection
from app.subjects.embedder import ARCFACE_TEMPLATE

FRONTAL = ARCFACE_TEMPLATE * 2.0 + np.array([40.0, 40.0], dtype=np.float32)


def _write_noise(path: Path, seed: int) -> str:
    img = np.random.default_rng(seed).integers(0, 255, (320, 320, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return str(path)


def _key(marker: int) -> int:
    # markers are 50 apart, so JPEG drift of a few levels still maps back
    return int(round(marker / 50.0))


class FakeDetector:
    """Identifies each crop by the flat 16x16 marker block written by _crop()."""

    def __init__(self, by_marker: dict[int, FaceDetection | None]):
        self.by_key = {_key(m): det for m, det in by_marker.items()}

    def detect_best(self, img):
        return self.by_key.get(_key(float(img[4, 4, 0])))


class FakeEmbedder:
    def embed(self, aligned):
        v = np.ones(512, dtype=np.float32)
        return v / np.linalg.norm(v)


def _crop(tmp_path: Path, marker: int) -> str:
    path = tmp_path / f"{marker:02d}.jpg"
    img = np.random.default_rng(marker).integers(0, 255, (320, 320, 3), dtype=np.uint8)
    img[:16, :16] = marker  # one full JPEG MCU, so it survives chroma subsampling
    cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 100])
    return str(path)


def _det(score: float, kps=FRONTAL) -> FaceDetection:
    return FaceDetection(score=score, bbox=np.array([40.0, 40.0, 260.0, 300.0]), kps=kps)


def test_keeps_top_three_by_quality(tmp_path: Path):
    markers = [10, 60, 110, 160, 210]
    scores = [0.70, 0.95, 0.80, 0.90, 0.75]
    det = FakeDetector({m: _det(s) for m, s in zip(markers, scores)})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    faces = analyzer([_crop(tmp_path, m) for m in markers])
    assert len(faces) == 3
    assert faces[0].quality >= faces[1].quality >= faces[2].quality
    assert faces[0].embedding.shape == (512,)
    assert faces[0].face_jpeg[:2] == b"\xff\xd8"


def test_skips_unreadable_and_faceless_crops(tmp_path: Path):
    det = FakeDetector({})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    assert analyzer([str(tmp_path / "missing.jpg"), _crop(tmp_path, 10)]) == []


def test_rejects_profile_faces(tmp_path: Path):
    profile = FRONTAL.copy()
    profile[2, 0] += 60.0  # nose far to one side
    det = FakeDetector({10: _det(0.95, profile)})
    analyzer = FaceAnalyzer(det, FakeEmbedder(), SubjectSettings())
    assert analyzer([_crop(tmp_path, 10)]) == []


def test_face_thumbnail_is_decodable_and_clamped():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    data = face_thumbnail_jpeg(img, np.array([-10.0, -10.0, 50.0, 50.0]))
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and decoded.shape[0] <= 100 and decoded.shape[1] <= 100
```

`backend/tests/subjects/test_face_models_integration.py`:

```python
"""Real SCRFD + ArcFace. Skipped unless backend/models/face/*.onnx exist
(run scripts/download_face_models.py). Identity fixtures are optional: set
WW_FACE_FIXTURES_DIR to a folder with identity_a/*.jpg and identity_b/*.jpg
(photos of consenting people, >= 2 each)."""
import os
from itertools import combinations
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.subjects.analyzer import FaceAnalyzer
from app.subjects.config import SubjectSettings
from app.subjects.detector import ScrfdDetector
from app.subjects.embedder import ArcFaceEmbedder

MODELS = Path(__file__).resolve().parents[2] / "models" / "face"
DET, REC = MODELS / "det_10g.onnx", MODELS / "w600k_r50.onnx"

pytestmark = pytest.mark.skipif(
    not (DET.exists() and REC.exists() and DET.stat().st_size > 1_000_000),
    reason="face models not downloaded (scripts/download_face_models.py)",
)


@pytest.fixture(scope="module")
def analyzer():
    return FaceAnalyzer(ScrfdDetector(str(DET)), ArcFaceEmbedder(str(REC)), SubjectSettings())


def test_blank_image_has_no_face(analyzer, tmp_path):
    p = tmp_path / "blank.jpg"
    cv2.imwrite(str(p), np.full((240, 240, 3), 127, dtype=np.uint8))
    assert analyzer([str(p)]) == []


@pytest.mark.skipif(not os.environ.get("WW_FACE_FIXTURES_DIR"), reason="WW_FACE_FIXTURES_DIR not set")
def test_same_identity_scores_higher_than_different(analyzer):
    root = Path(os.environ["WW_FACE_FIXTURES_DIR"])
    emb = {
        ident: [f.embedding for p in sorted((root / ident).glob("*.jpg")) for f in analyzer([str(p)])[:1]]
        for ident in ("identity_a", "identity_b")
    }
    assert all(len(v) >= 2 for v in emb.values()), "need >= 2 detectable faces per identity"
    same = [float(np.dot(x, y)) for v in emb.values() for x, y in combinations(v, 2)]
    cross = [float(np.dot(x, y)) for x in emb["identity_a"] for y in emb["identity_b"]]
    assert np.mean(same) > np.mean(cross) + 0.2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_types.py tests/subjects/test_detector.py tests/subjects/test_embedder.py tests/subjects/test_analyzer.py tests/subjects/test_face_models_integration.py -v`
Expected: `ModuleNotFoundError` for `app.subjects.types` / `detector` / `embedder` / `analyzer`.

- [ ] **Step 3: Implement `types.py`**

`backend/app/subjects/types.py`:

```python
"""Plain data passed between the worker, analyzer and service. No cv2/onnx imports."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AnalyzedFace:
    quality: float
    embedding: np.ndarray  # (512,) float32, L2-normalised
    face_jpeg: bytes       # face thumbnail stored for operator review


@dataclass(frozen=True)
class FaceRequest:
    incident_id: uuid.UUID
    camera_id: str
    detected_at: datetime  # aware UTC
    crops: tuple[str, ...]
    person_track_id: int | None = None

    @classmethod
    def from_payload(cls, payload: dict, incoming_dir: Path) -> "FaceRequest":
        """Parse a wastewatch.faces.requests message.

        Only crop paths inside incoming_dir/<incident_id>/ are kept -- anything
        else (another incident's folder, ../ traversal, absolute paths
        elsewhere) is dropped, so a bad message can never make the worker
        read, move or delete files it does not own.
        """
        incident_id = uuid.UUID(str(payload["incident_id"]))
        own_dir = (Path(incoming_dir) / str(incident_id)).resolve()
        crops = []
        for raw in payload.get("crops") or []:
            path = Path(str(raw)).resolve()
            if path.parent == own_dir:
                crops.append(str(path))
        ts = payload.get("detected_at")
        detected_at = (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            if isinstance(ts, (int, float))
            else datetime.now(timezone.utc)
        )
        track = payload.get("person_track_id")
        return cls(
            incident_id=incident_id,
            camera_id=str(payload.get("camera_id") or "unknown"),
            detected_at=detected_at,
            crops=tuple(crops),
            person_track_id=int(track) if track is not None else None,
        )
```

- [ ] **Step 4: Implement `detector.py`**

`backend/app/subjects/detector.py`:

```python
"""SCRFD face detector (ONNX, CPU).

Post-processing follows InsightFace's SCRFD reference implementation
(MIT-licensed code). The det_10g.onnx weights are research-only -- see
the spec's licensing section before commercial deployment.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class FaceDetection:
    score: float
    bbox: np.ndarray  # (4,) x1, y1, x2, y2 in source-image pixels
    kps: np.ndarray   # (5, 2) landmarks in source-image pixels


def letterbox(img: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio into the top-left of a black (w, h) canvas."""
    w_in, h_in = size
    h, w = img.shape[:2]
    scale = min(w_in / w, h_in / h)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    canvas = np.zeros((h_in, w_in, 3), dtype=np.uint8)
    canvas[:new_h, :new_w] = cv2.resize(img, (new_w, new_h))
    return canvas, scale


def decode_stride(
    scores: np.ndarray,
    bbox_preds: np.ndarray,
    kps_preds: np.ndarray,
    stride: int,
    height: int,
    width: int,
    num_anchors: int,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Scores, boxes (N,4) and landmarks (N,5,2) above threshold for one FPN level."""
    centers = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(np.float32)
    centers = (centers * stride).reshape(-1, 2)
    if num_anchors > 1:
        centers = np.stack([centers] * num_anchors, axis=1).reshape(-1, 2)
    flat_scores = scores.reshape(-1)
    keep = np.where(flat_scores >= threshold)[0]
    dist = bbox_preds.reshape(-1, 4)[keep] * stride
    kdist = kps_preds.reshape(-1, 10)[keep] * stride
    c = centers[keep]
    bboxes = np.stack(
        [c[:, 0] - dist[:, 0], c[:, 1] - dist[:, 1], c[:, 0] + dist[:, 2], c[:, 1] + dist[:, 3]],
        axis=-1,
    ).reshape(-1, 4)
    kpss = np.empty((len(keep), 5, 2), dtype=np.float32)
    kpss[:, :, 0] = c[:, 0:1] + kdist[:, 0::2]
    kpss[:, :, 1] = c[:, 1:2] + kdist[:, 1::2]
    return flat_scores[keep], bboxes, kpss


class ScrfdDetector:
    STRIDES = (8, 16, 32)
    NUM_ANCHORS = 2

    def __init__(self, model_path: str, input_size: tuple[int, int] = (320, 320), det_thresh: float = 0.5):
        import onnxruntime as ort

        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        if len(self._output_names) != 9:
            raise ValueError(f"Expected SCRFD with keypoints (9 outputs), got {len(self._output_names)}")
        self._input_size = input_size
        self._det_thresh = det_thresh

    def detect_best(self, img_bgr: np.ndarray) -> FaceDetection | None:
        det_img, scale = letterbox(img_bgr, self._input_size)
        blob = cv2.dnn.blobFromImage(
            det_img, 1.0 / 128.0, self._input_size, (127.5, 127.5, 127.5), swapRB=True
        )
        outs = self._session.run(self._output_names, {self._input_name: blob})
        w_in, h_in = self._input_size
        best: tuple[float, np.ndarray, np.ndarray] | None = None
        for idx, stride in enumerate(self.STRIDES):
            s, b, k = (o[0] if o.ndim == 3 else o for o in (outs[idx], outs[idx + 3], outs[idx + 6]))
            scores, bboxes, kpss = decode_stride(
                s, b, k, stride, h_in // stride, w_in // stride, self.NUM_ANCHORS, self._det_thresh
            )
            if len(scores):
                i = int(np.argmax(scores))
                if best is None or float(scores[i]) > best[0]:
                    best = (float(scores[i]), bboxes[i], kpss[i])
        if best is None:
            return None
        score, bbox, kps = best
        return FaceDetection(score=score, bbox=bbox / scale, kps=kps / scale)
```

- [ ] **Step 5: Implement `embedder.py`**

`backend/app/subjects/embedder.py`:

```python
"""ArcFace alignment + embedding (ONNX, CPU)."""
from __future__ import annotations

import cv2
import numpy as np

# Standard ArcFace 112x112 five-point template.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def alignment_matrix(landmarks: np.ndarray) -> np.ndarray:
    matrix, _ = cv2.estimateAffinePartial2D(
        np.asarray(landmarks, dtype=np.float32), ARCFACE_TEMPLATE, method=cv2.LMEDS
    )
    if matrix is None:
        raise ValueError("Could not estimate face alignment")
    return matrix


def align_face(img_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    return cv2.warpAffine(img_bgr, alignment_matrix(landmarks), (112, 112), borderValue=0.0)


class ArcFaceEmbedder:
    def __init__(self, model_path: str):
        import onnxruntime as ort

        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(
            aligned_bgr, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        vector = self._session.run(None, {self._input_name: blob})[0][0].astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector
```

- [ ] **Step 6: Implement `analyzer.py`**

`backend/app/subjects/analyzer.py`:

```python
"""Crops -> the best few usable faces (spec §6.2 steps 2-5)."""
from __future__ import annotations

import cv2
import numpy as np

from app.subjects.config import SubjectSettings
from app.subjects.embedder import align_face
from app.subjects.quality import laplacian_sharpness, passes_gate, quality_score, yaw_proxy
from app.subjects.types import AnalyzedFace


def face_thumbnail_jpeg(img: np.ndarray, bbox: np.ndarray, margin: float = 0.3) -> bytes:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    xa, ya = max(0, int(x1 - mx)), max(0, int(y1 - my))
    xb, yb = min(w, int(x2 + mx)), min(h, int(y2 + my))
    ok, buf = cv2.imencode(".jpg", img[ya:yb, xa:xb], [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("JPEG encode failed")
    return buf.tobytes()


class FaceAnalyzer:
    def __init__(self, detector, embedder, settings: SubjectSettings):
        self._detector = detector
        self._embedder = embedder
        self._settings = settings

    def __call__(self, crop_paths: list[str]) -> list[AnalyzedFace]:
        candidates = []
        for path in crop_paths:
            img = cv2.imread(path)
            if img is None:
                continue
            det = self._detector.detect_best(img)
            if det is None:
                continue
            width = float(det.bbox[2] - det.bbox[0])
            try:
                aligned = align_face(img, det.kps)
            except ValueError:
                continue
            sharpness = laplacian_sharpness(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY))
            yaw = yaw_proxy(det.kps)
            if not passes_gate(det.score, width, sharpness, yaw, self._settings):
                continue
            candidates.append((quality_score(det.score, width, sharpness, yaw), img, det, aligned))
        candidates.sort(key=lambda c: c[0], reverse=True)
        return [
            AnalyzedFace(
                quality=quality,
                embedding=self._embedder.embed(aligned),
                face_jpeg=face_thumbnail_jpeg(img, det.bbox),
            )
            for quality, img, det, aligned in candidates[: self._settings.keep_per_incident]
        ]
```

- [ ] **Step 7: Run the unit tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_types.py tests/subjects/test_detector.py tests/subjects/test_embedder.py tests/subjects/test_analyzer.py -v`
Expected: 12 passed.

- [ ] **Step 8: Add the model download script and fetch the weights**

`scripts/download_face_models.py`:

```python
#!/usr/bin/env python3
"""Download SCRFD (det_10g) + ArcFace (w600k_r50) from InsightFace's buffalo_l pack.

LICENCE: InsightFace pretrained weights are for NON-COMMERCIAL RESEARCH ONLY.
A paid deployment needs a commercial licence from InsightFace or a different
model (see docs/superpowers/specs/2026-09-29-face-subject-id-design.md §13).
"""
from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"
WANTED = ("det_10g.onnx", "w600k_r50.onnx")
DEST = Path(__file__).resolve().parent.parent / "backend" / "models" / "face"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    if all((DEST / name).exists() for name in WANTED):
        print(f"Face models already present in {DEST}")
        return 0
    print("NOTE: InsightFace weights are licensed for non-commercial research use only.")
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL, timeout=300) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name in WANTED:
                (DEST / name).write_bytes(zf.read(member))
                print(f"  wrote {DEST / name}")
    missing = [n for n in WANTED if not (DEST / n).exists()]
    if missing:
        print(f"ERROR: missing after download: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run it and the integration test:

```bash
backend/.venv/Scripts/python scripts/download_face_models.py
cd backend && .venv/Scripts/python -m pytest tests/subjects/test_face_models_integration.py -v
```

Expected: the download writes both files, and `test_blank_image_has_no_face` PASSES. `test_same_identity_scores_higher_than_different` is SKIPPED unless `WW_FACE_FIXTURES_DIR` is set. If the download is blocked (no network), both tests are skipped; note that in the commit message.

- [ ] **Step 9: Commit (models go through Git LFS via the existing `*.onnx` rule)**

```bash
git lfs track "*.onnx" >/dev/null   # rule already exists in .gitattributes; this is a no-op check
git add backend/app/subjects/types.py backend/app/subjects/detector.py backend/app/subjects/embedder.py backend/app/subjects/analyzer.py scripts/download_face_models.py backend/tests/subjects/test_types.py backend/tests/subjects/test_detector.py backend/tests/subjects/test_embedder.py backend/tests/subjects/test_analyzer.py backend/tests/subjects/test_face_models_integration.py backend/models/face
git commit -m "feat(subjects): SCRFD detector, ArcFace embedder, face analyzer, request parsing

Models: InsightFace buffalo_l det_10g + w600k_r50 (LFS). Research-only
licence -- see spec §13.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Qdrant face index

**Files:**
- Create: `backend/app/subjects/vector_store.py`
- Test: `backend/tests/subjects/test_vector_store.py`

**Interfaces:**
- Consumes: `matcher.Hit` (Task 5).
- Produces: `FaceVectorStore(client: QdrantClient, collection: str = "offender_faces", dim: int = 512)`, with methods:
  - `.ensure_collection() -> None` (idempotent)
  - `.upsert(face_id: uuid.UUID, vector, subject_id: uuid.UUID, incident_id: uuid.UUID) -> None` (overwrites payload)
  - `.search(vector, limit: int = 20) -> list[Hit]`
  - `.delete(face_ids: list[uuid.UUID]) -> None`

  All methods are synchronous; the service calls them through `asyncio.to_thread`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_vector_store.py`:

```python
import uuid

import numpy as np
import pytest
from qdrant_client import QdrantClient

from app.subjects.vector_store import FaceVectorStore


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def store():
    s = FaceVectorStore(QdrantClient(":memory:"), "test_faces")
    s.ensure_collection()
    return s


def test_ensure_collection_is_idempotent(store):
    store.ensure_collection()


def test_search_empty_collection(store):
    assert store.search(_unit(1)) == []


def test_upsert_and_search_returns_subject_hits(store):
    a, b = uuid.uuid4(), uuid.uuid4()
    fa = uuid.uuid4()
    store.upsert(fa, _unit(1), a, uuid.uuid4())
    store.upsert(uuid.uuid4(), _unit(2), b, uuid.uuid4())
    hits = store.search(_unit(1), limit=2)
    assert hits[0].subject_id == a and hits[0].face_id == fa
    assert hits[0].score == pytest.approx(1.0, abs=1e-4)
    assert len(hits) == 2


def test_upsert_overwrites_subject(store):
    face, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    store.upsert(face, _unit(1), a, uuid.uuid4())
    store.upsert(face, _unit(1), b, uuid.uuid4())
    [hit] = store.search(_unit(1))
    assert hit.subject_id == b


def test_delete_removes_points(store):
    face = uuid.uuid4()
    store.upsert(face, _unit(1), uuid.uuid4(), uuid.uuid4())
    store.delete([face])
    store.delete([])
    assert store.search(_unit(1)) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_vector_store.py -v`
Expected: `ModuleNotFoundError: No module named 'app.subjects.vector_store'`.

- [ ] **Step 3: Implement**

`backend/app/subjects/vector_store.py`:

```python
"""Qdrant index of subject face embeddings (collection offender_faces).

An index only: every vector also lives in subject_faces.embedding, so the
collection can be rebuilt from Postgres (SubjectService.resync_index).
"""
from __future__ import annotations

import uuid
from typing import Sequence

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.subjects.matcher import Hit


class FaceVectorStore:
    def __init__(self, client: QdrantClient, collection: str = "offender_faces", dim: int = 512):
        self._client = client
        self._collection = collection
        self._dim = dim

    def ensure_collection(self) -> None:
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                self._collection,
                vectors_config=qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE),
            )

    def upsert(self, face_id: uuid.UUID, vector: Sequence[float], subject_id: uuid.UUID, incident_id: uuid.UUID) -> None:
        self._client.upsert(
            self._collection,
            points=[
                qm.PointStruct(
                    id=str(face_id),
                    vector=[float(x) for x in vector],
                    payload={"subject_id": str(subject_id), "incident_id": str(incident_id)},
                )
            ],
        )

    def search(self, vector: Sequence[float], limit: int = 20) -> list[Hit]:
        result = self._client.query_points(
            self._collection, query=[float(x) for x in vector], limit=limit, with_payload=True
        )
        return [
            Hit(
                subject_id=uuid.UUID(point.payload["subject_id"]),
                face_id=uuid.UUID(str(point.id)),
                score=float(point.score),
            )
            for point in result.points
        ]

    def delete(self, face_ids: list[uuid.UUID]) -> None:
        if not face_ids:
            return
        self._client.delete(
            self._collection,
            points_selector=qm.PointIdsList(points=[str(f) for f in face_ids]),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_vector_store.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/subjects/vector_store.py backend/tests/subjects/test_vector_store.py
git commit -m "feat(subjects): Qdrant face index wrapper

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: SubjectService — processing a face request

**Files:**
- Create: `backend/app/subjects/service.py`
- Create: `backend/tests/subjects/factories.py`
- Modify: `backend/tests/subjects/conftest.py` (add fixtures)
- Test: `backend/tests/subjects/test_service_process.py`

**Interfaces:**
- Consumes:
  - the models (Task 4);
  - `SubjectSettings` (Task 4);
  - `Hit`, `best_per_subject`, `decide`, `faces_over_cap` (Task 5);
  - `AnalyzedFace`, `FaceRequest` (Task 6);
  - a `FaceVectorStore`-shaped object (Task 7).
- Produces:
  - `service.NotFound`, `service.Conflict` (exceptions).
  - `service.ProcessResult(status, subject_id, subject_code, match_type, incident_count, similarity)`.
  - `service.embedding_to_bytes(v) -> bytes`, `service.embedding_from_bytes(b) -> np.ndarray`, `service.utcnow() -> datetime`.
  - `SubjectService(session_factory, vector_store, settings)` with:
    - public attributes `.session_factory` and `.settings`;
    - `async process_request(req: FaceRequest, faces: list[AnalyzedFace]) -> ProcessResult`;
    - private helpers `_new_subject`, `_recompute`, `_enforce_face_cap`, `_index_faces`, `_drop_faces`, `_remove_incoming`, `_face_path`, which Tasks 9–10 reuse.
  - Test helpers in `tests/subjects/factories.py`: `T0`, `unit(seed)`, `with_similarity(base, sim, seed=999)`, `face(vec, quality=0.8)`, `request(incident_id=None, camera_id="cam-01", at=T0)`, `FailingStore`, `FlakyStore`.
  - Fixtures `settings`, `vector_store` and `service` in `tests/subjects/conftest.py`.

- [ ] **Step 1: Add test helpers and fixtures**

`backend/tests/subjects/factories.py`:

```python
"""Deterministic embeddings, requests and fake stores for subject-service tests."""
import uuid
from datetime import datetime, timezone

import numpy as np

from app.subjects.types import AnalyzedFace, FaceRequest

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
JPEG = b"\xff\xd8fake-jpeg\xff\xd9"


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(512).astype(np.float32)
    return v / np.linalg.norm(v)


def with_similarity(base: np.ndarray, sim: float, seed: int = 999) -> np.ndarray:
    """A unit vector whose cosine similarity to `base` is exactly `sim`."""
    u = unit(seed)
    u = u - float(np.dot(u, base)) * base
    u = u / np.linalg.norm(u)
    v = sim * base + np.sqrt(1.0 - sim * sim) * u
    return (v / np.linalg.norm(v)).astype(np.float32)


def face(vec: np.ndarray, quality: float = 0.8) -> AnalyzedFace:
    return AnalyzedFace(quality=quality, embedding=vec, face_jpeg=JPEG)


def request(incident_id: uuid.UUID | None = None, camera_id: str = "cam-01", at: datetime = T0) -> FaceRequest:
    return FaceRequest(incident_id=incident_id or uuid.uuid4(), camera_id=camera_id, detected_at=at, crops=())


class FailingStore:
    """Qdrant is down: every call raises."""

    def ensure_collection(self):
        pass

    def search(self, vector, limit=20):
        raise ConnectionError("qdrant down")

    def upsert(self, face_id, vector, subject_id, incident_id):
        raise ConnectionError("qdrant down")

    def delete(self, face_ids):
        raise ConnectionError("qdrant down")


class FlakyStore:
    """Wraps a real store; the first `fail_upserts` upserts raise."""

    def __init__(self, inner, fail_upserts: int):
        self.inner = inner
        self.fail_upserts = fail_upserts

    def ensure_collection(self):
        self.inner.ensure_collection()

    def search(self, vector, limit=20):
        return self.inner.search(vector, limit)

    def upsert(self, face_id, vector, subject_id, incident_id):
        if self.fail_upserts > 0:
            self.fail_upserts -= 1
            raise ConnectionError("qdrant blip")
        self.inner.upsert(face_id, vector, subject_id, incident_id)

    def delete(self, face_ids):
        self.inner.delete(face_ids)
```

Append to `backend/tests/subjects/conftest.py`:

```python
from qdrant_client import QdrantClient

from app.subjects.config import SubjectSettings
from app.subjects.service import SubjectService
from app.subjects.vector_store import FaceVectorStore


@pytest.fixture
def settings(tmp_path):
    return SubjectSettings(faces_dir=str(tmp_path / "faces"))


@pytest.fixture
def vector_store():
    store = FaceVectorStore(QdrantClient(":memory:"), "test_faces")
    store.ensure_collection()
    return store


@pytest.fixture
def service(session_factory, vector_store, settings):
    return SubjectService(session_factory, vector_store, settings)
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/subjects/test_service_process.py`:

```python
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.db.models import FaceIncident, Subject, SubjectFace, SubjectMatchReview
from app.subjects.config import SubjectSettings
from app.subjects.service import SubjectService

from tests.subjects.factories import FailingStore, T0, face, request, unit, with_similarity


async def test_first_incident_creates_new_subject(service, session_factory, settings):
    req = request()
    result = await service.process_request(req, [face(unit(1))])
    assert (result.status, result.match_type, result.subject_code, result.incident_count) == (
        "matched", "new", "S-001", 1,
    )
    async with session_factory() as s:
        row = (await s.execute(select(SubjectFace))).scalar_one()
        assert row.qdrant_synced is True
        crop = Path(row.crop_path)
        assert crop.read_bytes().startswith(b"\xff\xd8")
        assert crop.is_relative_to(Path(settings.faces_dir) / "subjects" / str(result.subject_id))
        assert (await s.get(FaceIncident, req.incident_id)).status == "matched"


async def test_same_face_twice_is_auto_repeat(service):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(at=T0 + timedelta(days=3)), [face(with_similarity(v, 0.8))])
    assert second.match_type == "auto"
    assert second.subject_id == first.subject_id
    assert second.incident_count == 2
    assert second.similarity == pytest.approx(0.8, abs=1e-3)


async def test_uncertain_match_creates_new_subject_and_review(service, session_factory):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.45))])
    assert (second.match_type, second.subject_code) == ("review", "S-002")
    async with session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
        assert review.status == "pending"
        assert review.new_subject_id == second.subject_id
        assert review.candidate_subject_id == first.subject_id
        assert review.similarity == pytest.approx(0.45, abs=1e-3)


async def test_different_face_is_new_without_review(service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.2))])
    assert (second.match_type, second.subject_code) == ("new", "S-002")
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(SubjectMatchReview))).scalar_one() == 0


async def test_no_faces_records_no_face_and_cleans_incoming(service, session_factory, settings):
    req = request()
    incoming = Path(settings.faces_dir) / "incoming" / str(req.incident_id)
    incoming.mkdir(parents=True)
    (incoming / "00.jpg").write_bytes(b"x")
    result = await service.process_request(req, [])
    assert result.status == "no_face"
    assert not incoming.exists()
    async with session_factory() as s:
        assert (await s.get(FaceIncident, req.incident_id)).status == "no_face"
        assert (await s.execute(select(func.count()).select_from(Subject))).scalar_one() == 0


async def test_duplicate_incident_is_ignored(service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    again = await service.process_request(req, [face(unit(1))])
    assert again.status == "duplicate"
    third = await service.process_request(request(), [face(unit(1))])
    assert third.incident_count == 2


async def test_face_cap_keeps_best_quality(session_factory, vector_store, tmp_path):
    settings = SubjectSettings(faces_dir=str(tmp_path / "faces"), max_faces=2)
    service = SubjectService(session_factory, vector_store, settings)
    v = unit(1)
    first = await service.process_request(request(), [face(v, quality=0.1)])
    await service.process_request(request(), [face(with_similarity(v, 0.9, seed=5), quality=0.9)])
    await service.process_request(request(), [face(with_similarity(v, 0.9, seed=6), quality=0.5)])
    async with session_factory() as s:
        rows = (await s.execute(select(SubjectFace).order_by(SubjectFace.quality.desc()))).scalars().all()
        assert [r.quality for r in rows] == [0.9, 0.5]
        subject = await s.get(Subject, first.subject_id)
        assert subject.cover_face_id == rows[0].id
        assert subject.incident_count == 3
    files = list((tmp_path / "faces" / "subjects" / str(first.subject_id)).glob("*.jpg"))
    assert len(files) == 2
    assert len(vector_store.search(v, 20)) == 2


async def test_qdrant_failure_keeps_face_unsynced(session_factory, settings):
    service = SubjectService(session_factory, FailingStore(), settings)
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    second = await service.process_request(request(), [face(with_similarity(v, 0.8))])
    assert first.status == "matched"
    # exact search over Postgres embeddings still links the repeat
    assert second.match_type == "auto" and second.subject_id == first.subject_id
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalars().all() == [False, False]


async def test_hits_on_merged_subject_are_ignored(service, session_factory):
    v = unit(1)
    first = await service.process_request(request(), [face(v)])
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == first.subject_id).values(status="merged"))
        await s.commit()
    second = await service.process_request(request(), [face(v)])
    assert second.match_type == "new"
    assert second.subject_id != first.subject_id
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_service_process.py -v`
Expected: `ModuleNotFoundError: No module named 'app.subjects.service'`.

- [ ] **Step 4: Implement `service.py`**

`backend/app/subjects/service.py`:

```python
"""Subject lifecycle: match violation faces to anonymous subjects.

Postgres is the source of truth (embeddings included); Qdrant is an index
updated after each commit. Faces whose index write failed keep
qdrant_synced=False and are re-indexed by resync_index(); if Qdrant search
itself fails, matching falls back to an exact search over Postgres.
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §6-8.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sqlalchemy import func, select, text, update

from app.db.models import FaceIncident, Subject, SubjectFace, SubjectIncident, SubjectMatchReview
from app.subjects.config import SubjectSettings
from app.subjects.matcher import Hit, best_per_subject, decide, faces_over_cap
from app.subjects.types import AnalyzedFace, FaceRequest

logger = logging.getLogger(__name__)

SEARCH_LIMIT = 20


class NotFound(Exception):
    """The subject, review or incident does not exist."""


class Conflict(Exception):
    """The action no longer applies (review already decided, subjects changed, ...)."""


@dataclass(frozen=True)
class ProcessResult:
    status: str  # matched | no_face | duplicate
    subject_id: uuid.UUID | None = None
    subject_code: str | None = None
    match_type: str | None = None  # new | auto | review
    incident_count: int | None = None
    similarity: float | None = None


def embedding_to_bytes(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def embedding_from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.float32)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SubjectService:
    def __init__(self, session_factory, vector_store, settings: SubjectSettings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._vs = vector_store
        self._faces_dir = Path(settings.faces_dir)

    # ── incident processing ────────────────────────────────────────

    async def process_request(self, req: FaceRequest, faces: list[AnalyzedFace]) -> ProcessResult:
        async with self.session_factory() as s:
            if await s.get(FaceIncident, req.incident_id) is not None:
                return ProcessResult("duplicate")
            if not faces:
                s.add(FaceIncident(
                    incident_id=req.incident_id, status="no_face",
                    camera_id=req.camera_id, detected_at=req.detected_at,
                ))
                await s.commit()
                self._remove_incoming(req.incident_id)
                return ProcessResult("no_face")

            per_subject = await self._score_subjects(s, [f.embedding for f in faces])
            decision = decide(per_subject, self.settings.match_auto, self.settings.match_review)
            if decision.kind == "auto":
                subject = await s.get(Subject, decision.subject_id)
            else:
                subject = await self._new_subject(s, req.detected_at)
            if decision.kind == "review":
                s.add(SubjectMatchReview(
                    incident_id=req.incident_id, new_subject_id=subject.id,
                    candidate_subject_id=decision.subject_id, similarity=decision.score,
                ))
            s.add(SubjectIncident(
                incident_id=req.incident_id, subject_id=subject.id, camera_id=req.camera_id,
                detected_at=req.detected_at,
                match_type="auto" if decision.kind == "auto" else "new",
                similarity=decision.score,
            ))
            new_rows: list[tuple[SubjectFace, AnalyzedFace]] = []
            for analyzed in faces:
                face_id = uuid.uuid4()
                row = SubjectFace(
                    id=face_id, subject_id=subject.id, incident_id=req.incident_id,
                    crop_path=str(self._face_path(subject.id, face_id)),
                    quality=analyzed.quality, embedding=embedding_to_bytes(analyzed.embedding),
                    qdrant_synced=False,
                )
                s.add(row)
                new_rows.append((row, analyzed))
            s.add(FaceIncident(
                incident_id=req.incident_id, status="matched",
                camera_id=req.camera_id, detected_at=req.detected_at,
            ))
            await s.flush()
            evicted = await self._enforce_face_cap(s, subject.id)
            await self._recompute(s, subject)
            await s.commit()

        evicted_ids = {row.id for row in evicted}
        kept = [(row, analyzed) for row, analyzed in new_rows if row.id not in evicted_ids]
        for row, analyzed in kept:
            path = Path(row.crop_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(analyzed.face_jpeg)
        await self._index_faces([row for row, _ in kept])
        await self._drop_faces(evicted)
        self._remove_incoming(req.incident_id)
        return ProcessResult(
            "matched", subject.id, subject.code, decision.kind, subject.incident_count, decision.score
        )

    # ── helpers (shared with operator actions / retention) ─────────

    async def _score_subjects(self, s, vectors: list[np.ndarray]) -> dict[uuid.UUID, float]:
        try:
            hits: list[Hit] = []
            for vector in vectors:
                hits.extend(await asyncio.to_thread(self._vs.search, vector, SEARCH_LIMIT))
        except Exception:
            logger.warning("Qdrant search failed; using exact search over Postgres", exc_info=True)
            hits = await self._exact_search(s, vectors)
        per_subject = best_per_subject(hits)
        if not per_subject:
            return {}
        active = set((await s.execute(
            select(Subject.id).where(Subject.id.in_(list(per_subject)), Subject.status == "active")
        )).scalars())
        return {sid: score for sid, score in per_subject.items() if sid in active}

    async def _exact_search(self, s, vectors: list[np.ndarray]) -> list[Hit]:
        rows = (await s.execute(
            select(SubjectFace.id, SubjectFace.subject_id, SubjectFace.embedding)
            .join(Subject, Subject.id == SubjectFace.subject_id)
            .where(Subject.status == "active")
        )).all()
        hits = []
        for face_id, subject_id, data in rows:
            stored = embedding_from_bytes(data)
            for vector in vectors:
                score = float(np.dot(stored, np.asarray(vector, dtype=np.float32)))
                hits.append(Hit(subject_id=subject_id, face_id=face_id, score=score))
        return hits

    async def _new_subject(self, s, seen_at: datetime) -> Subject:
        subject = Subject(
            code_num=await self._next_code_num(s), status="active", incident_count=0,
            first_seen_at=seen_at, last_seen_at=seen_at,
        )
        s.add(subject)
        await s.flush()
        return subject

    async def _next_code_num(self, s) -> int:
        if s.get_bind().dialect.name == "postgresql":
            return int((await s.execute(text("SELECT nextval('subject_code_seq')"))).scalar_one())
        current = (await s.execute(select(func.coalesce(func.max(Subject.code_num), 0)))).scalar_one()
        return int(current) + 1

    async def _recompute(self, s, subject: Subject) -> None:
        count, first, last = (await s.execute(
            select(func.count(), func.min(SubjectIncident.detected_at), func.max(SubjectIncident.detected_at))
            .where(SubjectIncident.subject_id == subject.id)
        )).one()
        subject.incident_count = int(count)
        if count:
            subject.first_seen_at, subject.last_seen_at = first, last
        subject.cover_face_id = (await s.execute(
            select(SubjectFace.id).where(SubjectFace.subject_id == subject.id)
            .order_by(SubjectFace.quality.desc(), SubjectFace.id).limit(1)
        )).scalar_one_or_none()

    async def _enforce_face_cap(self, s, subject_id: uuid.UUID) -> list[SubjectFace]:
        rows = (await s.execute(select(SubjectFace).where(SubjectFace.subject_id == subject_id))).scalars().all()
        over = set(faces_over_cap([(r.id, r.quality) for r in rows], self.settings.max_faces))
        evicted = [r for r in rows if r.id in over]
        for row in evicted:
            await s.delete(row)
        await s.flush()
        return evicted

    async def _index_faces(self, rows: list[SubjectFace]) -> int:
        synced = []
        for row in rows:
            try:
                await asyncio.to_thread(
                    self._vs.upsert, row.id, embedding_from_bytes(row.embedding), row.subject_id, row.incident_id
                )
                synced.append(row.id)
            except Exception:
                logger.warning("Qdrant upsert failed for face %s; will resync", row.id, exc_info=True)
        if synced:
            async with self.session_factory() as s:
                await s.execute(update(SubjectFace).where(SubjectFace.id.in_(synced)).values(qdrant_synced=True))
                await s.commit()
        return len(synced)

    async def _drop_faces(self, rows: list[SubjectFace]) -> None:
        if not rows:
            return
        try:
            await asyncio.to_thread(self._vs.delete, [r.id for r in rows])
        except Exception:
            logger.warning("Qdrant delete failed for %d faces", len(rows), exc_info=True)
        for row in rows:
            Path(row.crop_path).unlink(missing_ok=True)

    def _face_path(self, subject_id: uuid.UUID, face_id: uuid.UUID) -> Path:
        return self._faces_dir / "subjects" / str(subject_id) / f"{face_id}.jpg"

    def _remove_incoming(self, incident_id: uuid.UUID) -> None:
        shutil.rmtree(self._faces_dir / "incoming" / str(incident_id), ignore_errors=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_service_process.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/subjects/service.py backend/tests/subjects/factories.py backend/tests/subjects/conftest.py backend/tests/subjects/test_service_process.py
git commit -m "feat(subjects): process face requests into new/auto/review subjects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Operator actions — merge, reject, detach, delete, access log

**Files:**
- Modify: `backend/app/subjects/service.py`
- Test: `backend/tests/subjects/test_service_actions.py`

**Interfaces:**
- Consumes: Task 8's `SubjectService` internals.
- Produces (all methods on `SubjectService`):
  - `async log_access(s, subject_id: uuid.UUID | None, action: str, actor: str, client_ip: str | None = None, detail: dict | None = None) -> None`. It adds a row; the caller commits.
  - `async merge_review(review_id: uuid.UUID, actor: str, client_ip: str | None = None) -> uuid.UUID`. Returns the surviving subject id. Raises `NotFound` or `Conflict`.
  - `async reject_review(review_id, actor, client_ip=None) -> None`. Raises `NotFound` or `Conflict`.
  - `async detach_incident(subject_id, incident_id, actor, client_ip=None) -> uuid.UUID`. Returns the new subject id. Raises `NotFound`, or `Conflict` when it's the subject's only incident.
  - `async delete_subject(subject_id, actor, client_ip=None, reason: str = "operator") -> None`. Raises `NotFound`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_service_actions.py`:

```python
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from app.db.models import (
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)
from app.subjects.service import Conflict, NotFound

from tests.subjects.factories import T0, face, request, unit, with_similarity


async def _with_review(service):
    v = unit(1)
    req_a, req_b = request(), request()
    res_a = await service.process_request(req_a, [face(v)])
    res_b = await service.process_request(req_b, [face(with_similarity(v, 0.45))])
    async with service.session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
    return req_a, res_a, req_b, res_b, review, v


async def test_merge_folds_new_subject_into_candidate(service, session_factory, vector_store):
    req_a, res_a, req_b, res_b, review, v = await _with_review(service)
    target = await service.merge_review(review.id, actor="alice", client_ip="10.0.0.5")
    assert target == res_a.subject_id
    async with session_factory() as s:
        src = await s.get(Subject, res_b.subject_id)
        dst = await s.get(Subject, res_a.subject_id)
        assert src.status == "merged" and src.merged_into_id == dst.id
        assert dst.incident_count == 2
        inc = await s.get(SubjectIncident, req_b.incident_id)
        assert inc.subject_id == dst.id and inc.match_type == "review_merged"
        decided = await s.get(SubjectMatchReview, review.id)
        assert decided.status == "merged" and decided.decided_by == "alice"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.actor, log.client_ip, log.subject_id) == ("merge", "alice", "10.0.0.5", dst.id)
    hits = vector_store.search(with_similarity(v, 0.45), 20)
    assert {h.subject_id for h in hits} == {res_a.subject_id}


async def test_merge_twice_conflicts(service):
    *_, review, _ = await _with_review(service)
    await service.merge_review(review.id, actor="alice")
    with pytest.raises(Conflict):
        await service.merge_review(review.id, actor="bob")


async def test_merge_when_source_no_longer_active_conflicts(service, session_factory):
    _, _, _, res_b, review, _ = await _with_review(service)
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == res_b.subject_id).values(status="merged"))
        await s.commit()
    with pytest.raises(Conflict):
        await service.merge_review(review.id, actor="alice")
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, review.id)).status == "pending"


async def test_merge_after_candidate_deleted_is_not_found(service):
    _, res_a, _, _, review, _ = await _with_review(service)
    await service.delete_subject(res_a.subject_id, actor="alice")
    with pytest.raises(NotFound):
        await service.merge_review(review.id, actor="alice")


async def test_reject_keeps_both_subjects(service, session_factory):
    _, res_a, _, res_b, review, _ = await _with_review(service)
    await service.reject_review(review.id, actor="alice")
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, review.id)).status == "rejected"
        assert (await s.get(Subject, res_a.subject_id)).status == "active"
        assert (await s.get(Subject, res_b.subject_id)).status == "active"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.subject_id) == ("reject", res_b.subject_id)
    with pytest.raises(Conflict):
        await service.reject_review(review.id, actor="alice")


async def test_detach_moves_incident_to_new_subject(service, session_factory, vector_store):
    v = unit(1)
    req_a, req_c = request(), request(at=T0 + timedelta(days=1))
    res_a = await service.process_request(req_a, [face(v)])
    vc = with_similarity(v, 0.7)
    await service.process_request(req_c, [face(vc)])
    new_id = await service.detach_incident(res_a.subject_id, req_c.incident_id, actor="alice")
    async with session_factory() as s:
        old = await s.get(Subject, res_a.subject_id)
        new = await s.get(Subject, new_id)
        assert (old.incident_count, new.incident_count, new.code) == (1, 1, "S-002")
        inc = await s.get(SubjectIncident, req_c.incident_id)
        assert inc.subject_id == new_id and inc.match_type == "detached"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert log.action == "detach" and log.detail["new_subject_id"] == str(new_id)
    assert vector_store.search(vc, 1)[0].subject_id == new_id


async def test_detach_only_incident_conflicts(service):
    req = request()
    res = await service.process_request(req, [face(unit(1))])
    with pytest.raises(Conflict):
        await service.detach_incident(res.subject_id, req.incident_id, actor="alice")


async def test_detach_wrong_subject_is_not_found(service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    with pytest.raises(NotFound):
        await service.detach_incident(uuid.uuid4(), req.incident_id, actor="alice")


async def test_delete_subject_removes_everything_but_the_log(service, session_factory, vector_store, settings):
    req = request()
    res = await service.process_request(req, [face(unit(1))])
    await service.delete_subject(res.subject_id, actor="alice")
    async with session_factory() as s:
        assert await s.get(Subject, res.subject_id) is None
        assert (await s.execute(select(func.count()).select_from(SubjectFace))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(SubjectIncident))).scalar_one() == 0
        assert (await s.get(FaceIncident, req.incident_id)).status == "deleted"
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.action, log.subject_id, log.detail["reason"]) == ("delete", res.subject_id, "operator")
    assert vector_store.search(unit(1), 20) == []
    assert not (Path(settings.faces_dir) / "subjects" / str(res.subject_id)).exists()


async def test_delete_unknown_subject_is_not_found(service):
    with pytest.raises(NotFound):
        await service.delete_subject(uuid.uuid4(), actor="alice")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_service_actions.py -v`
Expected: FAIL with `AttributeError: 'SubjectService' object has no attribute 'merge_review'` (or `delete_subject`).

- [ ] **Step 3: Implement the actions**

In `backend/app/subjects/service.py`, change the imports to:

```python
from sqlalchemy import delete, func, select, text, update

from app.db.models import (
    FaceIncident,
    Subject,
    SubjectAccessLog,
    SubjectFace,
    SubjectIncident,
    SubjectMatchReview,
)
```

Add these methods to `SubjectService`, after `process_request`:

```python
    # ── operator actions (spec §8) ─────────────────────────────────

    async def log_access(
        self, s, subject_id: uuid.UUID | None, action: str, actor: str,
        client_ip: str | None = None, detail: dict | None = None,
    ) -> None:
        s.add(SubjectAccessLog(
            subject_id=subject_id, action=action, actor=actor, client_ip=client_ip, detail=detail,
        ))

    async def merge_review(self, review_id: uuid.UUID, actor: str, client_ip: str | None = None) -> uuid.UUID:
        async with self.session_factory() as s:
            review = await s.get(SubjectMatchReview, review_id)
            if review is None:
                raise NotFound("review")
            if review.status != "pending":
                raise Conflict(f"review already {review.status}")
            src = await s.get(Subject, review.new_subject_id)
            dst = await s.get(Subject, review.candidate_subject_id)
            if src is None or dst is None or src.status != "active" or dst.status != "active":
                raise Conflict("subjects changed since this review was created")
            moved = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == src.id)
            )).scalars())
            for row in moved:
                row.subject_id = dst.id
                row.qdrant_synced = False
            await s.execute(
                update(SubjectIncident).where(SubjectIncident.subject_id == src.id)
                .values(subject_id=dst.id, match_type="review_merged")
            )
            await self._repoint_reviews(s, old=src.id, new=dst.id, exclude=review.id)
            review.status, review.decided_by, review.decided_at = "merged", actor, utcnow()
            src.status, src.merged_into_id, src.incident_count, src.cover_face_id = "merged", dst.id, 0, None
            await s.flush()
            evicted = await self._enforce_face_cap(s, dst.id)
            await self._recompute(s, dst)
            await self.log_access(s, dst.id, "merge", actor, client_ip, {
                "review_id": str(review.id), "merged_subject_id": str(src.id),
            })
            await s.commit()
            target = dst.id
        evicted_ids = {r.id for r in evicted}
        await self._index_faces([r for r in moved if r.id not in evicted_ids])
        await self._drop_faces(evicted)
        return target

    async def _repoint_reviews(self, s, old: uuid.UUID, new: uuid.UUID, exclude: uuid.UUID) -> None:
        r = SubjectMatchReview
        await s.execute(update(r).where(r.id != exclude, r.new_subject_id == old).values(new_subject_id=new))
        await s.execute(update(r).where(r.id != exclude, r.candidate_subject_id == old).values(candidate_subject_id=new))
        await s.execute(
            update(r).where(r.status == "pending", r.new_subject_id == r.candidate_subject_id).values(status="obsolete")
        )

    async def reject_review(self, review_id: uuid.UUID, actor: str, client_ip: str | None = None) -> None:
        async with self.session_factory() as s:
            review = await s.get(SubjectMatchReview, review_id)
            if review is None:
                raise NotFound("review")
            if review.status != "pending":
                raise Conflict(f"review already {review.status}")
            review.status, review.decided_by, review.decided_at = "rejected", actor, utcnow()
            await self.log_access(s, review.new_subject_id, "reject", actor, client_ip, {
                "review_id": str(review.id), "candidate_subject_id": str(review.candidate_subject_id),
            })
            await s.commit()

    async def detach_incident(
        self, subject_id: uuid.UUID, incident_id: uuid.UUID, actor: str, client_ip: str | None = None,
    ) -> uuid.UUID:
        async with self.session_factory() as s:
            incident = await s.get(SubjectIncident, incident_id)
            if incident is None or incident.subject_id != subject_id:
                raise NotFound("incident")
            old = await s.get(Subject, subject_id)
            count = (await s.execute(
                select(func.count()).select_from(SubjectIncident)
                .where(SubjectIncident.subject_id == subject_id)
            )).scalar_one()
            if count <= 1:
                raise Conflict("this is the subject's only incident")
            new = await self._new_subject(s, incident.detected_at)
            incident.subject_id, incident.match_type = new.id, "detached"
            moved = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == subject_id, SubjectFace.incident_id == incident_id)
            )).scalars())
            for row in moved:
                row.subject_id = new.id
                row.qdrant_synced = False
            await s.execute(
                update(SubjectMatchReview)
                .where(SubjectMatchReview.incident_id == incident_id, SubjectMatchReview.status == "pending")
                .values(status="obsolete")
            )
            await s.flush()
            await self._recompute(s, old)
            await self._recompute(s, new)
            await self.log_access(s, subject_id, "detach", actor, client_ip, {
                "incident_id": str(incident_id), "new_subject_id": str(new.id),
            })
            await s.commit()
            new_id = new.id
        await self._index_faces(moved)
        return new_id

    async def delete_subject(
        self, subject_id: uuid.UUID, actor: str, client_ip: str | None = None, reason: str = "operator",
    ) -> None:
        async with self.session_factory() as s:
            subject = await s.get(Subject, subject_id)
            if subject is None:
                raise NotFound("subject")
            code = subject.code
            faces = list((await s.execute(
                select(SubjectFace).where(SubjectFace.subject_id == subject_id)
            )).scalars())
            incident_ids = list((await s.execute(
                select(SubjectIncident.incident_id).where(SubjectIncident.subject_id == subject_id)
            )).scalars())
            await s.execute(delete(SubjectMatchReview).where(
                (SubjectMatchReview.new_subject_id == subject_id)
                | (SubjectMatchReview.candidate_subject_id == subject_id)
            ))
            await s.execute(delete(SubjectIncident).where(SubjectIncident.subject_id == subject_id))
            if incident_ids:
                await s.execute(
                    update(FaceIncident).where(FaceIncident.incident_id.in_(incident_ids)).values(status="deleted")
                )
            await s.execute(delete(SubjectFace).where(SubjectFace.subject_id == subject_id))
            await s.execute(update(Subject).where(Subject.merged_into_id == subject_id).values(merged_into_id=None))
            await s.delete(subject)
            await self.log_access(s, subject_id, "delete", actor, client_ip, {
                "code": code, "incidents": len(incident_ids), "reason": reason,
            })
            await s.commit()
        await self._drop_faces(faces)
        shutil.rmtree(self._faces_dir / "subjects" / str(subject_id), ignore_errors=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_service_actions.py tests/subjects/test_service_process.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/subjects/service.py backend/tests/subjects/test_service_actions.py
git commit -m "feat(subjects): merge/reject reviews, detach incidents, delete subjects with access log

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Retention sweep and index resync

**Files:**
- Modify: `backend/app/subjects/service.py`
- Test: `backend/tests/subjects/test_service_retention.py`

**Interfaces:**
- Consumes: Tasks 8–9.
- Produces:
  - `service.SweepResult(subjects_deleted: int, tombstones_deleted: int, no_face_deleted: int, incoming_dirs_deleted: int)`.
  - `SubjectService.sweep(now: datetime | None = None) -> SweepResult`.
  - `SubjectService.resync_index(limit: int = 500) -> int`, which returns the number of faces indexed.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_service_retention.py`:

```python
import os
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.db.models import FaceIncident, Subject, SubjectAccessLog, SubjectFace, SubjectMatchReview
from app.subjects.service import SubjectService

from tests.subjects.factories import FlakyStore, T0, face, request, unit, with_similarity


async def test_sweep_deletes_subjects_past_retention(service, session_factory):
    old = await service.process_request(request(at=T0), [face(unit(1))])
    fresh = await service.process_request(request(at=T0 + timedelta(days=80)), [face(unit(2))])
    result = await service.sweep(now=T0 + timedelta(days=95))
    assert result.subjects_deleted == 1
    async with session_factory() as s:
        assert await s.get(Subject, old.subject_id) is None
        assert await s.get(Subject, fresh.subject_id) is not None
        log = (await s.execute(select(SubjectAccessLog))).scalar_one()
        assert (log.actor, log.detail["reason"]) == ("system:retention", "retention")


async def test_sweep_removes_old_incoming_folders(service, settings):
    incoming = Path(settings.faces_dir) / "incoming"
    old_dir, new_dir = incoming / "old", incoming / "new"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_ts = (T0 - timedelta(days=10)).timestamp()
    os.utime(old_dir, (old_ts, old_ts))
    os.utime(new_dir, (T0.timestamp(), T0.timestamp()))
    result = await service.sweep(now=T0)
    assert result.incoming_dirs_deleted == 1
    assert not old_dir.exists() and new_dir.exists()


async def test_sweep_drops_old_no_face_rows(service, session_factory):
    req = request()
    await service.process_request(req, [])
    async with session_factory() as s:
        await s.execute(update(FaceIncident).values(created_at=T0 - timedelta(days=30)))
        await s.commit()
    result = await service.sweep(now=T0)
    assert result.no_face_deleted == 1
    async with session_factory() as s:
        assert await s.get(FaceIncident, req.incident_id) is None


async def test_sweep_drops_old_merged_tombstones(service, session_factory):
    v = unit(1)
    res_a = await service.process_request(request(), [face(v)])
    res_b = await service.process_request(request(), [face(with_similarity(v, 0.45))])
    async with session_factory() as s:
        review = (await s.execute(select(SubjectMatchReview))).scalar_one()
    await service.merge_review(review.id, actor="alice")
    async with session_factory() as s:
        await s.execute(update(Subject).where(Subject.id == res_b.subject_id).values(created_at=T0 - timedelta(days=100)))
        await s.commit()
    result = await service.sweep(now=T0)
    assert result.tombstones_deleted == 1
    async with session_factory() as s:
        assert await s.get(Subject, res_b.subject_id) is None
        assert await s.get(Subject, res_a.subject_id) is not None
        assert await s.get(SubjectMatchReview, review.id) is None


async def test_resync_indexes_unsynced_faces(session_factory, vector_store, settings):
    service = SubjectService(session_factory, FlakyStore(vector_store, fail_upserts=1), settings)
    v = unit(1)
    await service.process_request(request(), [face(v)])
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalar_one() is False
    assert await service.resync_index() == 1
    async with session_factory() as s:
        assert (await s.execute(select(SubjectFace.qdrant_synced))).scalar_one() is True
    assert vector_store.search(v, 1)[0].score > 0.99
    assert await service.resync_index() == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_service_retention.py -v`
Expected: FAIL with `AttributeError: 'SubjectService' object has no attribute 'sweep'`.

- [ ] **Step 3: Implement**

In `backend/app/subjects/service.py`, change `from datetime import datetime, timezone` to `from datetime import datetime, timedelta, timezone`. Add after `ProcessResult`:

```python
@dataclass(frozen=True)
class SweepResult:
    subjects_deleted: int
    tombstones_deleted: int
    no_face_deleted: int
    incoming_dirs_deleted: int
```

Add these methods to `SubjectService`:

```python
    # ── retention and index maintenance (spec §11) ─────────────────

    async def sweep(self, now: datetime | None = None) -> SweepResult:
        now = now or utcnow()
        cutoff = now - timedelta(days=self.settings.retention_days)
        short_cutoff = now - timedelta(days=self.settings.incoming_retention_days)
        async with self.session_factory() as s:
            stale = list((await s.execute(
                select(Subject.id).where(Subject.status == "active", Subject.last_seen_at < cutoff)
            )).scalars())
        for subject_id in stale:
            await self.delete_subject(subject_id, actor="system:retention", reason="retention")
        async with self.session_factory() as s:
            tombstones = list((await s.execute(
                select(Subject.id).where(Subject.status == "merged", Subject.created_at < cutoff)
            )).scalars())
            if tombstones:
                await s.execute(delete(SubjectMatchReview).where(
                    SubjectMatchReview.new_subject_id.in_(tombstones)
                    | SubjectMatchReview.candidate_subject_id.in_(tombstones)
                ))
                await s.execute(update(Subject).where(Subject.merged_into_id.in_(tombstones)).values(merged_into_id=None))
                await s.execute(delete(Subject).where(Subject.id.in_(tombstones)))
            no_face = await s.execute(delete(FaceIncident).where(
                FaceIncident.status.in_(("no_face", "deleted")), FaceIncident.created_at < short_cutoff
            ))
            await s.commit()
        removed_dirs = 0
        incoming = self._faces_dir / "incoming"
        if incoming.is_dir():
            for folder in incoming.iterdir():
                modified = datetime.fromtimestamp(folder.stat().st_mtime, tz=timezone.utc)
                if folder.is_dir() and modified < short_cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
                    removed_dirs += 1
        result = SweepResult(len(stale), len(tombstones), no_face.rowcount or 0, removed_dirs)
        logger.info("Subject retention sweep: %s", result)
        return result

    async def resync_index(self, limit: int = 500) -> int:
        async with self.session_factory() as s:
            rows = list((await s.execute(
                select(SubjectFace).where(SubjectFace.qdrant_synced.is_(False)).limit(limit)
            )).scalars())
        return await self._index_faces(rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects -v`
Expected: all subject tests pass. `test_same_identity_scores_higher_than_different` is skipped, and the integration tests are skipped if no models are present.

- [ ] **Step 5: Commit**

```bash
git add backend/app/subjects/service.py backend/tests/subjects/test_service_retention.py
git commit -m "feat(subjects): retention sweep and Qdrant resync

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 11: subject-worker (Kafka consumer + service entrypoint)

**Files:**
- Create: `backend/app/subjects/worker.py`
- Create: `backend/services/subjects/__init__.py` (empty)
- Create: `backend/services/subjects/main.py`
- Test: `backend/tests/subjects/test_worker.py`

**Interfaces:**
- Consumes:
  - `FaceRequest.from_payload` (Task 6);
  - `SubjectService.process_request`, `.resync_index`, `.sweep` (Tasks 8–10);
  - `TOPIC_FACES_REQUESTS`, `GROUP_SUBJECT_WORKER` (Task 4);
  - `FaceAnalyzer`, `ScrfdDetector`, `ArcFaceEmbedder` (Task 6);
  - `FaceVectorStore` (Task 7).
- Produces:
  - `SubjectWorker(bootstrap_servers, service, analyzer, settings, consumer_factory=AIOKafkaConsumer, resync_every_s=600.0, sweep_every_s=86400.0)`, with methods:
    - `async handle(payload) -> ProcessResult`
    - `async handle_with_retry(payload, attempts=3, backoff_s=1.0) -> ProcessResult | None`
    - `async start()`
    - `async stop()`
  - `python -m services.subjects.main` as the container command.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_worker.py`:

```python
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import SubjectIncident
from app.subjects.worker import SubjectWorker

from tests.subjects.factories import T0, face, unit


class FakeAnalyzer:
    def __init__(self, faces=None, fail_times: int = 0):
        self.faces = faces or []
        self.fail_times = fail_times
        self.calls: list[list[str]] = []

    def __call__(self, paths):
        self.calls.append(list(paths))
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("model crashed")
        return list(self.faces) if paths else []


class FakeMsg:
    def __init__(self, value):
        self.value = value


class FakeConsumer:
    def __init__(self, values):
        self._values = values
        self.commits = 0

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for v in self._values:
            yield FakeMsg(v)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def make_worker(service, settings):
    def _make(analyzer):
        return SubjectWorker("kafka:9092", service, analyzer, settings)
    return _make


def _payload(settings, incident_id, n_crops=1, crops=None):
    folder = Path(settings.faces_dir) / "incoming" / str(incident_id)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n_crops):
        p = folder / f"{i:02d}.jpg"
        p.write_bytes(b"x")
        paths.append(str(p))
    return {
        "incident_id": str(incident_id), "camera_id": "cam-01", "detected_at": T0.timestamp(),
        "person_track_id": 7, "crops": paths if crops is None else crops,
    }


async def test_handle_runs_analyzer_on_own_crops_and_matches(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))])
    worker = make_worker(analyzer)
    iid = uuid.uuid4()
    payload = _payload(settings, iid, n_crops=2)
    result = await worker.handle(payload)
    assert (result.status, result.subject_code) == ("matched", "S-001")
    assert analyzer.calls == [[str(Path(p).resolve()) for p in payload["crops"]]]
    assert not (Path(settings.faces_dir) / "incoming" / str(iid)).exists()


async def test_handle_ignores_traversal_paths(make_worker, settings, tmp_path):
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"do not touch")
    analyzer = FakeAnalyzer([face(unit(1))])
    worker = make_worker(analyzer)
    iid = uuid.uuid4()
    sneaky = Path(settings.faces_dir) / "incoming" / str(iid) / ".." / ".." / ".." / "secret.jpg"
    result = await worker.handle(_payload(settings, iid, n_crops=0, crops=[str(secret), str(sneaky)]))
    assert analyzer.calls == [[]]
    assert result.status == "no_face"
    assert secret.read_bytes() == b"do not touch"


async def test_handle_with_retry_skips_malformed(make_worker):
    worker = make_worker(FakeAnalyzer())
    assert await worker.handle_with_retry({"camera_id": "cam-01"}) is None
    assert await worker.handle_with_retry(None) is None
    assert await worker.handle_with_retry({"incident_id": "nope"}) is None


async def test_handle_with_retry_retries_transient_errors(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))], fail_times=2)
    worker = make_worker(analyzer)
    result = await worker.handle_with_retry(_payload(settings, uuid.uuid4()), backoff_s=0)
    assert result.status == "matched"
    assert len(analyzer.calls) == 3


async def test_handle_with_retry_gives_up_after_attempts(make_worker, settings):
    analyzer = FakeAnalyzer([face(unit(1))], fail_times=5)
    worker = make_worker(analyzer)
    assert await worker.handle_with_retry(_payload(settings, uuid.uuid4()), attempts=3, backoff_s=0) is None
    assert len(analyzer.calls) == 3


async def test_consume_loop_commits_after_each_message(make_worker, settings, session_factory):
    worker = make_worker(FakeAnalyzer([face(unit(1))]))
    payload = _payload(settings, uuid.uuid4())
    worker._consumer = FakeConsumer([payload, dict(payload), {"bad": 1}])  # redelivery + malformed
    await worker._consume_loop()
    assert worker._consumer.commits == 3
    async with session_factory() as s:
        assert (await s.execute(select(func.count()).select_from(SubjectIncident))).scalar_one() == 1


def test_service_entrypoint_imports():
    from services.subjects import main as entry

    assert callable(entry.main)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_worker.py -v`
Expected: `ModuleNotFoundError: No module named 'app.subjects.worker'`.

- [ ] **Step 3: Implement the worker**

`backend/app/subjects/worker.py`:

```python
"""Kafka worker: wastewatch.faces.requests -> FaceAnalyzer -> SubjectService.

Offsets are committed manually after each message. Redelivery is harmless
(face_incidents makes processing idempotent); malformed messages are
logged and skipped; transient failures are retried a few times, then
skipped so one poison message cannot stall the queue.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import orjson
from aiokafka import AIOKafkaConsumer

from app.config.kafka import GROUP_SUBJECT_WORKER, TOPIC_FACES_REQUESTS
from app.subjects.config import SubjectSettings
from app.subjects.service import ProcessResult, SubjectService
from app.subjects.types import FaceRequest

logger = logging.getLogger(__name__)


def _loads(raw: bytes):
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None


class SubjectWorker:
    def __init__(
        self,
        bootstrap_servers: str,
        service: SubjectService,
        analyzer,
        settings: SubjectSettings,
        consumer_factory=AIOKafkaConsumer,
        resync_every_s: float = 600.0,
        sweep_every_s: float = 86400.0,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._service = service
        self._analyzer = analyzer
        self._settings = settings
        self._consumer_factory = consumer_factory
        self._resync_every_s = resync_every_s
        self._sweep_every_s = sweep_every_s
        self._consumer = None
        self._tasks: list[asyncio.Task] = []

    async def handle(self, payload: dict) -> ProcessResult:
        req = FaceRequest.from_payload(payload, Path(self._settings.faces_dir) / "incoming")
        faces = await asyncio.to_thread(self._analyzer, list(req.crops))
        result = await self._service.process_request(req, faces)
        logger.info(
            "Face incident %s cam=%s -> %s %s (%s, sim=%s, faces=%d)",
            req.incident_id, req.camera_id, result.status, result.subject_code,
            result.match_type, None if result.similarity is None else round(result.similarity, 3), len(faces),
        )
        return result

    async def handle_with_retry(self, payload, attempts: int = 3, backoff_s: float = 1.0) -> ProcessResult | None:
        for attempt in range(1, attempts + 1):
            try:
                return await self.handle(payload)
            except (KeyError, ValueError, TypeError):
                logger.exception("Malformed face request skipped: %s", str(payload)[:200])
                return None
            except Exception:
                logger.exception("Face request failed (attempt %d/%d)", attempt, attempts)
                if attempt < attempts:
                    await asyncio.sleep(backoff_s * attempt)
        return None

    async def start(self) -> None:
        self._consumer = self._consumer_factory(
            TOPIC_FACES_REQUESTS,
            bootstrap_servers=self._bootstrap_servers,
            group_id=GROUP_SUBJECT_WORKER,
            value_deserializer=_loads,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self._tasks = [
            asyncio.create_task(self._consume_loop()),
            asyncio.create_task(self._maintenance_loop()),
        ]
        logger.info("SubjectWorker consuming %s", TOPIC_FACES_REQUESTS)

    async def _consume_loop(self) -> None:
        async for msg in self._consumer:
            await self.handle_with_retry(msg.value)
            await self._consumer.commit()

    async def _maintenance_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_sweep: float | None = None
        while True:
            try:
                await self._service.resync_index()
                if last_sweep is None or loop.time() - last_sweep >= self._sweep_every_s:
                    await self._service.sweep()
                    last_sweep = loop.time()
            except Exception:
                logger.exception("Subject maintenance tick failed")
            await asyncio.sleep(self._resync_every_s)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._consumer is not None:
            await self._consumer.stop()
```

- [ ] **Step 4: Implement the entrypoint**

`backend/services/subjects/__init__.py`: empty file.

`backend/services/subjects/main.py`:

```python
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Subject Worker -- violation-time face matching (CPU).

Consumes: wastewatch.faces.requests
See docs/superpowers/specs/2026-09-29-face-subject-id-design.md.
"""
import asyncio
import logging
import signal

from qdrant_client import QdrantClient

from app.config.settings import settings as app_settings
from app.subjects.analyzer import FaceAnalyzer
from app.subjects.config import SubjectSettings
from app.subjects.detector import ScrfdDetector
from app.subjects.embedder import ArcFaceEmbedder
from app.subjects.service import SubjectService
from app.subjects.vector_store import FaceVectorStore
from app.subjects.worker import SubjectWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.subject-worker")


async def main() -> None:
    from app.db.engine import async_session  # lazy: engine needs WW_DATABASE_URL

    settings = SubjectSettings.from_env()
    logger.info(
        "Starting subject worker (auto>=%.2f review>=%.2f, retention %dd)",
        settings.match_auto, settings.match_review, settings.retention_days,
    )
    store = FaceVectorStore(QdrantClient(url=settings.qdrant_url), settings.collection)
    await asyncio.to_thread(store.ensure_collection)
    analyzer = FaceAnalyzer(ScrfdDetector(settings.det_model), ArcFaceEmbedder(settings.rec_model), settings)
    service = SubjectService(async_session, store, settings)
    worker = SubjectWorker(app_settings.kafka_bootstrap_servers, service, analyzer, settings)
    await worker.start()

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown.set)
    logger.info("Subject worker ready")
    try:
        await shutdown.wait()
    finally:
        await worker.stop()
        logger.info("Subject worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_worker.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/subjects/worker.py backend/services/subjects backend/tests/subjects/test_worker.py
git commit -m "feat(subjects): subject-worker consuming wastewatch.faces.requests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Alerts carry incident_id; GET /api/alerts returns subject badge fields

**Files:**
- Modify: `backend/app/services/alert_consumer.py` (`_handle_alert`, ~L119-250)
- Create: `backend/app/subjects/queries.py`
- Modify: `backend/app/schemas/alert.py`
- Modify: `backend/app/api/routes/alerts.py` (`list_alerts`)
- Test: `backend/tests/subjects/test_alert_badges.py`

**Interfaces:**
- Consumes: the models (Task 4); `SubjectService` fixtures (Task 8).
- Produces:
  - `queries.AlertBadge(face_status: str | None, subject_id: uuid.UUID | None, subject_code: str | None, subject_incident_count: int | None, subject_review_pending: bool)`.
  - `queries.alert_badges(session, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, AlertBadge]`. Incidents with no `face_incidents` row, i.e. still processing, are absent from the dict.
  - `AlertResponse` gains `incident_id`, `face_status`, `subject_id`, `subject_code`, `subject_incident_count` (all optional, default `None`) and `subject_review_pending: bool = False`.
  - `GET /api/alerts` items carry those fields; the frontend (Task 14) reads them.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_alert_badges.py`:

```python
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models import Alert, AlertSeverity, AlertStatus
from app.subjects.queries import alert_badges

from tests.subjects.factories import face, request, unit, with_similarity


async def test_badges_for_each_state(service, session_factory):
    v = unit(1)
    pending, no_face = request(), request()
    first, repeat, uncertain = request(), request(), request()
    await service.process_request(no_face, [])
    await service.process_request(first, [face(v)])
    await service.process_request(repeat, [face(with_similarity(v, 0.8))])
    # own seed: sharing seed 999 with the 0.8 face would make it ~0.9 similar to that face
    await service.process_request(uncertain, [face(with_similarity(v, 0.45, seed=7))])
    ids = [r.incident_id for r in (pending, no_face, first, repeat, uncertain)]
    async with session_factory() as s:
        badges = await alert_badges(s, ids)
    assert pending.incident_id not in badges
    assert badges[no_face.incident_id].face_status == "no_face"
    assert badges[no_face.incident_id].subject_code is None
    assert (badges[first.incident_id].subject_code, badges[first.incident_id].subject_incident_count) == ("S-001", 2)
    assert badges[repeat.incident_id].subject_code == "S-001"
    assert badges[first.incident_id].subject_review_pending is False
    assert badges[uncertain.incident_id].subject_code == "S-002"
    assert badges[uncertain.incident_id].subject_review_pending is True


async def test_badges_empty_input(session_factory):
    async with session_factory() as s:
        assert await alert_badges(s, []) == {}


@pytest.fixture
async def alerts_client(app, session_factory):
    from app.api.deps import get_db

    async def override():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_list_alerts_includes_subject_badge(alerts_client, session_factory, service):
    req = request()
    await service.process_request(req, [face(unit(1))])
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="Waste dumping confirmed",
                    status=AlertStatus.active, incident_id=req.incident_id))
        s.add(Alert(severity=AlertSeverity.warning, title="Older alert", status=AlertStatus.active))
        await s.commit()
    resp = await alerts_client.get("/api/alerts")
    assert resp.status_code == 200
    by_title = {a["title"]: a for a in resp.json()}
    a = by_title["Waste dumping confirmed"]
    assert a["incident_id"] == str(req.incident_id)
    assert (a["face_status"], a["subject_code"], a["subject_incident_count"], a["subject_review_pending"]) == (
        "matched", "S-001", 1, False,
    )
    old = by_title["Older alert"]
    assert old["face_status"] is None and old["subject_code"] is None


async def test_alert_consumer_stores_incident_id(monkeypatch):
    from app.services import alert_consumer as ac

    captured: dict = {}

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            captured["params"] = stmt.compile().params

        async def commit(self):
            pass

    broadcasts: list[dict] = []

    async def fake_broadcast(payload):
        broadcasts.append(payload)

    async def fake_uuid(name):
        return "11111111-1111-1111-1111-111111111111"

    async def fake_info(name):
        return None

    monkeypatch.setattr(ac, "async_session", lambda: FakeSession())
    monkeypatch.setattr(ac, "broadcast_alert", fake_broadcast)
    consumer = ac.AlertConsumer("kafka:9092")
    monkeypatch.setattr(consumer, "_resolve_camera_uuid", fake_uuid)
    monkeypatch.setattr(consumer, "_resolve_camera_info", fake_info)

    iid = str(uuid.uuid4())
    await consumer._handle_alert({
        "camera_id": "cam-01", "severity": "critical", "title": "t", "message": "m",
        "suppress_tts": True, "incident_id": iid, "detected_at": 1790000000.0,
    })
    assert str(captured["params"]["incident_id"]) == iid
    assert broadcasts[0]["incident_id"] == iid

    await consumer._handle_alert({
        "camera_id": "cam-01", "severity": "critical", "title": "t", "message": "m",
        "suppress_tts": True, "incident_id": "garbage",
    })
    assert captured["params"]["incident_id"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_alert_badges.py -v`
Expected: `ModuleNotFoundError: No module named 'app.subjects.queries'`.

- [ ] **Step 3: Implement `queries.py` (badge part)**

`backend/app/subjects/queries.py`:

```python
"""Read-side queries for subject data (alert badges, subject pages, reviews)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.db.models import FaceIncident, Subject, SubjectIncident, SubjectMatchReview, subject_code


@dataclass(frozen=True)
class AlertBadge:
    face_status: str | None
    subject_id: uuid.UUID | None
    subject_code: str | None
    subject_incident_count: int | None
    subject_review_pending: bool


async def alert_badges(session, incident_ids: list[uuid.UUID]) -> dict[uuid.UUID, AlertBadge]:
    """Badge data per incident. Incidents still being processed are absent."""
    if not incident_ids:
        return {}
    review_pending = (
        select(SubjectMatchReview.id)
        .where(SubjectMatchReview.new_subject_id == Subject.id, SubjectMatchReview.status == "pending")
        .exists()
    )
    rows = (await session.execute(
        select(
            FaceIncident.incident_id, FaceIncident.status,
            Subject.id, Subject.code_num, Subject.incident_count, review_pending,
        )
        .outerjoin(SubjectIncident, SubjectIncident.incident_id == FaceIncident.incident_id)
        .outerjoin(Subject, Subject.id == SubjectIncident.subject_id)
        .where(FaceIncident.incident_id.in_(incident_ids))
    )).all()
    return {
        incident_id: AlertBadge(
            face_status=status,
            subject_id=subject_id,
            subject_code=subject_code(code_num) if code_num is not None else None,
            subject_incident_count=count,
            subject_review_pending=bool(pending) if subject_id is not None else False,
        )
        for incident_id, status, subject_id, code_num, count, pending in rows
    }
```

- [ ] **Step 4: Extend the alert schema and route**

In `backend/app/schemas/alert.py`, add to `AlertResponse`, before `model_config`:

```python
    incident_id: uuid.UUID | None = None
    # Face subject badge (docs/superpowers/specs/2026-09-29-face-subject-id-design.md D6).
    face_status: str | None = None  # None = pending/none, "matched", "no_face", "deleted"
    subject_id: uuid.UUID | None = None
    subject_code: str | None = None
    subject_incident_count: int | None = None
    subject_review_pending: bool = False
```

In `backend/app/api/routes/alerts.py`, add the imports `from dataclasses import asdict` and `from app.subjects.queries import alert_badges`. Replace the last two lines of `list_alerts` (`result = await db.execute(stmt)` / `return result.scalars().all()`) with:

```python
    result = await db.execute(stmt)
    alerts = result.scalars().all()
    badges = await alert_badges(db, [a.incident_id for a in alerts if a.incident_id is not None])
    out = []
    for alert in alerts:
        item = AlertResponse.model_validate(alert)
        badge = badges.get(alert.incident_id) if alert.incident_id is not None else None
        if badge is not None:
            item = item.model_copy(update=asdict(badge))
        out.append(item)
    return out
```

- [ ] **Step 5: Store `incident_id` in `AlertConsumer`**

In `backend/app/services/alert_consumer.py`, add a module-level helper after the `TTS_DIR` constant:

```python
def _parse_uuid(value) -> str | None:
    """Pipeline-minted incident_id, or None if absent/invalid."""
    if not value:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None
```

In `_handle_alert`, after `_detected_at = payload.get("detected_at")`, add:

```python
        incident_id = _parse_uuid(payload.get("incident_id"))
```

In the `insert(tbl).values(...)` call, add `incident_id=incident_id,` after `snapshot_url=snapshot_url,`. In the `ws_payload` dict, add `"incident_id": incident_id,` after `"snapshot_url": snapshot_url,`.

- [ ] **Step 6: Run the tests to verify they pass, plus the existing alert route tests**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_alert_badges.py tests/test_routes_with_db.py -v`
Expected: the 4 new tests pass. `test_routes_with_db.py` has the same pass/fail set as the Task 1 baseline, and `test_list_alerts_empty` still passes.

- [ ] **Step 7: Commit**

```bash
git add backend/app/subjects/queries.py backend/app/schemas/alert.py backend/app/api/routes/alerts.py backend/app/services/alert_consumer.py backend/tests/subjects/test_alert_badges.py
git commit -m "feat(alerts): store incident_id and return subject badge fields on GET /api/alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: /api/subjects routes with access logging

**Files:**
- Modify: `backend/app/subjects/queries.py` (add subject/review queries)
- Create: `backend/app/schemas/subject.py`
- Create: `backend/app/api/routes/subjects.py`
- Modify: `backend/app/main.py` (register router)
- Test: `backend/tests/subjects/test_routes.py`

**Interfaces:**
- Consumes:
  - `SubjectService` and its methods (Tasks 8–10), `NotFound`, `Conflict`;
  - `require_admin` from `app.auth.middleware`;
  - `SubjectSettings.from_env`, `FaceVectorStore`, `QdrantClient`.
- Produces HTTP endpoints. The frontend in Task 15 uses them:

  | Method | Path | Response |
  |---|---|---|
  | GET | `/api/subjects?repeat_only=&sort=incidents\|last_seen&limit=&offset=` | `[SubjectSummary]` |
  | GET | `/api/subjects/reviews?status=pending` | `[ReviewOut]` |
  | GET | `/api/subjects/reviews/count` | `{pending: int}` (not access-logged, because it carries no personal data; the sidebar polls it) |
  | GET | `/api/subjects/faces/{face_id}` | `image/jpeg` |
  | GET | `/api/subjects/{subject_id}` | `SubjectDetail` |
  | POST | `/api/subjects/reviews/{review_id}/merge` | `ActionResult` |
  | POST | `/api/subjects/reviews/{review_id}/reject` | `ActionResult` |
  | POST | `/api/subjects/{subject_id}/incidents/{incident_id}/detach` | `ActionResult` |
  | DELETE | `/api/subjects/{subject_id}` | `ActionResult` |

- JSON shapes:
  - `SubjectSummary = {id, code, incident_count, first_seen_at, last_seen_at, cover_face_id, camera_ids: [str]}`
  - `SubjectDetail = SubjectSummary + {status, faces: [{id, incident_id, quality, created_at}], incidents: [{incident_id, camera_id, detected_at, match_type, similarity, snapshot_url, zone, title}]}`
  - `ReviewOut = {id, incident_id, similarity, status, created_at, new_subject: {subject_id, code, face_ids}, candidate: {subject_id, code, face_ids}}`
  - `ActionResult = {ok: true, subject_id: uuid | null}`
- Errors: `NotFound` → 404, `Conflict` → 409, no auth → 401.
- Dependency `get_subject_service()`, which tests override.

- [ ] **Step 1: Write the failing tests**

`backend/tests/subjects/test_routes.py`:

```python
import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import Alert, AlertSeverity, AlertStatus, SubjectAccessLog, SubjectMatchReview

from tests.subjects.factories import T0, face, request, unit, with_similarity


@pytest.fixture
async def subjects_client(app, service):
    from app.api.routes.subjects import get_subject_service

    app.dependency_overrides[get_subject_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _repeat_subject(service, session_factory):
    v = unit(1)
    req1 = request(camera_id="cam-01", at=T0)
    req2 = request(camera_id="cam-02", at=T0 + timedelta(days=2))
    res1 = await service.process_request(req1, [face(v)])
    await service.process_request(req2, [face(with_similarity(v, 0.8))])
    async with session_factory() as s:
        s.add(Alert(severity=AlertSeverity.critical, title="Waste dumping confirmed", zone="Zone A",
                    status=AlertStatus.active, incident_id=req1.incident_id,
                    snapshot_url="/evidence/alerts/cam-01/a.jpg"))
        await s.commit()
    return res1.subject_id, req1, req2


async def test_list_empty(subjects_client):
    resp = await subjects_client.get("/api/subjects")
    assert resp.status_code == 200 and resp.json() == []


async def test_list_and_detail(subjects_client, service, session_factory):
    subject_id, req1, _ = await _repeat_subject(service, session_factory)
    [item] = (await subjects_client.get("/api/subjects")).json()
    assert (item["code"], item["incident_count"], item["camera_ids"]) == ("S-001", 2, ["cam-01", "cam-02"])
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    assert len(detail["faces"]) == 2 and len(detail["incidents"]) == 2
    first = next(i for i in detail["incidents"] if i["incident_id"] == str(req1.incident_id))
    assert (first["snapshot_url"], first["zone"], first["match_type"]) == (
        "/evidence/alerts/cam-01/a.jpg", "Zone A", "new",
    )


async def test_repeat_only_filter(subjects_client, service, session_factory):
    await _repeat_subject(service, session_factory)
    await service.process_request(request(), [face(unit(7))])
    codes = [s["code"] for s in (await subjects_client.get("/api/subjects?repeat_only=true")).json()]
    assert codes == ["S-001"]


async def test_face_image_served_privately(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    resp = await subjects_client.get(f"/api/subjects/faces/{detail['faces'][0]['id']}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.headers["cache-control"] == "no-store"
    assert resp.content.startswith(b"\xff\xd8")
    assert (await subjects_client.get(f"/api/subjects/faces/{uuid.uuid4()}")).status_code == 404


async def test_unknown_subject_is_404(subjects_client):
    assert (await subjects_client.get(f"/api/subjects/{uuid.uuid4()}")).status_code == 404


async def test_merge_route_conflict_is_409(subjects_client, service, session_factory):
    v = unit(1)
    a = await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    [review] = (await subjects_client.get("/api/subjects/reviews")).json()
    assert review["candidate"]["code"] == "S-001" and review["new_subject"]["code"] == "S-002"
    assert len(review["new_subject"]["face_ids"]) == 1
    ok = await subjects_client.post(f"/api/subjects/reviews/{review['id']}/merge")
    assert ok.status_code == 200 and ok.json()["subject_id"] == str(a.subject_id)
    again = await subjects_client.post(f"/api/subjects/reviews/{review['id']}/merge")
    assert again.status_code == 409
    assert (await subjects_client.get("/api/subjects/reviews")).json() == []


async def test_review_count_is_not_logged(subjects_client, service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    resp = await subjects_client.get("/api/subjects/reviews/count")
    assert resp.status_code == 200 and resp.json() == {"pending": 1}
    async with session_factory() as s:
        assert (await s.execute(select(SubjectAccessLog))).scalars().all() == []


async def test_reject_route(subjects_client, service, session_factory):
    v = unit(1)
    await service.process_request(request(), [face(v)])
    await service.process_request(request(), [face(with_similarity(v, 0.45))])
    [review] = (await subjects_client.get("/api/subjects/reviews")).json()
    assert (await subjects_client.post(f"/api/subjects/reviews/{review['id']}/reject")).status_code == 200
    async with session_factory() as s:
        assert (await s.get(SubjectMatchReview, uuid.UUID(review["id"]))).status == "rejected"


async def test_detach_route(subjects_client, service, session_factory):
    subject_id, _, req2 = await _repeat_subject(service, session_factory)
    resp = await subjects_client.post(f"/api/subjects/{subject_id}/incidents/{req2.incident_id}/detach")
    assert resp.status_code == 200
    new_id = resp.json()["subject_id"]
    assert (await subjects_client.get(f"/api/subjects/{new_id}")).json()["code"] == "S-002"
    only = await subjects_client.post(f"/api/subjects/{new_id}/incidents/{req2.incident_id}/detach")
    assert only.status_code == 409


async def test_delete_route(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    assert (await subjects_client.delete(f"/api/subjects/{subject_id}")).status_code == 200
    assert (await subjects_client.get(f"/api/subjects/{subject_id}")).status_code == 404
    assert (await subjects_client.delete(f"/api/subjects/{subject_id}")).status_code == 404


async def test_access_log_records_reads(subjects_client, service, session_factory):
    subject_id, _, _ = await _repeat_subject(service, session_factory)
    await subjects_client.get("/api/subjects")
    detail = (await subjects_client.get(f"/api/subjects/{subject_id}")).json()
    await subjects_client.get(f"/api/subjects/faces/{detail['faces'][0]['id']}")
    async with session_factory() as s:
        rows = (await s.execute(select(SubjectAccessLog))).scalars().all()
    assert {r.action for r in rows} == {"list", "view", "face_view"}
    assert {r.actor for r in rows} == {"dev"}


async def test_requires_admin_without_dev_mode(subjects_client, monkeypatch):
    monkeypatch.delenv("WW_AUTH_DEV_MODE", raising=False)
    assert (await subjects_client.get("/api/subjects")).status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects/test_routes.py -v`
Expected: `ModuleNotFoundError: No module named 'app.api.routes.subjects'`.

- [ ] **Step 3: Add the read queries**

Append to `backend/app/subjects/queries.py`, and extend its model import to `from app.db.models import Alert, FaceIncident, Subject, SubjectFace, SubjectIncident, SubjectMatchReview, subject_code`:

```python
def _summary(subject: Subject, camera_ids: list[str]) -> dict:
    return {
        "id": subject.id,
        "code": subject.code,
        "incident_count": subject.incident_count,
        "first_seen_at": subject.first_seen_at,
        "last_seen_at": subject.last_seen_at,
        "cover_face_id": subject.cover_face_id,
        "camera_ids": camera_ids,
    }


async def _camera_ids(session, subject_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not subject_ids:
        return {}
    rows = (await session.execute(
        select(SubjectIncident.subject_id, SubjectIncident.camera_id)
        .where(SubjectIncident.subject_id.in_(subject_ids)).distinct()
    )).all()
    out: dict[uuid.UUID, list[str]] = {}
    for subject_id, camera_id in rows:
        out.setdefault(subject_id, []).append(camera_id)
    return {k: sorted(v) for k, v in out.items()}


async def list_subjects(
    session, repeat_only: bool = False, sort: str = "incidents", limit: int = 50, offset: int = 0,
) -> list[dict]:
    stmt = select(Subject).where(Subject.status == "active")
    if repeat_only:
        stmt = stmt.where(Subject.incident_count >= 2)
    if sort == "incidents":
        stmt = stmt.order_by(Subject.incident_count.desc(), Subject.last_seen_at.desc(), Subject.code_num)
    else:
        stmt = stmt.order_by(Subject.last_seen_at.desc(), Subject.code_num)
    subjects = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    cameras = await _camera_ids(session, [s.id for s in subjects])
    return [_summary(s, cameras.get(s.id, [])) for s in subjects]


async def subject_detail(session, subject_id: uuid.UUID) -> dict | None:
    subject = await session.get(Subject, subject_id)
    if subject is None or subject.status != "active":
        return None
    faces = (await session.execute(
        select(SubjectFace).where(SubjectFace.subject_id == subject_id).order_by(SubjectFace.quality.desc())
    )).scalars().all()
    incidents = (await session.execute(
        select(SubjectIncident, Alert.snapshot_url, Alert.zone, Alert.title)
        .outerjoin(Alert, Alert.incident_id == SubjectIncident.incident_id)
        .where(SubjectIncident.subject_id == subject_id)
        .order_by(SubjectIncident.detected_at.desc())
    )).all()
    cameras = await _camera_ids(session, [subject_id])
    return {
        **_summary(subject, cameras.get(subject_id, [])),
        "status": subject.status,
        "faces": [
            {"id": f.id, "incident_id": f.incident_id, "quality": f.quality, "created_at": f.created_at}
            for f in faces
        ],
        "incidents": [
            {
                "incident_id": inc.incident_id, "camera_id": inc.camera_id, "detected_at": inc.detected_at,
                "match_type": inc.match_type, "similarity": inc.similarity,
                "snapshot_url": snapshot_url, "zone": zone, "title": title,
            }
            for inc, snapshot_url, zone, title in incidents
        ],
    }


async def _review_side(session, subject_id: uuid.UUID) -> dict | None:
    subject = await session.get(Subject, subject_id)
    if subject is None:
        return None
    face_ids = list((await session.execute(
        select(SubjectFace.id).where(SubjectFace.subject_id == subject_id)
        .order_by(SubjectFace.quality.desc()).limit(3)
    )).scalars())
    return {"subject_id": subject.id, "code": subject.code, "face_ids": face_ids}


async def pending_review_count(session) -> int:
    from sqlalchemy import func

    return int((await session.execute(
        select(func.count()).select_from(SubjectMatchReview).where(SubjectMatchReview.status == "pending")
    )).scalar_one())


async def list_reviews(session, status: str = "pending") -> list[dict]:
    reviews = (await session.execute(
        select(SubjectMatchReview).where(SubjectMatchReview.status == status)
        .order_by(SubjectMatchReview.created_at.desc())
    )).scalars().all()
    out = []
    for review in reviews:
        new_side = await _review_side(session, review.new_subject_id)
        candidate = await _review_side(session, review.candidate_subject_id)
        if new_side is None or candidate is None:
            continue
        out.append({
            "id": review.id, "incident_id": review.incident_id, "similarity": review.similarity,
            "status": review.status, "created_at": review.created_at,
            "new_subject": new_side, "candidate": candidate,
        })
    return out
```

- [ ] **Step 4: Add the schemas**

`backend/app/schemas/subject.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class SubjectSummary(BaseModel):
    id: uuid.UUID
    code: str
    incident_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    cover_face_id: uuid.UUID | None
    camera_ids: list[str]


class SubjectFaceOut(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    quality: float
    created_at: datetime


class SubjectIncidentOut(BaseModel):
    incident_id: uuid.UUID
    camera_id: str
    detected_at: datetime
    match_type: str
    similarity: float | None
    snapshot_url: str | None
    zone: str | None
    title: str | None


class SubjectDetail(SubjectSummary):
    status: str
    faces: list[SubjectFaceOut]
    incidents: list[SubjectIncidentOut]


class ReviewSide(BaseModel):
    subject_id: uuid.UUID
    code: str
    face_ids: list[uuid.UUID]


class ReviewOut(BaseModel):
    id: uuid.UUID
    incident_id: uuid.UUID
    similarity: float
    status: str
    created_at: datetime
    new_subject: ReviewSide
    candidate: ReviewSide


class ActionResult(BaseModel):
    ok: bool = True
    subject_id: uuid.UUID | None = None
```

- [ ] **Step 5: Add the router**

`backend/app/api/routes/subjects.py`:

```python
"""Anonymous subject IDs from violation-time faces (admin only, access-logged).

See docs/superpowers/specs/2026-09-29-face-subject-id-design.md §9, §11.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.auth.middleware import require_admin
from app.schemas.subject import ActionResult, ReviewOut, SubjectDetail, SubjectSummary
from app.subjects.config import SubjectSettings
from app.subjects.queries import list_reviews, list_subjects, pending_review_count, subject_detail
from app.subjects.service import Conflict, NotFound, SubjectService

router = APIRouter(prefix="/subjects", tags=["subjects"])

_service: SubjectService | None = None


def get_subject_service() -> SubjectService:
    global _service
    if _service is None:
        from qdrant_client import QdrantClient

        from app.db.engine import async_session
        from app.subjects.vector_store import FaceVectorStore

        settings = SubjectSettings.from_env()
        store = FaceVectorStore(QdrantClient(url=settings.qdrant_url), settings.collection)
        _service = SubjectService(async_session, store, settings)
    return _service


def _actor(user: dict) -> str:
    return str(user.get("preferred_username") or user.get("sub") or "unknown")


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _log(service: SubjectService, subject_id, action: str, user: dict, request: Request, detail=None) -> None:
    async with service.session_factory() as s:
        await service.log_access(s, subject_id, action, _actor(user), _ip(request), detail)
        await s.commit()


@router.get("", response_model=list[SubjectSummary])
async def get_subjects(
    request: Request,
    repeat_only: bool = False,
    sort: Literal["incidents", "last_seen"] = "incidents",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        items = await list_subjects(s, repeat_only, sort, limit, offset)
    await _log(service, None, "list", user, request, {"count": len(items), "repeat_only": repeat_only})
    return items


@router.get("/reviews/count")
async def get_review_count(
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
) -> dict:
    """Pending-review count for the sidebar badge. No personal data, so not access-logged."""
    async with service.session_factory() as s:
        return {"pending": await pending_review_count(s)}


@router.get("/reviews", response_model=list[ReviewOut])
async def get_reviews(
    request: Request,
    status: Literal["pending", "merged", "rejected", "obsolete"] = "pending",
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        items = await list_reviews(s, status)
    await _log(service, None, "list_reviews", user, request, {"count": len(items), "status": status})
    return items


@router.get("/faces/{face_id}")
async def get_face_image(
    face_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    from app.db.models import SubjectFace

    async with service.session_factory() as s:
        face = await s.get(SubjectFace, face_id)
    if face is None:
        raise HTTPException(status_code=404, detail="Face not found")
    path = Path(face.crop_path).resolve()
    allowed = (Path(service.settings.faces_dir) / "subjects").resolve()
    if not path.is_relative_to(allowed) or not path.is_file():
        raise HTTPException(status_code=404, detail="Face not found")
    await _log(service, face.subject_id, "face_view", user, request, {"face_id": str(face_id)})
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/{subject_id}", response_model=SubjectDetail)
async def get_subject(
    subject_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    async with service.session_factory() as s:
        detail = await subject_detail(s, subject_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Subject not found")
    await _log(service, subject_id, "view", user, request)
    return detail


@router.post("/reviews/{review_id}/merge", response_model=ActionResult)
async def merge_review(
    review_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        target = await service.merge_review(review_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult(subject_id=target)


@router.post("/reviews/{review_id}/reject", response_model=ActionResult)
async def reject_review(
    review_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        await service.reject_review(review_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult()


@router.post("/{subject_id}/incidents/{incident_id}/detach", response_model=ActionResult)
async def detach_incident(
    subject_id: uuid.UUID,
    incident_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        new_id = await service.detach_incident(subject_id, incident_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    return ActionResult(subject_id=new_id)


@router.delete("/{subject_id}", response_model=ActionResult)
async def delete_subject(
    subject_id: uuid.UUID,
    request: Request,
    service: SubjectService = Depends(get_subject_service),
    user: dict = Depends(require_admin),
):
    try:
        await service.delete_subject(subject_id, _actor(user), _ip(request))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    return ActionResult(subject_id=subject_id)
```

In `backend/app/main.py`, add `subjects` to the `from app.api.routes import ...` line and register the router after `reports`:

```python
    app.include_router(subjects.router, prefix="/api")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python -m pytest tests/subjects -v`
Expected: all subject tests pass, with model-dependent tests possibly skipped.

- [ ] **Step 7: Commit**

```bash
git add backend/app/subjects/queries.py backend/app/schemas/subject.py backend/app/api/routes/subjects.py backend/app/main.py backend/tests/subjects/test_routes.py
git commit -m "feat(api): /api/subjects list/detail/face/review/merge/reject/detach/delete (admin, access-logged)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---
### Task 14: Frontend — subject badge on alerts and history

**Files:**
- Modify: `frontend/src/types/monitoring.ts` (the `Alert` interface)
- Create: `frontend/src/lib/subjects.ts`
- Create: `frontend/src/components/subjects/SubjectBadge.tsx`
- Modify: `frontend/src/context/MonitoringContext.tsx`
- Modify: `frontend/src/components/alerts/AlertPanel.tsx`
- Modify: `frontend/src/components/views/HistoryView.tsx`
- Test: `frontend/src/lib/subjects.test.ts`
- Test: `frontend/src/components/subjects/SubjectBadge.test.tsx`

**Interfaces:**
- Consumes: the `GET /api/alerts` fields from Task 12 (`incident_id`, `face_status`, `subject_id`, `subject_code`, `subject_incident_count`, `subject_review_pending`).
- Produces:
  - `type SubjectFields`, `mapSubjectFields(a: Record<string, unknown>): SubjectFields`, `subjectBadgeInfo(f: SubjectFields): SubjectBadgeInfo | null`.
  - `<SubjectBadge fields onOpen? className? />`.
  - Context additions:
    - `type AppView` (the existing union plus `'subjects'`);
    - `selectedSubjectId: string | null`;
    - `openSubject(id: string | null): void`, which selects the subject and switches to the `subjects` view when `id` is non-null.

- [ ] **Step 1: Install and record the frontend baseline**

```bash
cd frontend
npm ci
npx vitest run 2>&1 | tail -5
npx tsc --noEmit -p tsconfig.app.json 2>&1 | tail -5
```

Expected: vitest passes (the example test), and tsc prints either nothing or some pre-existing errors. Note the pre-existing tsc error count, if any; later steps must not add new errors.

- [ ] **Step 2: Write the failing tests**

`frontend/src/lib/subjects.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { mapSubjectFields, subjectBadgeInfo } from '@/lib/subjects';

describe('mapSubjectFields', () => {
  it('maps API snake_case fields', () => {
    expect(
      mapSubjectFields({
        incident_id: 'i1',
        face_status: 'matched',
        subject_id: 's1',
        subject_code: 'S-001',
        subject_incident_count: 2,
        subject_review_pending: true,
      }),
    ).toEqual({
      incidentId: 'i1',
      faceStatus: 'matched',
      subjectId: 's1',
      subjectCode: 'S-001',
      subjectIncidentCount: 2,
      subjectReviewPending: true,
    });
  });

  it('defaults missing fields', () => {
    expect(mapSubjectFields({})).toEqual({
      incidentId: undefined,
      faceStatus: null,
      subjectId: undefined,
      subjectCode: undefined,
      subjectIncidentCount: undefined,
      subjectReviewPending: false,
    });
  });
});

describe('subjectBadgeInfo', () => {
  const matched = { faceStatus: 'matched' as const, subjectId: 's1', subjectCode: 'S-007' };

  it('shows nothing while pending, deleted, or without a code', () => {
    expect(subjectBadgeInfo({ faceStatus: null })).toBeNull();
    expect(subjectBadgeInfo({ faceStatus: 'deleted' })).toBeNull();
    expect(subjectBadgeInfo({ faceStatus: 'matched' })).toBeNull();
  });

  it('labels no-face incidents', () => {
    expect(subjectBadgeInfo({ faceStatus: 'no_face' })).toEqual({ label: 'Face not captured', tone: 'muted' });
  });

  it('labels new, repeat and review states', () => {
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 1 })).toEqual({
      label: 'S-007 · New', tone: 'new', subjectId: 's1',
    });
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 3 })).toEqual({
      label: 'S-007 · Repeat ×3', tone: 'repeat', subjectId: 's1',
    });
    expect(subjectBadgeInfo({ ...matched, subjectIncidentCount: 1, subjectReviewPending: true })).toEqual({
      label: 'S-007 · Match under review', tone: 'review', subjectId: 's1',
    });
  });

  it('never uses accusatory wording', () => {
    const labels = [1, 2, 5].map((n) => subjectBadgeInfo({ ...matched, subjectIncidentCount: n })?.label ?? '');
    for (const label of labels) expect(label.toLowerCase()).not.toMatch(/offender|culprit|criminal/);
  });
});
```

`frontend/src/components/subjects/SubjectBadge.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SubjectBadge } from '@/components/subjects/SubjectBadge';

describe('SubjectBadge', () => {
  it('renders a repeat badge that opens the subject', () => {
    const onOpen = vi.fn();
    render(
      <SubjectBadge
        fields={{ faceStatus: 'matched', subjectId: 's1', subjectCode: 'S-003', subjectIncidentCount: 3 }}
        onOpen={onOpen}
      />,
    );
    const badge = screen.getByTestId('subject-badge');
    expect(badge).toHaveTextContent('S-003 · Repeat ×3');
    fireEvent.click(badge);
    expect(onOpen).toHaveBeenCalledWith('s1');
  });

  it('renders nothing while the face result is pending', () => {
    const { container } = render(<SubjectBadge fields={{ faceStatus: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('is not clickable for "Face not captured"', () => {
    const onOpen = vi.fn();
    render(<SubjectBadge fields={{ faceStatus: 'no_face' }} onOpen={onOpen} />);
    fireEvent.click(screen.getByTestId('subject-badge'));
    expect(onOpen).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/subjects.test.ts src/components/subjects/SubjectBadge.test.tsx`
Expected: FAIL, with the error "Failed to resolve import "@/lib/subjects"".

- [ ] **Step 4: Implement the types, helpers and badge**

In `frontend/src/types/monitoring.ts`, add to `interface Alert` after `ttsAudioUrl?: string;`:

```ts
  // Face subject badge fields from GET /api/alerts (face subject-ID feature).
  incidentId?: string;
  faceStatus?: 'matched' | 'no_face' | 'deleted' | null;
  subjectId?: string;
  subjectCode?: string;
  subjectIncidentCount?: number;
  subjectReviewPending?: boolean;
```

`frontend/src/lib/subjects.ts`:

```ts
import type { Alert } from '@/types/monitoring';

export type SubjectFields = Pick<
  Alert,
  'incidentId' | 'faceStatus' | 'subjectId' | 'subjectCode' | 'subjectIncidentCount' | 'subjectReviewPending'
>;

export type BadgeTone = 'new' | 'repeat' | 'review' | 'muted';

export interface SubjectBadgeInfo {
  label: string;
  tone: BadgeTone;
  subjectId?: string;
}

/** Map the snake_case subject fields of a GET /api/alerts item. */
export function mapSubjectFields(a: Record<string, unknown>): SubjectFields {
  return {
    incidentId: (a.incident_id as string | undefined) ?? undefined,
    faceStatus: (a.face_status as SubjectFields['faceStatus']) ?? null,
    subjectId: (a.subject_id as string | undefined) ?? undefined,
    subjectCode: (a.subject_code as string | undefined) ?? undefined,
    subjectIncidentCount: (a.subject_incident_count as number | undefined) ?? undefined,
    subjectReviewPending: Boolean(a.subject_review_pending),
  };
}

/** Neutral badge text for an alert's subject; null while pending or when there is nothing to show. */
export function subjectBadgeInfo(f: SubjectFields): SubjectBadgeInfo | null {
  if (f.faceStatus === 'no_face') return { label: 'Face not captured', tone: 'muted' };
  if (f.faceStatus !== 'matched' || !f.subjectCode) return null;
  if (f.subjectReviewPending) {
    return { label: `${f.subjectCode} · Match under review`, tone: 'review', subjectId: f.subjectId };
  }
  const count = f.subjectIncidentCount ?? 1;
  if (count >= 2) return { label: `${f.subjectCode} · Repeat ×${count}`, tone: 'repeat', subjectId: f.subjectId };
  return { label: `${f.subjectCode} · New`, tone: 'new', subjectId: f.subjectId };
}
```

`frontend/src/components/subjects/SubjectBadge.tsx`:

```tsx
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { subjectBadgeInfo, type BadgeTone, type SubjectFields } from '@/lib/subjects';

const TONE_CLASS: Record<BadgeTone, string> = {
  new: 'bg-primary/10 text-primary border-primary/30',
  repeat: 'bg-destructive/15 text-destructive border-destructive/40 font-semibold',
  review: 'bg-amber-500/15 text-amber-600 border-amber-500/40',
  muted: 'bg-muted text-muted-foreground border-border',
};

interface SubjectBadgeProps {
  fields: SubjectFields;
  onOpen?: (subjectId: string) => void;
  className?: string;
}

export function SubjectBadge({ fields, onOpen, className }: SubjectBadgeProps) {
  const info = subjectBadgeInfo(fields);
  if (!info) return null;
  const subjectId = info.subjectId;
  const clickable = Boolean(subjectId && onOpen);
  return (
    <Badge
      variant="outline"
      data-testid="subject-badge"
      role={clickable ? 'button' : undefined}
      onClick={clickable ? () => onOpen!(subjectId!) : undefined}
      className={cn(
        'text-[10px] px-1.5 h-5 whitespace-nowrap',
        TONE_CLASS[info.tone],
        clickable && 'cursor-pointer hover:opacity-80',
        className,
      )}
    >
      {info.label}
    </Badge>
  );
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/subjects.test.ts src/components/subjects/SubjectBadge.test.tsx`
Expected: 9 passed.

- [ ] **Step 6: Wire it into the context, alert panel and history**

In `frontend/src/context/MonitoringContext.tsx`:

1. Add `import { mapSubjectFields } from '@/lib/subjects';` to the imports.
2. Above `interface MonitoringContextType`, add:
   ```ts
   export type AppView = 'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline' | 'subjects';
   ```
3. In the interface, replace the two `currentView` / `setCurrentView` lines with:
   ```ts
     currentView: AppView;
     setCurrentView: (view: AppView) => void;
     selectedSubjectId: string | null;
     openSubject: (id: string | null) => void;
   ```
4. Replace the `useState<'monitoring' | ... | 'timeline'>('monitoring')` line with:
   ```ts
     const [currentView, setCurrentView] = useState<AppView>('monitoring');
     const [selectedSubjectId, setSelectedSubjectId] = useState<string | null>(null);
     const openSubject = useCallback((id: string | null) => {
       setSelectedSubjectId(id);
       if (id) setCurrentView('subjects');
     }, []);
   ```
5. In `fetchAlerts`, add `...mapSubjectFields(a),` after `ttsAudioUrl: a.tts_audio_url,` inside the `.map((a: any) => ({ ... }))`.
6. Add `selectedSubjectId,` and `openSubject,` to the provider `value` object after `setCurrentView,`.

In `frontend/src/components/alerts/AlertPanel.tsx`:

1. Add `import { SubjectBadge } from '@/components/subjects/SubjectBadge';`.
2. Add `openSubject` to the `useMonitoring()` destructuring in `AlertPanel`.
3. Pass `onOpenSubject={(id) => { openSubject(id); setIsAlertPanelOpen(false); }}` to `<AlertCard ... />`.
4. Add `onOpenSubject: (subjectId: string) => void;` to `AlertCardProps`, and add `onOpenSubject` to `AlertCard`'s parameter destructuring.
5. Inside the `{/* Meta */}` div, after the zone `<span>`, add:
   ```tsx
           <SubjectBadge fields={alert} onOpen={onOpenSubject} />
   ```

In `frontend/src/components/views/HistoryView.tsx`:

1. Add `import { mapSubjectFields } from '@/lib/subjects';`, `import { SubjectBadge } from '@/components/subjects/SubjectBadge';` and `import { useMonitoring } from '@/context/MonitoringContext';`.
2. In the `data.map((a: any) => ({ ... }))` inside `fetchData`, add `...mapSubjectFields(a),` after the `snapshotUrl:` line.
3. In `EventRow`, add `const { openSubject } = useMonitoring();` at the top of the function. Directly after `<span className="text-xs text-muted-foreground">{event.id}</span>`, add:
   ```tsx
               <SubjectBadge fields={event as any} onOpen={openSubject} className="mt-1" />
   ```

- [ ] **Step 7: Type-check and run all frontend tests**

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json 2>&1 | tail -5
npx vitest run
```

Expected: no new tsc errors compared with Step 1, and all vitest tests pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types/monitoring.ts frontend/src/lib/subjects.ts frontend/src/lib/subjects.test.ts frontend/src/components/subjects/SubjectBadge.tsx frontend/src/components/subjects/SubjectBadge.test.tsx frontend/src/context/MonitoringContext.tsx frontend/src/components/alerts/AlertPanel.tsx frontend/src/components/views/HistoryView.tsx
git commit -m "feat(frontend): subject badge on live alerts and event history

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Frontend — Subjects view (grid, detail, review queue)

**Files:**
- Create: `frontend/src/lib/subjectsApi.ts`
- Create: `frontend/src/hooks/useAuthedImage.ts`
- Create: `frontend/src/hooks/usePendingReviewCount.ts`
- Create: `frontend/src/components/subjects/FaceImage.tsx`
- Create: `frontend/src/components/subjects/SubjectGrid.tsx`
- Create: `frontend/src/components/subjects/SubjectDetailPanel.tsx`
- Create: `frontend/src/components/subjects/ReviewQueue.tsx`
- Create: `frontend/src/components/views/SubjectsView.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/pages/Index.tsx`
- Test: `frontend/src/components/subjects/SubjectGrid.test.tsx`
- Test: `frontend/src/components/subjects/ReviewQueue.test.tsx`

**Interfaces:**
- Consumes:
  - the `/api/subjects*` endpoints and JSON shapes from Task 13;
  - `useMonitoring().selectedSubjectId` and `openSubject` from Task 14.
- Produces: the `subjects` view reachable from the sidebar and from badge clicks. The sidebar shows a pending-review count.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/subjects/SubjectGrid.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SubjectGrid } from '@/components/subjects/SubjectGrid';

const subjects = [
  { id: 's3', code: 'S-003', incident_count: 3, first_seen_at: '2026-09-01T08:00:00Z', last_seen_at: '2026-09-20T08:00:00Z', cover_face_id: null, camera_ids: ['cam-01'] },
  { id: 's1', code: 'S-001', incident_count: 1, first_seen_at: '2026-09-02T08:00:00Z', last_seen_at: '2026-09-02T08:00:00Z', cover_face_id: null, camera_ids: ['cam-02'] },
];

describe('SubjectGrid', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => ({ ok: true, json: async () => subjects }));
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists subjects with neutral repeat labels and opens one', async () => {
    const onOpen = vi.fn();
    render(<SubjectGrid onOpen={onOpen} />);
    expect(await screen.findByText('S-003')).toBeInTheDocument();
    expect(screen.getByText('Repeat ×3')).toBeInTheDocument();
    expect(screen.getByText('1 incident')).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('subject-card')[0]);
    expect(onOpen).toHaveBeenCalledWith('s3');
  });

  it('refetches with repeat_only when the toggle is switched on', async () => {
    render(<SubjectGrid onOpen={vi.fn()} />);
    await screen.findByText('S-003');
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('repeat_only=true'))).toBe(true),
    );
  });
});
```

`frontend/src/components/subjects/ReviewQueue.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReviewQueue } from '@/components/subjects/ReviewQueue';

const review = {
  id: 'r1',
  incident_id: 'i2',
  similarity: 0.47,
  status: 'pending',
  created_at: '2026-09-29T10:00:00Z',
  new_subject: { subject_id: 's2', code: 'S-002', face_ids: ['f2'] },
  candidate: { subject_id: 's1', code: 'S-001', face_ids: ['f1'] },
};

describe('ReviewQueue', () => {
  let pending: unknown[];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    pending = [review];
    URL.createObjectURL = vi.fn(() => 'blob:face');
    URL.revokeObjectURL = vi.fn();
    fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/faces/')) return { ok: true, blob: async () => new Blob(['x']) };
      if (init?.method === 'POST') {
        pending = [];
        return { ok: true, json: async () => ({ ok: true, subject_id: 's1' }) };
      }
      return { ok: true, json: async () => pending };
    });
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it('shows both sides and merges on "Same person"', async () => {
    const onChanged = vi.fn();
    render(<ReviewQueue onChanged={onChanged} />);
    expect(await screen.findByText('S-002')).toBeInTheDocument();
    expect(screen.getByText('S-001')).toBeInTheDocument();
    expect(screen.getByText('47% similar')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Same person' }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url).endsWith('/api/subjects/reviews/r1/merge') && init?.method === 'POST',
        ),
      ).toBe(true),
    );
    expect(await screen.findByText('No matches waiting for review.')).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it('rejects on "Different"', async () => {
    render(<ReviewQueue />);
    await screen.findByText('S-002');
    fireEvent.click(screen.getByRole('button', { name: 'Different' }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/api/subjects/reviews/r1/reject'))).toBe(true),
    );
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/subjects/SubjectGrid.test.tsx src/components/subjects/ReviewQueue.test.tsx`
Expected: FAIL, because the `SubjectGrid` / `ReviewQueue` imports can't be resolved.

- [ ] **Step 3: Implement the API client and hooks**

`frontend/src/lib/subjectsApi.ts`:

```ts
import { API_BASE } from '@/lib/api-base';

export interface SubjectSummary {
  id: string;
  code: string;
  incident_count: number;
  first_seen_at: string;
  last_seen_at: string;
  cover_face_id: string | null;
  camera_ids: string[];
}

export interface SubjectFace {
  id: string;
  incident_id: string;
  quality: number;
  created_at: string;
}

export interface SubjectIncident {
  incident_id: string;
  camera_id: string;
  detected_at: string;
  match_type: 'new' | 'auto' | 'review_merged' | 'detached' | string;
  similarity: number | null;
  snapshot_url: string | null;
  zone: string | null;
  title: string | null;
}

export interface SubjectDetail extends SubjectSummary {
  status: string;
  faces: SubjectFace[];
  incidents: SubjectIncident[];
}

export interface ReviewSide {
  subject_id: string;
  code: string;
  face_ids: string[];
}

export interface Review {
  id: string;
  incident_id: string;
  similarity: number;
  status: string;
  created_at: string;
  new_subject: ReviewSide;
  candidate: ReviewSide;
}

export interface ActionResult {
  ok: boolean;
  subject_id: string | null;
}

/**
 * Headers for subject endpoints. The dashboard has no login yet (spec §11,
 * known gap), so this is empty; add the Keycloak bearer token here once
 * dashboard login exists and every subject request picks it up.
 */
export function authHeaders(): Record<string, string> {
  return {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  });
  if (!resp.ok) throw new Error(`${init?.method ?? 'GET'} ${path} failed (${resp.status})`);
  return resp.json() as Promise<T>;
}

export const subjectsApi = {
  list: (repeatOnly: boolean) =>
    request<SubjectSummary[]>(`/api/subjects?repeat_only=${repeatOnly}&sort=incidents`),
  get: (id: string) => request<SubjectDetail>(`/api/subjects/${id}`),
  reviews: () => request<Review[]>('/api/subjects/reviews?status=pending'),
  reviewCount: () => request<{ pending: number }>('/api/subjects/reviews/count'),
  merge: (reviewId: string) =>
    request<ActionResult>(`/api/subjects/reviews/${reviewId}/merge`, { method: 'POST' }),
  reject: (reviewId: string) =>
    request<ActionResult>(`/api/subjects/reviews/${reviewId}/reject`, { method: 'POST' }),
  detach: (subjectId: string, incidentId: string) =>
    request<ActionResult>(`/api/subjects/${subjectId}/incidents/${incidentId}/detach`, { method: 'POST' }),
  remove: (subjectId: string) => request<ActionResult>(`/api/subjects/${subjectId}`, { method: 'DELETE' }),
  faceUrl: (faceId: string) => `${API_BASE}/api/subjects/faces/${faceId}`,
};

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
```

`frontend/src/hooks/useAuthedImage.ts`:

```ts
import { useEffect, useState } from 'react';
import { authHeaders } from '@/lib/subjectsApi';

/** Fetch a private image with auth headers and expose it as a blob: URL (revoked on change/unmount). */
export function useAuthedImage(url: string | null): string | null {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!url) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    fetch(url, { headers: authHeaders(), cache: 'no-store' })
      .then((resp) => (resp.ok ? resp.blob() : null))
      .then((blob) => {
        if (!blob || cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setSrc(null);
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return src;
}
```

`frontend/src/hooks/usePendingReviewCount.ts`:

```ts
import { useEffect, useState } from 'react';
import { subjectsApi } from '@/lib/subjectsApi';

/** Pending face-match reviews, polled for the sidebar badge (endpoint is not access-logged). */
export function usePendingReviewCount(intervalMs = 30_000): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      subjectsApi
        .reviewCount()
        .then((r) => {
          if (!cancelled) setCount(r.pending);
        })
        .catch(() => {});
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [intervalMs]);
  return count;
}
```

- [ ] **Step 4: Implement the components**

`frontend/src/components/subjects/FaceImage.tsx`:

```tsx
import { User } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuthedImage } from '@/hooks/useAuthedImage';
import { subjectsApi } from '@/lib/subjectsApi';

interface FaceImageProps {
  faceId: string | null;
  className?: string;
  alt?: string;
}

export function FaceImage({ faceId, className, alt = 'Face' }: FaceImageProps) {
  const src = useAuthedImage(faceId ? subjectsApi.faceUrl(faceId) : null);
  if (!src) {
    return (
      <div className={cn('rounded border border-dashed border-border bg-muted flex items-center justify-center', className)}>
        <User className="w-1/3 h-1/3 text-muted-foreground" />
      </div>
    );
  }
  return <img src={src} alt={alt} className={cn('object-cover rounded border border-border', className)} />;
}
```

`frontend/src/components/subjects/SubjectGrid.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { FaceImage } from '@/components/subjects/FaceImage';
import { errorMessage, subjectsApi, type SubjectSummary } from '@/lib/subjectsApi';

export function SubjectGrid({ onOpen }: { onOpen: (id: string) => void }) {
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [subjects, setSubjects] = useState<SubjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSubjects(null);
    setError(null);
    subjectsApi
      .list(repeatOnly)
      .then((data) => !cancelled && setSubjects(data))
      .catch((e) => !cancelled && setError(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [repeatOnly]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Switch id="repeat-only" checked={repeatOnly} onCheckedChange={setRepeatOnly} />
        <Label htmlFor="repeat-only" className="text-sm">Repeat only</Label>
      </div>
      {error && <p className="text-sm text-destructive">Could not load subjects: {error}</p>}
      {subjects === null && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {subjects?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No subjects yet. A subject is created when a violation captures a usable face.
        </p>
      )}
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(160px,1fr))]">
        {subjects?.map((s) => (
          <button
            key={s.id}
            type="button"
            data-testid="subject-card"
            onClick={() => onOpen(s.id)}
            className="text-left rounded-lg border border-border bg-card p-2 hover:border-primary/50 transition-colors"
          >
            <FaceImage faceId={s.cover_face_id} className="w-full aspect-square" alt={`${s.code} face`} />
            <div className="mt-2 flex items-center justify-between gap-2">
              <span className="font-mono font-semibold text-sm">{s.code}</span>
              <Badge variant={s.incident_count >= 2 ? 'destructive' : 'secondary'} className="text-[10px] h-5">
                {s.incident_count >= 2 ? `Repeat ×${s.incident_count}` : '1 incident'}
              </Badge>
            </div>
            <p className="text-[11px] text-muted-foreground mt-1">
              Last seen {new Date(s.last_seen_at).toLocaleString()}
            </p>
          </button>
        ))}
      </div>
    </div>
  );
}
```

`frontend/src/components/subjects/SubjectDetailPanel.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { FaceImage } from '@/components/subjects/FaceImage';
import { API_BASE } from '@/lib/api-base';
import { errorMessage, subjectsApi, type SubjectDetail } from '@/lib/subjectsApi';

const MATCH_LABEL: Record<string, string> = {
  new: 'First seen',
  auto: 'Auto-matched',
  review_merged: 'Confirmed by operator',
  detached: 'Detached',
};

interface SubjectDetailPanelProps {
  subjectId: string;
  onBack: () => void;
  onOpen: (id: string | null) => void;
}

export function SubjectDetailPanel({ subjectId, onBack, onOpen }: SubjectDetailPanelProps) {
  const [detail, setDetail] = useState<SubjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setError(null);
    subjectsApi.get(subjectId).then(setDetail).catch((e) => setError(errorMessage(e)));
  }, [subjectId]);

  useEffect(() => {
    setDetail(null);
    load();
  }, [load]);

  const detach = async (incidentId: string) => {
    if (!window.confirm('Move this incident to a new subject? Use this when the automatic match was wrong.')) return;
    setBusy(true);
    try {
      const result = await subjectsApi.detach(subjectId, incidentId);
      if (result.subject_id) onOpen(result.subject_id);
      else load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!detail || !window.confirm(`Delete ${detail.code} and all of its face data? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await subjectsApi.remove(subjectId);
      onBack();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack} className="gap-1">
        <ArrowLeft className="w-4 h-4" /> All subjects
      </Button>
      {error && <p className="text-sm text-destructive">{error}</p>}
      {!detail && !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      {detail && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold font-mono">{detail.code}</h2>
              <p className="text-sm text-muted-foreground">
                {detail.incident_count} {detail.incident_count === 1 ? 'incident' : 'incidents'} · first seen{' '}
                {new Date(detail.first_seen_at).toLocaleString()} · last seen{' '}
                {new Date(detail.last_seen_at).toLocaleString()}
              </p>
            </div>
            <Button variant="destructive" size="sm" onClick={remove} disabled={busy} className="gap-1">
              <Trash2 className="w-4 h-4" /> Delete subject
            </Button>
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {detail.faces.map((f) => (
              <FaceImage key={f.id} faceId={f.id} className="w-24 h-24 flex-shrink-0" alt={`${detail.code} face`} />
            ))}
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Snapshot</TableHead>
                <TableHead>Camera / zone</TableHead>
                <TableHead>Match</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {detail.incidents.map((inc) => (
                <TableRow key={inc.incident_id}>
                  <TableCell className="text-xs font-mono">{new Date(inc.detected_at).toLocaleString()}</TableCell>
                  <TableCell>
                    {inc.snapshot_url ? (
                      <img src={`${API_BASE}${inc.snapshot_url}`} alt="Evidence snapshot" className="w-16 h-12 object-cover rounded border border-border" />
                    ) : (
                      <span className="text-xs text-muted-foreground">No frame</span>
                    )}
                  </TableCell>
                  <TableCell className="text-sm">
                    {inc.camera_id}
                    {inc.zone ? <span className="text-muted-foreground"> · {inc.zone}</span> : null}
                  </TableCell>
                  <TableCell className="text-xs">
                    {MATCH_LABEL[inc.match_type] ?? inc.match_type}
                    {inc.similarity != null && (
                      <span className="text-muted-foreground"> ({Math.round(inc.similarity * 100)}%)</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy || detail.incidents.length <= 1}
                      onClick={() => detach(inc.incident_id)}
                    >
                      Not this person
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </>
      )}
    </div>
  );
}
```

`frontend/src/components/subjects/ReviewQueue.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { FaceImage } from '@/components/subjects/FaceImage';
import { errorMessage, subjectsApi, type Review, type ReviewSide } from '@/lib/subjectsApi';

function Side({ title, side }: { title: string; side: ReviewSide }) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{title}</p>
      <p className="font-mono font-semibold text-sm">{side.code}</p>
      <div className="flex gap-1">
        {side.face_ids.map((id) => (
          <FaceImage key={id} faceId={id} className="w-20 h-20" alt={`${side.code} face`} />
        ))}
      </div>
    </div>
  );
}

export function ReviewQueue({ onChanged }: { onChanged?: () => void }) {
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(() => {
    subjectsApi.reviews().then(setReviews).catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (id: string, action: 'merge' | 'reject') => {
    setBusyId(id);
    setError(null);
    try {
      await (action === 'merge' ? subjectsApi.merge(id) : subjectsApi.reject(id));
      onChanged?.();
    } catch (e) {
      setError(`Could not ${action}: ${errorMessage(e)}`);
    } finally {
      setBusyId(null);
      load();
    }
  };

  if (error && reviews === null) return <p className="text-sm text-destructive">{error}</p>;
  if (reviews === null) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return (
    <div className="space-y-3">
      {error && <p className="text-sm text-destructive">{error}</p>}
      {reviews.length === 0 && <p className="text-sm text-muted-foreground">No matches waiting for review.</p>}
      {reviews.map((r) => (
        <div key={r.id} className="rounded-lg border border-border bg-card p-3 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Possible same person</p>
            <span className="text-xs text-muted-foreground">{Math.round(r.similarity * 100)}% similar</span>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Side title="New incident" side={r.new_subject} />
            <Side title="Existing subject" side={r.candidate} />
          </div>
          <div className="flex gap-2">
            <Button size="sm" disabled={busyId === r.id} onClick={() => act(r.id, 'merge')}>
              Same person
            </Button>
            <Button size="sm" variant="outline" disabled={busyId === r.id} onClick={() => act(r.id, 'reject')}>
              Different
            </Button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

`frontend/src/components/views/SubjectsView.tsx`:

```tsx
import { useState } from 'react';
import { Users } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useMonitoring } from '@/context/MonitoringContext';
import { ReviewQueue } from '@/components/subjects/ReviewQueue';
import { SubjectDetailPanel } from '@/components/subjects/SubjectDetailPanel';
import { SubjectGrid } from '@/components/subjects/SubjectGrid';

export function SubjectsView() {
  const { selectedSubjectId, openSubject } = useMonitoring();
  const [tab, setTab] = useState<'subjects' | 'review'>('subjects');
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Users className="w-5 h-5" /> Subjects
        </h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          Anonymous IDs for people involved in violations. Only faces captured at a violation are kept, and a
          subject is deleted automatically after the retention period (90 days by default) without a new
          incident. Uncertain matches are never linked without your review.
        </p>
      </div>
      <Tabs value={tab} onValueChange={(v) => setTab(v as 'subjects' | 'review')}>
        <TabsList>
          <TabsTrigger value="subjects">Subjects</TabsTrigger>
          <TabsTrigger value="review">Review</TabsTrigger>
        </TabsList>
        <TabsContent value="subjects" className="mt-4">
          {selectedSubjectId ? (
            <SubjectDetailPanel
              key={`${selectedSubjectId}-${refreshKey}`}
              subjectId={selectedSubjectId}
              onBack={() => openSubject(null)}
              onOpen={openSubject}
            />
          ) : (
            <SubjectGrid key={refreshKey} onOpen={openSubject} />
          )}
        </TabsContent>
        <TabsContent value="review" className="mt-4">
          <ReviewQueue onChanged={() => setRefreshKey((k) => k + 1)} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 5: Add the view to the sidebar and the page**

In `frontend/src/components/layout/Sidebar.tsx`:

1. Add `Users,` to the `lucide-react` import list.
2. Add `import { usePendingReviewCount } from '@/hooks/usePendingReviewCount';`.
3. Add `{ id: 'subjects', label: 'Subjects', icon: Users },` to `navigationItems` after the `history` item.
4. In `Sidebar()`, after `const activeAlerts = ...`, add `const pendingReviews = usePendingReviewCount();`.
5. In the nav `<button>`, after `<span className="font-medium">{item.label}</span>`, add:

```tsx
              {item.id === 'subjects' && pendingReviews > 0 && (
                <Badge variant="secondary" className="ml-auto h-5 px-1.5 text-[10px]" title="Matches waiting for review">
                  {pendingReviews}
                </Badge>
              )}
```

In `frontend/src/pages/Index.tsx`, add `import { SubjectsView } from '@/components/views/SubjectsView';` and, after the `history` line:

```tsx
      {currentView === 'subjects' && <SubjectsView />}
```

- [ ] **Step 6: Run the tests, type-check and build**

```bash
cd frontend
npx vitest run
npx tsc --noEmit -p tsconfig.app.json 2>&1 | tail -5
npm run build 2>&1 | tail -5
```

Expected:
- vitest: all tests pass (the 4 new ones included).
- tsc: no new errors compared with the Task 14 baseline.
- build: completes with `✓ built in`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/subjectsApi.ts frontend/src/hooks/useAuthedImage.ts frontend/src/hooks/usePendingReviewCount.ts frontend/src/components/subjects frontend/src/components/views/SubjectsView.tsx frontend/src/components/layout/Sidebar.tsx frontend/src/pages/Index.tsx
git commit -m "feat(frontend): Subjects view with review queue, detach and delete

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Deployment wiring, docs, full verification

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.lite.yml`
- Modify: `backend/Dockerfile`
- Modify: `README.md` (short "Face subject IDs" section)
- Modify: `docs/superpowers/specs/2026-09-29-face-subject-id-design.md` (record the plan-time decisions)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - a `subject-worker` container;
  - a `faces_private` volume shared by `pipeline`, `api` and `subject-worker`;
  - Qdrant available in both compose files.

- [ ] **Step 1: Backend image**

In `backend/Dockerfile`:
- Change the apt line to `RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 && rm -rf /var/lib/apt/lists/*`.
- In the `RUN adduser ...` block, add ` /app/faces_private` to the `mkdir -p` list, so the final ownership line `chown -R appuser:appuser ... /app` covers it.

- [ ] **Step 2: `docker-compose.yml`**

1. `api` service:
   - add `WW_QDRANT_URL: http://qdrant:6333` under `environment`;
   - add `- faces_private:/app/faces_private` under `volumes`;
   - add under `depends_on`:
     ```yaml
           qdrant:
             condition: service_started
     ```
2. `pipeline` service:
   - add `WW_FACE_CAPTURE_ENABLED: ${WW_FACE_CAPTURE_ENABLED:-1}` under `environment`;
   - add `- faces_private:/app/faces_private` under `volumes`.
3. Add this service after `backend-workers`:

```yaml
  # ── Subject worker: violation-time face matching (CPU) ─────────
  # docs/superpowers/specs/2026-09-29-face-subject-id-design.md
  subject-worker:
    build:
      context: ./backend
    env_file:
      - .env
    environment:
      WW_KAFKA_BOOTSTRAP_SERVERS: kafka:9092
      WW_DATABASE_URL: ${WW_DATABASE_URL}
      WW_QDRANT_URL: http://qdrant:6333
      # Stricter than the 0.55 code default for the first week (spec §16);
      # lower to 0.55 once logged scores show no false auto-matches.
      WW_FACE_MATCH_AUTO: ${WW_FACE_MATCH_AUTO:-0.60}
    depends_on:
      kafka:
        condition: service_healthy
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
    volumes:
      - faces_private:/app/faces_private
    command: ["python3", "-m", "services.subjects.main"]
    restart: unless-stopped
```

4. Add `faces_private:` to the top-level `volumes:` list.

- [ ] **Step 3: `docker-compose.lite.yml`**

1. Change the header comment line `# Lightweight stack: core path only (no keycloak, no qdrant).` to `# Lightweight stack: core path only (no keycloak). Qdrant is included for face subject IDs.`.
2. Add this service after `redis`:

```yaml
  qdrant:
    image: qdrant/qdrant:v1.13.6
    volumes:
      - qdrant_data:/qdrant/storage
    restart: unless-stopped
```

3. Make the same `api` and `pipeline` edits as in Step 2.1–2.2, and add the same `subject-worker` service.
4. Add `qdrant_data:` and `faces_private:` to the top-level `volumes:` list.

- [ ] **Step 4: Validate the compose files (if Docker is available)**

```bash
docker compose -f docker-compose.yml config --quiet && echo main-ok
docker compose -f docker-compose.lite.yml config --quiet && echo lite-ok
```

Expected: `main-ok` and `lite-ok`. If Docker isn't installed on this machine, run `python -c "import yaml,sys;[yaml.safe_load(open(f)) for f in sys.argv[1:]];print('yaml-ok')" docker-compose.yml docker-compose.lite.yml` with the backend venv instead (install `pyyaml` into it first), and note in the report that `docker compose config` still needs to run on the deployment box.

- [ ] **Step 5: README and spec notes**

Append to `README.md`:

```markdown
## Face subject IDs (repeat violators)

When a violation alert fires, the pipeline keeps the violator's clean (pre-overlay) head crops and the
`subject-worker` assigns an anonymous ID (`S-001`, ...). Repeat violations by the same person are
linked automatically when the face match is confident, or queued for operator review in
**Subjects → Review** when it is not. Design: `docs/superpowers/specs/2026-09-29-face-subject-id-design.md`.

- Kill switch: `WW_FACE_CAPTURE_ENABLED=0` on the pipeline restores the previous behaviour exactly.
- Thresholds: `WW_FACE_MATCH_AUTO` (compose default 0.60 for the first week, code default 0.55) and `WW_FACE_MATCH_REVIEW` (0.40).
- Retention: subjects with no new incident for `WW_SUBJECT_RETENTION_DAYS` (90) are deleted with their faces.
- Face images live only in the `faces_private` volume and are served only via `/api/subjects/faces/{id}` (admin, access-logged).
- Models: `python scripts/download_face_models.py` (InsightFace buffalo_l — **non-commercial research licence**; a paid deployment needs a commercial licence or a replacement model).
- Only the Python metadata path fires alerts, so faces are not captured when the native `wastewatchmeta` plugin path is active.
```

In the spec, add this subsection at the end of §8 (Operator actions):

```markdown
**Plan-time refinements (2026-09-29):** detaching a subject's *only* incident returns 409 instead of creating an empty subject;
if Qdrant search fails, matching falls back to an exact search over the embeddings stored in Postgres; the sidebar's pending-review
count uses `GET /api/subjects/reviews/count`, which is not access-logged because it carries no personal data.
```

- [ ] **Step 6: Full automated verification**

```bash
cd pipeline && ../backend/.venv/Scripts/python -m pytest tests -q; cd ..
cd backend && .venv/Scripts/python -m pytest tests/subjects -q && .venv/Scripts/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -15; cd ..
cd frontend && npx vitest run && npm run build 2>&1 | tail -3; cd ..
```

Expected:
- pipeline: all pass.
- `tests/subjects`: all pass, with model-dependent tests skipped if the weights are absent.
- Full backend suite: failures are **only** the pre-existing ones recorded in the Task 1 commit message.
- frontend: all pass, and the build succeeds.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml docker-compose.lite.yml backend/Dockerfile README.md docs/superpowers/specs/2026-09-29-face-subject-id-design.md
git commit -m "chore(deploy): subject-worker, faces_private volume, Qdrant in lite stack; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Live-stack checklist**

This runs on the GPU deployment box and is done with the user. Nothing is pushed.

1. `docker compose up -d --build`, then run migrations: `docker compose exec api alembic upgrade head`. Expect the output to end at `008`.
2. `docker compose logs -f subject-worker`. Expect `Subject worker ready`.
3. Start the sample stream. `docker compose logs pipeline | grep "Face capture enabled"` should print one line.
4. Check pipeline FPS and output-stream smoothness with the kill switch **on** and **off** (`WW_FACE_CAPTURE_ENABLED=0 docker compose up -d pipeline`). Expect no visible stall and no FPS drop greater than 1 fps.
5. With nobody violating, `docker compose exec subject-worker ls /app/faces_private/incoming` stays empty.
6. Trigger a dumping violation. Within about 8 s the alert card shows `S-001 · New`, or `Face not captured` if the face wasn't visible. Repeat with the same person: expect `S-001 · Repeat ×2` or a review item.
7. Open **Subjects**: the face crops show, and the incident timeline shows snapshots. Merge, reject and detach all work. `SELECT action, actor FROM subject_access_log ORDER BY at DESC LIMIT 10;` shows the actions.
8. Confirm that the black privacy rectangle is still drawn on the live output stream, and that face crops are **not** reachable under `/evidence/...`.
