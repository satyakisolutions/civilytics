# OSD Activity Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-object bbox labels with enriched inline format showing track ID, ROI flag, action, dwell time, and FSM state — on both the DeepStream H.264 stream and the frontend React overlay. Reconfigure FSM thresholds for narrow-road timing (3s/10s/15s). Wire OBSERVE state to start smart recording + VLM + TTS warning. Add evidence lifecycle (keep video on violation, delete on clear).

**Architecture:** Pipeline `decorate_for_osd` probe rewritten for enriched labels + stats overlay. FSM thresholds reconfigured via `config.py`. `extract_metadata` probe gets OBSERVE/VIOLATION trigger hooks. New `EvidenceWriter` handles evidence JSON + keyframe + recording retention. Frontend `MetadataOverlay.tsx` updated with enriched inline labels and stats pills.

**Tech Stack:** Python (DeepStream pyds), React/TypeScript, Tailwind CSS, Kafka, WebSocket

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `pipeline/app/config.py` | Pipeline configuration | Modify — add `dwell_violation_threshold_s`, adjust enter/observe thresholds |
| `pipeline/app/event_fsm.py` | FSM state machine | Modify — add violation threshold, split OBSERVE→VIOLATION transition |
| `pipeline/app/pipeline.py` | OSD probe + metadata probe | Modify — rewrite `decorate_for_osd`, add OBSERVE/VIOLATION trigger hooks in `extract_metadata` |
| `pipeline/app/evidence_writer.py` | Evidence JSON + keyframe + recording lifecycle | Create |
| `frontend/src/components/camera/MetadataOverlay.tsx` | Frontend detection overlay | Modify — enriched inline labels, stats pills |

---

### Task 1: Reconfigure FSM Thresholds

**Files:**
- Modify: `pipeline/app/config.py`
- Modify: `pipeline/app/event_fsm.py`

- [ ] **Step 1: Update config thresholds**

In `pipeline/app/config.py`, change the `PipelineSettings` class:

```python
# Dwell thresholds — tuned for narrow-road LSG deployment
dwell_enter_threshold_s: float = 3.0      # was 5.0 — 3s standing is notable
dwell_observe_threshold_s: float = 10.0   # was 15.0 — 10s is suspicious
dwell_violation_threshold_s: float = 15.0  # new — 15s confirms violation
```

- [ ] **Step 2: Fix dwell time reset on ROI exit**

In `pipeline/app/tracking.py`, the `TrackInfo.update()` method sets `in_roi=False` when leaving ROI but does NOT reset `roi_entered_at` — so `dwell_time_s` keeps growing after leaving. Fix line 44-45:

```python
elif not in_roi and self.in_roi:
    self.in_roi = False
    self.roi_entered_at = None  # Reset — dwell only counts time IN roi
```

- [ ] **Step 3: Add violation threshold to TrackFSM dataclass**

In `pipeline/app/event_fsm.py`, update `TrackFSM` dataclass to add the new field:

```python
@dataclass
class TrackFSM:
    camera_id: str
    track_id: int
    class_name: str
    state: FSMState = FSMState.IDLE
    entered_at: float | None = None
    dwell_time_s: float = 0.0
    last_bbox: dict | None = None
    last_update: float = field(default_factory=time.monotonic)
    enter_threshold_s: float = 3.0
    observe_threshold_s: float = 10.0
    violation_threshold_s: float = 15.0
    exit_timeout_s: float = 300.0
```

- [ ] **Step 3: Update EventFSMManager constructor and transitions**

In `EventFSMManager.__init__`, accept and store the new threshold:

```python
def __init__(self, enter_threshold_s: float = 3.0, observe_threshold_s: float = 10.0,
             violation_threshold_s: float = 15.0, exit_timeout_s: float = 300.0):
    self._fsms: dict[tuple[str, int], TrackFSM] = {}
    self._enter_threshold = enter_threshold_s
    self._observe_threshold = observe_threshold_s
    self._violation_threshold = violation_threshold_s
    self._exit_timeout = exit_timeout_s
```

Update the `update()` method's TrackFSM creation to pass it:

```python
self._fsms[key] = TrackFSM(
    camera_id=camera_id, track_id=track_id, class_name=class_name,
    enter_threshold_s=self._enter_threshold,
    observe_threshold_s=self._observe_threshold,
    violation_threshold_s=self._violation_threshold,
    exit_timeout_s=self._exit_timeout,
)
```

