# OSD Activity Panel — Inline Bbox Labels + Stats Overlay

**Date:** 2026-04-16
**Status:** Draft
**Scope:** Pipeline OSD probe + Frontend MetadataOverlay

---

## Problem

The current per-object label on the annotated video stream shows only `person #42 51%` — the class name, track ID, and confidence. Operators cannot see at a glance whether an object is in an ROI, how long it has dwelled, or what FSM state it is in without checking a separate panel. The narrow-road LSG deployment needs fast visual escalation: 10 seconds of dwell is already suspicious, 15 seconds demands an immediate voice alert.

## Solution

Replace the per-object bbox label with an enriched inline format that shows track ID, ROI flag, action, dwell time, and FSM state. Add a stats overlay at the top-right corner showing total objects and escalated count. Render on both the DeepStream H.264 stream (for NVR/RTSP viewers) and the frontend React overlay (for rich styling and animations).

## FSM Threshold Reconfiguration

Current thresholds are too slow for narrow-road surveillance. Reconfigure:

| Transition | Current | New | Rationale |
|-----------|---------|-----|-----------|
| IDLE → ENTER | 5s | **3s** | Narrow road — 3s standing is already notable |
| ENTER → OBSERVE | 15s | **10s** | 10s of dwell on a narrow road is suspicious |
| OBSERVE → VIOLATION | (same as observe) | **15s** | 15s triggers VLM + voice alert |

**Config changes in `pipeline/app/config.py`:**
```python
dwell_enter_threshold_s: float = 3.0    # was 5.0
dwell_observe_threshold_s: float = 10.0  # was 15.0
```