Split the OBSERVE → VIOLATION transition to use the new threshold. In the `update()` method, change the OBSERVE block:

```python
elif fsm.state == FSMState.OBSERVE:
    if not in_roi:
        fsm.state = FSMState.EXITED
    elif dwell_time_s >= self._violation_threshold:
        fsm.state = FSMState.VIOLATION
```

And the ENTER → OBSERVE block stays the same but now uses the 10s observe threshold:

```python
elif fsm.state == FSMState.ENTER:
    if not in_roi:
        fsm.state = FSMState.EXITED
        self._cleanup(key)
        return None
    elif dwell_time_s >= self._observe_threshold:
        fsm.state = FSMState.OBSERVE
```

- [ ] **Step 4: Update pipeline.py FSM instantiation**

In `pipeline/app/pipeline.py` `main()`, update the `EventFSMManager` creation (around line 817):

```python
fsm_manager = EventFSMManager(
    enter_threshold_s=pipeline_settings.dwell_enter_threshold_s,
    observe_threshold_s=pipeline_settings.dwell_observe_threshold_s,
    violation_threshold_s=pipeline_settings.dwell_violation_threshold_s,
)
```

- [ ] **Step 5: Verify FSM transitions work**

Run: `docker compose exec pipeline python3 -c "
from app.event_fsm import EventFSMManager
fsm = EventFSMManager(enter_threshold_s=3.0, observe_threshold_s=10.0, violation_threshold_s=15.0)
# Simulate: enter ROI at 0s, dwell increases
for t in [1, 2, 3, 5, 8, 10, 12, 15, 18]:
    evt = fsm.update('cam-01', 1, 'person', in_roi=True, dwell_time_s=float(t))
    if evt: print(f't={t}s: {evt.prev_state.value} -> {evt.state.value}')
"`

Expected:
```
t=3s: idle -> enter
t=10s: enter -> observe
t=15s: observe -> violation
```

- [ ] **Step 6: Commit**

```bash
git add pipeline/app/config.py pipeline/app/event_fsm.py pipeline/app/pipeline.py pipeline/app/tracking.py
git commit -m "feat(pipeline): reconfigure FSM thresholds (3s/10s/15s) + fix dwell reset on ROI exit"
```

---

### Task 2: Rewrite DeepStream OSD Labels

**Files:**
- Modify: `pipeline/app/pipeline.py` (function `decorate_for_osd`, lines 210-353)

- [ ] **Step 1: Add action text derivation helpers**

Add these helper functions above `decorate_for_osd` in `pipeline/app/pipeline.py`:

```python
def _derive_action_text(
    class_name: str,
    category: str,
    dwell_time_s: float,
    carried_item: str | None,
) -> str:
    """Derive human-readable action text for the bbox label."""
    if carried_item:
        return f"carrying {carried_item}"
    if category == "waste_object":
        return f"dumped {class_name}"
    if category == "vehicle":
        return "stopped" if dwell_time_s >= 3.0 else "passing"
    if category == "person":
        return "waiting" if dwell_time_s >= 3.0 else "moving"
    return class_name


def _find_carried_item(
    person_rect,
    carried_items: list[tuple],
) -> str | None:
    """Check if any carried_item bbox overlaps >50% with a person bbox.
    
    Args:
        person_rect: pyds rect_params of the person
        carried_items: list of (class_name, rect_params) tuples
    
    Returns:
        The class_name of the overlapping carried item, or None.
    """
    if not carried_items:
        return None
    px1, py1 = person_rect.left, person_rect.top
    px2, py2 = px1 + person_rect.width, py1 + person_rect.height
    p_area = person_rect.width * person_rect.height
    if p_area <= 0:
        return None

    for item_name, item_rect in carried_items:
        ix1, iy1 = item_rect.left, item_rect.top
        ix2, iy2 = ix1 + item_rect.width, iy1 + item_rect.height
        # Intersection
        ox1 = max(px1, ix1)
        oy1 = max(py1, iy1)
        ox2 = min(px2, ix2)
        oy2 = min(py2, iy2)
        if ox2 <= ox1 or oy2 <= oy1:
            continue
        overlap = (ox2 - ox1) * (oy2 - oy1)
        item_area = item_rect.width * item_rect.height
        if item_area > 0 and overlap / item_area > 0.5:
            return item_name
    return None
```

- [ ] **Step 2: Rewrite `decorate_for_osd` with enriched labels and stats overlay**

Replace the entire `decorate_for_osd` function body with:

```python
def decorate_for_osd(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    _user_data: object,
) -> Gst.PadProbeReturn:
    """Populate every NvDsObjectMeta with enriched bbox labels and stats overlay.

    Label format: #NNN ROI | action | Xs STATE
    Stats overlay (top-right): Objects: N | Escalated: M
    """
    buf = info.get_buffer()
    if buf is None:
        return Gst.PadProbeReturn.OK
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    if batch_meta is None:
        return Gst.PadProbeReturn.OK

    # FSM state → OSD colors
    STATE_TEXT_COLORS = {
        "idle":      (0.75, 0.75, 0.75, 1.0),
        "enter":     (0.20, 0.85, 1.00, 1.0),
        "observe":   (1.0, 1.0, 1.0, 1.0),
        "violation": (1.0, 1.0, 1.0, 1.0),
        "alerting":  (1.0, 1.0, 1.0, 1.0),
        "escalated": (1.0, 1.0, 1.0, 1.0),
    }
    STATE_BG_COLORS = {
        "idle":      (0.0, 0.0, 0.0, 0.55),
        "enter":     (0.0, 0.0, 0.0, 0.55),
        "observe":   (0.95, 0.60, 0.05, 0.80),
        "violation": (0.90, 0.15, 0.15, 0.80),
        "alerting":  (0.90, 0.15, 0.15, 0.80),
        "escalated": (0.80, 0.10, 0.10, 0.90),
    }
    STATE_BORDER_WIDTH = {
        "idle": 2, "enter": 2, "observe": 3,
        "violation": 4, "alerting": 4, "escalated": 4,
    }
    STATE_BORDER_COLORS = {
        "idle":      (0.75, 0.75, 0.75, 1.0),
        "enter":     (0.20, 0.85, 1.00, 1.0),
        "observe":   (0.95, 0.70, 0.10, 1.0),
        "violation": (1.00, 0.25, 0.25, 1.0),
        "alerting":  (1.00, 0.25, 0.25, 1.0),
        "escalated": (0.90, 0.10, 0.10, 1.0),
    }
    STATE_LABELS = {
        "enter": "ENTER", "observe": "OBSERVE", "violation": "ALERT",
        "alerting": "ALERT", "escalated": "ESCALATED",
    }

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        frame_w = frame_meta.source_frame_width or pipeline_settings.input_width
        total_objects = 0
        escalated_count = 0

        # --- First pass: collect carried_items for association ---
        carried_items = []
        obj_meta_list = frame_meta.obj_meta_list
        while obj_meta_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
            except StopIteration:
                break
            cid = int(obj_meta.class_id)
            cname = COCO_CLASS_MAP.get(cid)
            if cname and CLASS_CATEGORIES.get(cname) == "carried_item":
                carried_items.append((cname, obj_meta.rect_params))
            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        # --- Second pass: decorate all objects ---
        obj_meta_list = frame_meta.obj_meta_list
        while obj_meta_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
            except StopIteration:
                break

            cid = int(obj_meta.class_id)
            cname = COCO_CLASS_MAP.get(cid)
            if cname is None:
                try:
                    obj_meta_list = obj_meta_list.next
                except StopIteration:
                    break
                continue

            total_objects += 1
            category = CLASS_CATEGORIES.get(cname, "unknown")
            track_id = int(obj_meta.object_id)
            is_tracked = track_id != 0xFFFFFFFFFFFFFFFF
            short_id = track_id % 1000 if is_tracked else 0

            # Get FSM state and dwell from user meta (set by extract_metadata)
            # For OSD, we approximate using confidence and track existence
            fsm_state = "idle"
            dwell_time_s = 0.0
            in_roi = False

            # The FSM state is available via the metadata probe context,
            # but decorate_for_osd runs on a different pad. We read the
            # tracker confidence as a proxy — objects with NvDsObjectMeta
            # have tracker_confidence set by nvtracker.
            # For now, use the text that extract_metadata has already
            # written into obj_meta.text_params via a shared dict.
            # APPROACH: We'll read the per-track FSM state from the
            # shared fsm_manager instance that pipeline.py creates.
            # This is safe because both probes run on the GLib main thread.

            # --- Build label ---
            if not is_tracked or (fsm_state == "idle" and not in_roi):
                # Minimal label for untracked / idle+not-in-ROI
                label = f"#{short_id:03d} {cname}" if is_tracked else cname
            else:
                # Enriched label
                carried = _find_carried_item(obj_meta.rect_params, carried_items) if category == "person" else None
                action = _derive_action_text(cname, category, dwell_time_s, carried)
                roi_tag = "ROI" if in_roi else ""
                state_tag = STATE_LABELS.get(fsm_state, "")
                dwell_tag = f"{dwell_time_s:.0f}s" if dwell_time_s > 0 else ""
                parts = [f"#{short_id:03d}"]
                if roi_tag:
                    parts.append(roi_tag)
                parts.append(action)
                if dwell_tag:
                    parts.append(dwell_tag)
                if state_tag:
                    parts.append(state_tag)
                label = " | ".join(parts[:2]) + " | " + " ".join(parts[2:]) if len(parts) > 2 else " | ".join(parts)

            # Check escalation
            if fsm_state in ("violation", "alerting", "escalated"):
                escalated_count += 1

            # --- Apply colors ---
            r, g, b, a = STATE_BORDER_COLORS.get(fsm_state, (0.75, 0.75, 0.75, 1.0))
            rect = obj_meta.rect_params
            rect.border_width = STATE_BORDER_WIDTH.get(fsm_state, 2)
            rect.border_color.red = r
            rect.border_color.green = g
            rect.border_color.blue = b
            rect.border_color.alpha = a
            rect.has_bg_color = 0

            # --- Label text ---
            tr, tg, tb, ta = STATE_TEXT_COLORS.get(fsm_state, (0.75, 0.75, 0.75, 1.0))
            br, bg_, bb, ba = STATE_BG_COLORS.get(fsm_state, (0.0, 0.0, 0.0, 0.55))
            txt = obj_meta.text_params
            txt.display_text = label
            txt.x_offset = max(0, int(rect.left))
            txt.y_offset = max(0, int(rect.top) - 18)
            txt.font_params.font_name = "Serif"
            txt.font_params.font_size = 11
            txt.font_params.font_color.red = tr
            txt.font_params.font_color.green = tg
            txt.font_params.font_color.blue = tb
            txt.font_params.font_color.alpha = ta
            txt.set_bg_clr = 1
            txt.text_bg_clr.red = br
            txt.text_bg_clr.green = bg_
            txt.text_bg_clr.blue = bb
            txt.text_bg_clr.alpha = ba

            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        # --- Stats overlay (top-right) ---
        try:
            disp_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
            disp_meta.num_labels = 1
            tp = disp_meta.text_params[0]
            tp.display_text = f"Objects: {total_objects} | Escalated: {escalated_count}"
            tp.x_offset = max(10, int(frame_w) - 400)
            tp.y_offset = 20
            tp.font_params.font_name = "Serif"
            tp.font_params.font_size = 14
            tp.font_params.font_color.red = 1.0
            tp.font_params.font_color.green = 1.0
            tp.font_params.font_color.blue = 1.0
            tp.font_params.font_color.alpha = 1.0
            tp.set_bg_clr = 1
            if escalated_count > 0:
                tp.text_bg_clr.red, tp.text_bg_clr.green, tp.text_bg_clr.blue = 0.80, 0.10, 0.10
            else:
                tp.text_bg_clr.red, tp.text_bg_clr.green, tp.text_bg_clr.blue = 0.10, 0.45, 0.15
            tp.text_bg_clr.alpha = 0.70
            pyds.nvds_add_display_meta_to_frame(frame_meta, disp_meta)
        except Exception:
            pass

        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK
```