The OBSERVE → VIOLATION transition already uses `observe_threshold_s` as the lower bound and transitions when dwell exceeds it. The 15s VIOLATION trigger is achieved by the existing FSM logic: ENTER at 3s, OBSERVE at 10s, VIOLATION at 15s (observe_threshold_s + 5s gap handled by the FSM's own check of `dwell_time_s >= observe_threshold_s` which we set to 10s — but VIOLATION needs a separate threshold).

**New field in config:**
```python
dwell_violation_threshold_s: float = 15.0  # new — triggers VIOLATION state
```

**FSM change in `event_fsm.py`:** The OBSERVE → VIOLATION transition currently fires when `dwell_time_s >= observe_threshold_s`. Change it to fire when `dwell_time_s >= violation_threshold_s` (15s). The ENTER → OBSERVE transition fires at `enter_threshold_s` (3s → 10s remap: rename `enter_threshold_s` to keep semantics clear, or adjust the threshold values).

**Final FSM timeline with trigger actions:**
```
0s          3s          10s                        15s
|-- IDLE ---|-- ENTER ---|------ OBSERVE ----------|-- VIOLATION --→
            in_roi=true  ↓ suspicious               ↓ confirmed
                         ├─ START smart recording    ├─ ALERT dispatched
                         ├─ TRIGGER VLM analysis     ├─ Escalation begins
                         └─ TRIGGER voice warning    └─ SIP call (if configured)
                           "Suspicious activity..."    "Violation confirmed..."
```

### Trigger Actions by State

| State | Trigger Actions |
|-------|----------------|
| **ENTER** (3s) | Track begins, bbox label enriched, no recording |
| **OBSERVE** (10s) | Start smart recording, send VLM request, send TTS voice warning ("Suspicious activity detected..."), bbox turns amber |
| **VIOLATION** (15s) | Dispatch confirmed alert (WebSocket + Telegram + DB), bbox turns red pulsing, TTS escalation voice ("Violation confirmed..."), begin SIP escalation cascade |

**Key distinction:** OBSERVE starts the evidence-gathering pipeline (recording + VLM + warning voice). VIOLATION is the confirmed alert that gets dispatched to operators and persisted. This gives the system 5 seconds (10s→15s) of VLM analysis time before the formal alert fires — if VLM clears the activity during OBSERVE, the alert never fires.

### VLM Temporal Diff Analysis

During OBSERVE, the FrameBatcher collects cropped bbox frames from the smart recording. When the batch is sent to VLM (Qwen2.5-VL), the prompt instructs the model to compare frames temporally:

**VLM prompt strategy:**
> "You are analyzing a sequence of frames from a surveillance camera in a restricted zone. Compare the first and last frames carefully. Describe:
> 1. What is the person/vehicle doing? (stopping, waiting, carrying, reaching)
> 2. Is anything new on the ground that wasn't there before? (dumping)
> 3. Is anything MISSING that was there before? (theft/removal — banners, signs, items)
> 4. Did the person leave something behind OR take something away?
> Answer with: DUMPING_CONFIRMED (object left behind), THEFT_DETECTED (object removed/stolen), SUSPICIOUS (unclear activity), or CLEAR (nothing changed)."

The VLM receives cropped bbox frames (not full-frame) so it focuses on the specific tracked object and its immediate surroundings. The temporal sequence (frame 1 at 10s, frame 2 at 12s, frame 3 at 14s) lets it detect the "arrive with item → item on ground → leave without item" pattern that confirms dumping.

**VLM result actions:**
- `DUMPING_CONFIRMED` → FSM proceeds to VIOLATION, confirmed alert fires
- `THEFT_DETECTED` → FSM proceeds to VIOLATION, confirmed alert fires (different alert text)
- `SUSPICIOUS` → FSM proceeds to VIOLATION (err on the side of caution)
- `CLEAR` → FSM resets to IDLE, recording stops, no alert dispatched

## Per-Object Bbox Label Format

### Label String

```
#NNN [ROI] | action | Xs STATE
```

Examples:
- `#042 ROI | carrying cover | 12s OBSERVE`
- `#017 ROI | waiting | 8s ENTER`
- `#003 person` (minimal — not in ROI, IDLE)

### Clutter Reduction Rules

- Objects **not in ROI** AND in **IDLE** state: show minimal label `#NNN classname` (e.g., `#003 person`)
- Objects **in ROI** OR in **ENTER/OBSERVE/VIOLATION** state: show full enriched label
- Track ID always truncated to 3 digits: `track_id % 1000`, prefixed with `#`

### Action Text Derivation

The "action" field in the label is derived from the object's category and movement:

| Condition | Action Text |
|-----------|-------------|
| category=person, dwell < 3s | `moving` |
| category=person, dwell >= 3s | `waiting` |
| category=person + nearby carried_item | `carrying {item}` (e.g., `carrying cover`) |
| category=vehicle, dwell < 3s | `passing` |
| category=vehicle, dwell >= 3s | `stopped` |
| category=waste_object | `dumped {class}` (e.g., `dumped bottle`) |

"Nearby carried_item" is determined by bbox overlap: if a carried_item bbox overlaps >50% with a person bbox in the same frame, the person's action becomes `carrying {item_class}`.

### Color Coding

**DeepStream OSD (text_params):**

| FSM State | Text Color | Background |
|-----------|-----------|------------|
| IDLE | Grey (0.75, 0.75, 0.75) | Black 55% alpha |
| ENTER | Blue (0.20, 0.85, 1.00) | Black 55% alpha |
| OBSERVE | White (1.0, 1.0, 1.0) | Amber (0.95, 0.60, 0.05) 80% alpha |
| VIOLATION | White (1.0, 1.0, 1.0) | Red (0.90, 0.15, 0.15) 80% alpha |

**DeepStream OSD (rect_params border):**

| FSM State | Border Width | Border Color |
|-----------|-------------|--------------|
| IDLE | 2 | Grey |
| ENTER | 2 | Blue |
| OBSERVE | 3 | Amber |
| VIOLATION | 4 | Red |

**Frontend CSS (MetadataOverlay.tsx):**

| FSM State | Badge Style | Border | Animation |
|-----------|------------|--------|-----------|
| IDLE | `text-gray-400 bg-transparent` | `border border-gray-600` | none |
| ENTER | `text-blue-300 bg-black/60` | `border-2 border-blue-500` | none |
| OBSERVE | `text-white bg-amber-600/80` | `border-[3px] border-amber-500` | subtle glow (`shadow-amber-500/50`) |
| VIOLATION | `text-white bg-red-600/90` | `border-4 border-red-500` | pulsing red (`animate-pulse`, `shadow-red-500/70`) |

## Top-Right Stats Overlay

### DeepStream OSD

A `NvDsDisplayMeta` text element at the top-right of each frame:

```
Objects: 7 | Escalated: 2
```

- Position: `(frame_width - 360, 20)` — same X as the existing action overlay, but that overlay moves to top-center or is replaced by this combined line
- Background: green `(0.10, 0.45, 0.15)` when escalated=0, red `(0.80, 0.10, 0.10)` when escalated>0
- Font: Serif 14pt, white text

**Replace the existing top-right action overlay** (`CLEAR`, `DUMPING SUSPECTED`, etc.) with this stats line. The per-object FSM state labels make the old action overlay redundant.

### Frontend

A pill badge at the top-right corner of the video container:

```tsx
<div className="absolute top-2 right-2 z-20 flex gap-2">
  <span className="px-2 py-1 rounded-full text-xs font-mono bg-black/70 text-white">
    Objects: {totalObjects}
  </span>
  <span className={cn(
    "px-2 py-1 rounded-full text-xs font-mono",
    escalatedCount > 0
      ? "bg-red-600/90 text-white animate-pulse"
      : "bg-green-700/80 text-white"
  )}>
    Escalated: {escalatedCount}
  </span>
</div>
```

`totalObjects` = detections in current frame with `class_id` in `COCO_CLASS_MAP`.
`escalatedCount` = detections where `fsm_state` is `violation`, `alerting`, or `escalated`.

## Data Flow

```
Pipeline (decorate_for_osd probe on nvdsosd.sink)
  ├─ Iterates obj_meta_list
  ├─ For each object:
  │   ├─ Reads class_id, track_id, confidence, rect_params
  │   ├─ Checks COCO_CLASS_MAP for class name + category
  │   ├─ Checks carried_item overlap (person+item bbox overlap)
  │   ├─ Builds enriched label string
  │   ├─ Sets text_params with label + FSM-colored background
  │   └─ Sets rect_params border width + color per FSM state
  ├─ Counts totalObjects and escalatedCount
  └─ Writes NvDsDisplayMeta with stats overlay text

Pipeline (extract_metadata probe on fakesink.sink)
  ├─ Already sends per-detection: track_id, in_roi, fsm_state, dwell_time_s, label, category
  ├─ No changes needed — all fields already present in Kafka metadata
  └─ Kafka wastewatch.metadata → Backend → WebSocket → Frontend

Frontend (MetadataOverlay.tsx)
  ├─ Reads detection array from useDetectionMetadata() WebSocket hook
  ├─ For each detection with in_roi=true OR fsm_state != idle:
  │   ├─ Renders enriched inline label badge above/below bbox
  │   └─ Applies FSM-state CSS class for color + animation
  ├─ Computes totalObjects and escalatedCount from detection array
  └─ Renders top-right stats pills
```

## Carried-Item Association (Person + Item Overlap)

To show "carrying cover" in the label, the `decorate_for_osd` probe needs to associate carried_item detections with nearby person detections.

**Algorithm (in `decorate_for_osd`):**
1. First pass: collect all carried_item obj_metas with their bboxes
2. Second pass: for each person obj_meta, check if any carried_item bbox overlaps >50% (IoU or containment)
3. If match: set person's action to `carrying {item_class}`
4. Mark the carried_item as "associated" so it doesn't get its own enriched label

**Frontend equivalent:** Same logic in `MetadataOverlay.tsx` using the detection array from WebSocket. Compare bbox overlap between person and carried_item detections.

## Files Changed

| File | Change | Complexity |
|------|--------|-----------|
| `pipeline/app/config.py` | Add `dwell_violation_threshold_s=15.0`, change `dwell_enter_threshold_s=3.0`, `dwell_observe_threshold_s=10.0` | Low |
| `pipeline/app/event_fsm.py` | Use `violation_threshold_s` for OBSERVE→VIOLATION transition | Low |
| `pipeline/app/pipeline.py` (`decorate_for_osd`) | Rewrite label format, add carried-item association, add stats overlay, remove old action overlay | Medium |
| `pipeline/app/pipeline.py` (`extract_metadata`) | On OBSERVE transition: trigger smart recording + VLM request + TTS warning. On VIOLATION transition: trigger confirmed alert dispatch. Move VLM/TTS triggers from VIOLATION to OBSERVE. | Medium |
| `pipeline/app/violation_dispatcher.py` | Split into two paths: `on_observe()` (recording + VLM + warning TTS) and `on_violation()` (confirmed alert + escalation TTS) | Medium |
| `frontend/src/components/camera/MetadataOverlay.tsx` | Enriched inline labels, stats pills, FSM-state CSS classes, pulse animations, carried-item association | Medium |

## Evidence Lifecycle

Every OBSERVE state entry starts evidence collection. The outcome (CLEAR vs VIOLATION) determines what gets kept.

### Evidence Outcomes

| VLM Result | FSM Outcome | Recording | Keyframe | JSON Metadata | Stored Where |
|-----------|-------------|-----------|----------|---------------|-------------|
| CLEAR | Reset to IDLE | **Deleted** | Save 1 keyframe (JPEG) | Save JSON | `evidence/suspicious/` |
| SUSPICIOUS | Proceeds to VIOLATION | **Kept** (MP4) | Save 1 keyframe | Save JSON | `evidence/violations/` |
| DUMPING_CONFIRMED | Proceeds to VIOLATION | **Kept** (MP4) | Save 1 keyframe | Save JSON | `evidence/violations/` |
| Timeout (no VLM response) | Proceeds to VIOLATION | **Kept** (MP4) | Save 1 keyframe | Save JSON | `evidence/violations/` |

### Evidence JSON Schema

```json
{
  "event_id": "uuid",
  "camera_id": "cam-01",
  "track_id": 42,
  "timestamp_start": "2026-04-16T10:30:00Z",
  "timestamp_end": "2026-04-16T10:30:18Z",
  "duration_s": 18,
  "outcome": "violation | suspicious | clear",
  "fsm_states": ["enter", "observe", "violation"],
  "class_name": "person",
  "action": "carrying cover",
  "dwell_time_s": 18.0,
  "in_roi": true,
  "roi_name": "Restricted Zone",
  "vlm_result": {
    "verdict": "DUMPING_CONFIRMED",
    "confidence": 0.87,
    "waste_type": "plastic bag",
    "description": "Person arrived carrying white plastic bag, placed it on ground near wall, walked away without it"
  },
  "bbox_path": [
    {"t": 0, "x": 0.3, "y": 0.4, "w": 0.1, "h": 0.2},
    {"t": 5, "x": 0.32, "y": 0.42, "w": 0.1, "h": 0.2}
  ],
  "keyframe_url": "/evidence/violations/cam-01/2026-04-16/evt_abc123_keyframe.jpg",
  "video_url": "/evidence/violations/cam-01/2026-04-16/evt_abc123.mp4",
  "tts_sent": true,
  "alert_dispatched": true
}
```

### Storage Rules

- **Suspicious (CLEAR):** JSON + 1 keyframe JPEG only. No video. Lightweight — queryable for analytics but doesn't consume disk.
- **Violations (CONFIRMED/SUSPICIOUS/TIMEOUT):** JSON + keyframe JPEG + full MP4 clip.
- **Retention:** Both suspicious and violation evidence auto-rotated after **7 days** (configurable via `WW_EVIDENCE_RETENTION_DAYS`). Rotation runs hourly from the pipeline health check, deleting date-stamped directories older than the cutoff.
- **Directory structure:** `evidence/{outcome}/{camera_id}/{date}/evt_{event_id}_*`

### Pipeline Integration

The smart recording is already implemented via NvDsSR (DeepStream Smart Record). Changes needed:

1. **Start recording on OBSERVE** (currently starts on VIOLATION) — move the `NvDsSR` start trigger to the OBSERVE FSM transition
2. **Stop recording on outcome:** VLM CLEAR → stop + delete MP4 + save keyframe + save JSON. VLM CONFIRMED → stop + keep MP4 + save keyframe + save JSON.
3. **Keyframe capture:** Extract the frame with highest detection confidence during the event window as the keyframe JPEG
4. **Evidence writer:** New component `EvidenceWriter` that receives the outcome event and writes JSON + manages file retention

## Timeline Playback & Event History (Separate Spec)

The timeline/history UI with search, notifications, export, and analytics is a substantial frontend + backend feature that deserves its own design spec. It consumes the evidence JSON/video produced by this spec.

**Tracked as item #8:** Timeline Playback & Event History — includes:
- Searchable event timeline (filter by camera, date, outcome, class)
- Video playback for violation events (MP4 clips)
- Keyframe gallery for suspicious events
- Notification preferences (which events trigger push/email)
- CSV/PDF export of event history
- Analytics dashboard (events per day, per camera, per class, heatmap)

This spec produces the data; the timeline spec consumes it.

## Out of Scope

- Alert UI red blinking borders (separate item #2)
- ROI editor fixes (separate item #3)
- VLM → TTS voice alerts (separate item #4)
- Dashboard health panel (separate item #6)
- Google Maps camera location (separate item #7)
- Timeline Playback & Event History UI (separate item #8 — consumes evidence from this spec)

## Success Criteria

1. Every tracked object on the annotated stream shows `#NNN ROI | action | Xs STATE` when in ROI or non-IDLE
2. Objects not in ROI and IDLE show minimal `#NNN classname` label
3. Bbox border color and width changes with FSM state (blue→amber→red)
4. Top-right stats show `Objects: N | Escalated: M` with red pulse when M>0
5. Frontend overlay matches OSD labels with richer styling and animations
6. FSM transitions at 3s (ENTER), 10s (OBSERVE), 15s (VIOLATION)
7. Person carrying an item shows `carrying {item}` in the action field
8. OBSERVE state (10s) triggers: smart recording start + VLM analysis request + TTS voice warning
9. VIOLATION state (15s) triggers: confirmed alert dispatch + escalation TTS + operator notification
10. If VLM clears during OBSERVE window (10-15s), alert does NOT fire — recording stops, FSM resets