**Note on FSM state access:** The `decorate_for_osd` probe and `extract_metadata` probe run on different pads but on the same GLib main thread. To get FSM state per object in the OSD probe, we need to pass the `fsm_manager` instance. Update the probe attachment in `main()` to pass it as user_data:

In `pipeline/app/pipeline.py` `main()`, change the OSD probe attachment (around line 854):

```python
osd_sink.add_probe(
    Gst.PadProbeType.BUFFER,
    decorate_for_osd,
    {"fsm_manager": fsm_manager, "track_manager": track_manager},
)
```

Then update `decorate_for_osd` signature to accept the context dict and use it:

```python
def decorate_for_osd(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    ctx: dict,
) -> Gst.PadProbeReturn:
```

And inside the second pass, look up FSM state per track:

```python
    fsm_mgr = ctx.get("fsm_manager") if ctx else None
    track_mgr = ctx.get("track_manager") if ctx else None
    # ...
    # Inside the per-object loop, after getting track_id:
    if is_tracked and fsm_mgr:
        fsm_state = fsm_mgr.get_fsm_state(camera_id, track_id) or "idle"
    if is_tracked and track_mgr:
        track_info = track_mgr.get_track(camera_id, track_id)
        if track_info:
            dwell_time_s = track_info.dwell_time_s
            in_roi = track_info.in_roi
```

Where `camera_id` is derived from `frame_meta.source_id`:

```python
    camera_id = f"cam-{frame_meta.source_id:02d}"
```

- [ ] **Step 3: Commit**

```bash
git add pipeline/app/pipeline.py
git commit -m "feat(pipeline): enriched OSD labels with FSM state + stats overlay"
```

---

### Task 3: Wire OBSERVE and VIOLATION Triggers in extract_metadata

**Files:**
- Modify: `pipeline/app/pipeline.py` (function `extract_metadata`, lines 361-536)

- [ ] **Step 1: Add OBSERVE trigger — smart recording + VLM + TTS warning**

In `extract_metadata`, after the FSM event is produced (around line 450), add OBSERVE-specific triggers:

```python
if fsm_event:
    kafka_producer.send_event(fsm_event)

    # --- OBSERVE triggers: start evidence gathering ---
    if fsm_event.state == FSMState.OBSERVE:
        # Start smart recording
        kafka_producer.send_evidence_start(
            camera_id=camera_id,
            track_id=track_id,
            class_name=class_name,
        )
        # Send VLM analysis request
        kafka_producer.send_vlm_request(
            camera_id=camera_id, track_id=track_id,
            zone_name=camera_id, dwell_time_s=track_info.dwell_time_s,
            class_name=class_name,
            path_summary=f"observe_trigger:{dwell_time_s:.0f}s",
        )
        # Send TTS warning (not confirmed alert yet)
        kafka_producer.send_tts_request(
            camera_id=camera_id,
            track_id=track_id,
            text=f"Warning. Suspicious activity detected at camera {camera_id}. A {class_name} has been in the restricted zone for {int(dwell_time_s)} seconds. Please investigate.",
            priority="normal",
        )

    # --- VIOLATION triggers: confirmed alert ---
    elif fsm_event.state == FSMState.VIOLATION:
        # Dispatch confirmed alert
        kafka_producer.send_alert(
            camera_id=camera_id,
            track_id=track_id,
            severity="critical",
            title=f"Violation confirmed — {class_name} at {camera_id}",
            message=f"A {class_name} has been in the restricted zone for {int(dwell_time_s)} seconds. Alert dispatched.",
        )
        # Escalation TTS
        kafka_producer.send_tts_request(
            camera_id=camera_id,
            track_id=track_id,
            text=f"Alert. Violation confirmed at camera {camera_id}. A {class_name} has been in the restricted zone for {int(dwell_time_s)} seconds. Immediate attention required.",
            priority="high",
        )

    # --- LPR on violation (existing, keep) ---
    if fsm_event.state.value == "violation":
        if class_name in VEHICLE_CLASSES:
            kafka_producer.send_lpr_request(
                camera_id=camera_id,
                track_id=track_id,
                vehicle_bbox=bbox_dict,
                class_name=class_name,
            )
```

- [ ] **Step 2: Remove old VLM/TTS triggers that fire on VIOLATION only**

Remove or guard the existing `kafka_producer.send_vlm_request()` calls that were triggered by the FrameBatcher's on_frame/on_track_exit logic (around lines 484-499). These are now replaced by the OBSERVE trigger above. Keep the FrameBatcher for batch-mode VLM but change its trigger to collect frames only during OBSERVE+VIOLATION states (already configured).

- [ ] **Step 3: Commit**

```bash
git add pipeline/app/pipeline.py
git commit -m "feat(pipeline): wire OBSERVE triggers (recording+VLM+TTS) and VIOLATION confirmed alert"
```

---

### Task 4: Create Evidence Writer

**Files:**
- Create: `pipeline/app/evidence_writer.py`

- [ ] **Step 1: Create the EvidenceWriter class**

```python
# pipeline/app/evidence_writer.py
"""Evidence lifecycle manager.

Handles JSON metadata, keyframe capture, and recording retention
based on VLM outcome (CLEAR → delete video, keep keyframe+JSON;
VIOLATION → keep everything).
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

EVIDENCE_BASE = Path(os.environ.get("WW_EVIDENCE_DIR", "/app/evidence"))


@dataclass
class EvidenceRecord:
    event_id: str
    camera_id: str
    track_id: int
    timestamp_start: str
    timestamp_end: str
    duration_s: float
    outcome: str  # "violation" | "suspicious" | "clear"
    fsm_states: list[str]
    class_name: str
    action: str
    dwell_time_s: float
    in_roi: bool
    roi_name: str
    vlm_result: dict | None
    bbox_path: list[dict]
    keyframe_url: str
    video_url: str
    tts_sent: bool
    alert_dispatched: bool


class EvidenceWriter:
    """Manages evidence files based on VLM outcome."""

    def __init__(self, base_dir: Path | None = None):
        self._base = base_dir or EVIDENCE_BASE
        self._active: dict[tuple[str, int], dict] = {}  # (cam, track) → context

    def start_evidence(
        self, camera_id: str, track_id: int, class_name: str,
        roi_name: str = "Restricted Zone",
    ) -> str:
        """Called on OBSERVE — begin tracking evidence for this event."""
        event_id = uuid.uuid4().hex[:12]
        self._active[(camera_id, track_id)] = {
            "event_id": event_id,
            "camera_id": camera_id,
            "track_id": track_id,
            "class_name": class_name,
            "roi_name": roi_name,
            "timestamp_start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "fsm_states": ["observe"],
            "bbox_path": [],
            "best_keyframe": None,
            "best_confidence": 0.0,
        }
        logger.info("Evidence started: %s for %s track=%d", event_id, camera_id, track_id)
        return event_id

    def add_frame(
        self, camera_id: str, track_id: int,
        bbox: dict, confidence: float, fsm_state: str,
    ) -> None:
        """Record bbox path and track best keyframe candidate."""
        ctx = self._active.get((camera_id, track_id))
        if not ctx:
            return
        ctx["bbox_path"].append({"t": len(ctx["bbox_path"]), **bbox})
        if fsm_state not in ctx["fsm_states"]:
            ctx["fsm_states"].append(fsm_state)
        if confidence > ctx["best_confidence"]:
            ctx["best_confidence"] = confidence

    def finalize(
        self, camera_id: str, track_id: int,
        outcome: str,
        dwell_time_s: float,
        action: str,
        vlm_result: dict | None = None,
        video_path: str | None = None,
        keyframe_data: bytes | None = None,
        tts_sent: bool = False,
        alert_dispatched: bool = False,
    ) -> EvidenceRecord | None:
        """Called on VLM result or VIOLATION — finalize evidence."""
        ctx = self._active.pop((camera_id, track_id), None)
        if not ctx:
            logger.warning("finalize: no active evidence for %s track=%d", camera_id, track_id)
            return None

        timestamp_end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event_id = ctx["event_id"]
        date_str = time.strftime("%Y-%m-%d")

        # Determine storage directory
        if outcome == "clear":
            subdir = self._base / "suspicious" / camera_id / date_str
        else:
            subdir = self._base / "violations" / camera_id / date_str
        subdir.mkdir(parents=True, exist_ok=True)

        # Save keyframe
        keyframe_path = subdir / f"evt_{event_id}_keyframe.jpg"
        keyframe_url = str(keyframe_path)
        if keyframe_data:
            keyframe_path.write_bytes(keyframe_data)

        # Handle video
        video_url = ""
        if outcome == "clear":
            # Delete the recording — only keep keyframe + JSON
            if video_path and os.path.exists(video_path):
                try:
                    os.remove(video_path)
                    logger.info("Deleted recording for cleared event %s", event_id)
                except OSError:
                    logger.exception("Failed to delete recording %s", video_path)
        else:
            # Move/keep the recording
            if video_path and os.path.exists(video_path):
                dest = subdir / f"evt_{event_id}.mp4"
                try:
                    os.rename(video_path, str(dest))
                    video_url = str(dest)
                except OSError:
                    video_url = video_path  # Keep in place if move fails

        # Build record
        record = EvidenceRecord(
            event_id=event_id,
            camera_id=camera_id,
            track_id=track_id,
            timestamp_start=ctx["timestamp_start"],
            timestamp_end=timestamp_end,
            duration_s=dwell_time_s,
            outcome=outcome,
            fsm_states=ctx["fsm_states"],
            class_name=ctx["class_name"],
            action=action,
            dwell_time_s=dwell_time_s,
            in_roi=True,
            roi_name=ctx["roi_name"],
            vlm_result=vlm_result,
            bbox_path=ctx["bbox_path"],
            keyframe_url=keyframe_url,
            video_url=video_url,
            tts_sent=tts_sent,
            alert_dispatched=alert_dispatched,
        )

        # Write JSON
        json_path = subdir / f"evt_{event_id}.json"
        json_path.write_text(json.dumps(asdict(record), indent=2))
        logger.info("Evidence finalized: %s outcome=%s → %s", event_id, outcome, json_path)

        return record
```

- [ ] **Step 2: Commit**

```bash
git add pipeline/app/evidence_writer.py
git commit -m "feat(pipeline): add EvidenceWriter for evidence JSON + keyframe + recording lifecycle"
```

---

### Task 5: Update Frontend MetadataOverlay with Enriched Labels

**Files:**
- Modify: `frontend/src/components/camera/MetadataOverlay.tsx`

- [ ] **Step 1: Add action text derivation and carried-item association**

Add these utility functions at the top of `MetadataOverlay.tsx` (after the existing constants):

```typescript
// ---------------------------------------------------------------------------
// Action text derivation
// ---------------------------------------------------------------------------

function deriveActionText(det: BBoxDetection, carriedItem: string | null): string {
  if (carriedItem) return `carrying ${carriedItem}`;
  if (det.category === "waste_object") return `dumped ${det.class_name}`;
  const dwell = det.dwell_time_s ?? 0;
  if (det.category === "vehicle") return dwell >= 3 ? "stopped" : "passing";
  return dwell >= 3 ? "waiting" : "moving";
}

function findCarriedItem(person: BBoxDetection, detections: BBoxDetection[]): string | null {
  const [px1, py1, px2, py2] = person.bbox;
  const pArea = (px2 - px1) * (py2 - py1);
  if (pArea <= 0) return null;

  for (const det of detections) {
    if (det.category !== "carried_item") continue;
    const [ix1, iy1, ix2, iy2] = det.bbox;
    const ox1 = Math.max(px1, ix1);
    const oy1 = Math.max(py1, iy1);
    const ox2 = Math.min(px2, ix2);
    const oy2 = Math.min(py2, iy2);
    if (ox2 <= ox1 || oy2 <= oy1) continue;
    const overlap = (ox2 - ox1) * (oy2 - oy1);
    const itemArea = (ix2 - ix1) * (iy2 - iy1);
    if (itemArea > 0 && overlap / itemArea > 0.5) return det.class_name;
  }
  return null;
}

function formatTrackId(trackId: number | null | undefined): string {
  if (trackId == null) return "";
  return `#${String(trackId % 1000).padStart(3, "0")}`;
}

// ---------------------------------------------------------------------------
// Enriched label builder
// ---------------------------------------------------------------------------

function buildEnrichedLabel(det: BBoxDetection, allDetections: BBoxDetection[]): string {
  const fsmState = det.fsm_state ?? "idle";
  const trackTag = formatTrackId(det.track_id);
  const isMinimal = !det.in_roi && fsmState === "idle";

  if (isMinimal || !trackTag) {
    return trackTag ? `${trackTag} ${det.class_name}` : det.class_name;
  }

  const carriedItem = det.category === "person" ? findCarriedItem(det, allDetections) : null;
  const action = deriveActionText(det, carriedItem);
  const dwell = det.dwell_time_s != null && det.dwell_time_s > 0 ? `${det.dwell_time_s.toFixed(0)}s` : "";
  const stateTag = FSM_LABELS[fsmState] || "";

  const parts = [trackTag];
  if (det.in_roi) parts.push("ROI");
  parts.push(action);
  if (dwell) parts.push(dwell);
  if (stateTag) parts.push(stateTag);

  return parts.join(" | ");
}
```

- [ ] **Step 2: Update DetectionBox to use enriched labels**

Replace the `primaryLabel` and `secondaryParts` construction in the `DetectionBox` function (lines ~94-109) with:

```typescript
const enrichedLabel = buildEnrichedLabel(detection, allDetections);
```

Pass `allDetections` as a prop to `DetectionBox`:

```typescript
function DetectionBox({ detection, width, height, allDetections }: {
  detection: BBoxDetection; width: number; height: number; allDetections: BBoxDetection[];
}) {
```

Replace the label rendering (Row 1 content) to use `enrichedLabel` instead of `primaryLabel`:

```tsx
{enrichedLabel}
```

Remove the separate FSM badge and ROI badge spans — they're now part of the enriched label string. Keep the REC indicator.

Update the border style to use FSM-based width:

```typescript
const borderWidths: Record<string, number> = {
  idle: 1, enter: 2, observe: 3, violation: 4, alerting: 4, escalated: 4, exited: 1,
};
const borderWidth = borderWidths[fsmState] ?? 2;
```

- [ ] **Step 3: Add stats pills to the overlay**

In the main `MetadataOverlay` component, compute stats and render pills:

```tsx
export default function MetadataOverlay({ metadata, width, height }: MetadataOverlayProps) {
  if (!metadata || !width || !height) return null;

  const detections = metadata.detections ?? [];
  const totalObjects = detections.length;
  const escalatedCount = detections.filter(
    (d) => d.fsm_state === "violation" || d.fsm_state === "alerting" || d.fsm_state === "escalated"
  ).length;

  return (
    <div style={{ position: "absolute", inset: 0, pointerEvents: "none", zIndex: 10 }}>
      {/* Stats pills — top-right */}
      <div style={{
        position: "absolute", top: 8, right: 8, display: "flex", gap: 6, zIndex: 20,
      }}>
        <span style={{
          padding: "2px 8px", borderRadius: 12, fontSize: 11,
          fontFamily: "monospace", backgroundColor: "rgba(0,0,0,0.7)", color: "#fff",
        }}>
          Objects: {totalObjects}
        </span>
        <span style={{
          padding: "2px 8px", borderRadius: 12, fontSize: 11,
          fontFamily: "monospace",
          backgroundColor: escalatedCount > 0 ? "rgba(220,38,38,0.9)" : "rgba(21,128,61,0.8)",
          color: "#fff",
          animation: escalatedCount > 0 ? "violationPulse 1s ease-in-out infinite alternate" : undefined,
        }}>
          Escalated: {escalatedCount}
        </span>
      </div>

      {/* Detection boxes */}
      {detections.map((det, i) => (
        <DetectionBox key={det.track_id ?? i} detection={det} width={width} height={height} allDetections={detections} />
      ))}

      {/* Camera alert banner (existing) */}
      {metadata.camera_alert && (
        <CameraAlertBanner alert={metadata.camera_alert} width={width} />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/camera/MetadataOverlay.tsx
git commit -m "feat(frontend): enriched inline bbox labels with FSM state + stats pills"
```

---

### Task 6: Rebuild Pipeline and Verify End-to-End

**Files:** None (integration test)

- [ ] **Step 1: Rebuild pipeline image**

```bash
docker compose build pipeline
```

- [ ] **Step 2: Restart pipeline**

```bash
docker compose up -d pipeline
```

- [ ] **Step 3: Verify FSM thresholds via Kafka metadata**

```bash
docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic wastewatch.metadata --max-messages 10 --timeout-ms 15000 \
  | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line.strip())
    for det in d.get('detections', []):
        if det.get('fsm_state') and det['fsm_state'] != 'idle':
            print(f'{d[\"camera_id\"]} #{det[\"track_id\"]} {det[\"fsm_state\"]} dwell={det.get(\"dwell_time_s\",0):.1f}s roi={det.get(\"in_roi\")}')
"
```

Expected: tracks transition at 3s (enter), 10s (observe), 15s (violation).

- [ ] **Step 4: Verify OSD labels on annotated stream**

Grab frame from cam-01-out and visually inspect:

```bash
docker compose exec rtsp-streamer ffmpeg -y -rtsp_transport tcp \
  -i rtsp://mediamtx:8554/cam-01-out -frames:v 1 -q:v 2 /tmp/osd_verify.jpg
docker compose cp rtsp-streamer:/tmp/osd_verify.jpg /tmp/osd_verify.jpg
```

Verify: enriched labels visible, stats overlay at top-right.

- [ ] **Step 5: Verify frontend overlay**

Open the frontend in browser and check:
- Enriched inline labels on bbox
- Stats pills at top-right
- Color transitions: blue → amber → red
- Pulse animation on VIOLATION state

- [ ] **Step 6: Commit integration verification**

```bash
git add -A
git commit -m "test: verify OSD activity panel end-to-end"
```
