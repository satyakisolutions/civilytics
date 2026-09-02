# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""WasteWatch DeepStream pipeline.

Reads RTSP streams from MediaMTX, runs RF-DETR object detection via nvinfer,
re-streams annotated video back to MediaMTX over RTP/UDP, and publishes
per-frame detection metadata to Kafka.

Environment variables (all prefixed WW_):
    WW_SOURCES            Comma-separated list of camera_id=rtsp://... pairs.
                          Example: "cam-01=rtsp://mediamtx:8554/cam-01,cam-02=rtsp://..."
    WW_KAFKA_BOOTSTRAP_SERVERS
    WW_MEDIAMTX_RTSP_URL  Base RTSP URL for the MediaMTX server (default rtsp://mediamtx:8554)
    WW_BATCH_SIZE         nvinfer batch size (default 4)
    WW_FRAME_SKIP         nvinfer inference interval (default 2)
    WW_MUXER_OUTPUT_WIDTH / WW_MUXER_OUTPUT_HEIGHT
"""

import base64
import logging
import os
import sys
import math
import threading
import time
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402

import pyds  # noqa: E402 — provided by the DeepStream Docker image

from app.command_receiver import CommandHandler, CommandReceiver
from app.config import pipeline_settings
from app.connection_monitor import ConnectionMonitor
from app.event_fsm import EventFSMManager, FSMState
from app.frame_batcher import FrameBatcher
from app.health import CameraHealth
from app.kafka_producer import PipelineKafkaProducer
from app.output_manager import OutputStreamManager
from app.preprocessor import ROIPreprocessor
from app.roi import ROIManager
from app.source_manager import SourceManager
from app.tracking import TrackStateManager
from app.vlm_grpc_client import VLMGrpcClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Relevant COCO classes for waste dumping surveillance.
# COCO 91-index class mapping. RF-DETR's DeepStream output head
# (scripts/onnx_to_engine.py::append_rfdetr_deepstream_head) emits
# class_id in the COCO full index range where 0 = background and
# person = 1. Previous version of this file used COCO80 indices
# (person = 0) which silently filtered every real detection.
# Keep in lock-step with pipeline/configs/labels.txt.
BBOX_CLASS_MAP: dict[int, str] = {
    # Fine-tuned 2-class RFDETR-Nano indices (see training/finetune_rfdetr.py).
    # Training used 0=person, 1=handbag — DeepStreamOutput argmax returns
    # those indices directly, and labels.txt is now `person\nhandbag` in
    # lock-step. Old COCO91 map caused handbags to be labeled "person" and
    # persons to be dropped entirely, so all downstream FSM transitions
    # keyed off the wrong class.
    0: "person",
    1: "handbag",
}

# RF-DETR-Seg-Nano 3-class indices (post class-0-background-drop, see
# nvdsinfer_custom_impl_Seg/nvdsparseseg_RfdetrSeg.cpp and
# pipeline/configs/labels_seg.txt / nvinfer_rfdetr_seg.txt). Selected
# instead of BBOX_CLASS_MAP when WW_USE_SEG_MODEL=1.
SEG_CLASS_MAP: dict[int, str] = {
    0: "bag",
    1: "person",
    2: "waste",
}

COCO_CLASS_MAP: dict[int, str] = (
    SEG_CLASS_MAP if os.environ.get("WW_USE_SEG_MODEL", "0") == "1" else BBOX_CLASS_MAP
)

# Per-category OSD colour (r, g, b, a) used by the pre-nvosd decoration
# probe. Keeps the rendered bbox colour semantically meaningful so an
# operator can distinguish people / vehicles / carried items / waste at
# a glance without reading the label text.
OSD_CATEGORY_COLOURS: dict[str, tuple[float, float, float, float]] = {
    "person":       (0.20, 0.85, 1.00, 1.0),  # cyan
    "vehicle":      (1.00, 0.72, 0.10, 1.0),  # amber (Satyaki brand)
    "carried_item": (0.95, 0.40, 0.90, 1.0),  # magenta
    "waste_object": (1.00, 0.25, 0.25, 1.0),  # red
    "unknown":      (0.75, 0.75, 0.75, 1.0),  # grey
}

# Category mapping for detection enrichment.
CLASS_CATEGORIES: dict[str, str] = {
    "person": "person",
    "bicycle": "vehicle", "car": "vehicle", "motorcycle": "vehicle",
    "bus": "vehicle", "truck": "vehicle",
    "backpack": "carried_item", "umbrella": "carried_item",
    "handbag": "carried_item", "suitcase": "carried_item",
    # RF-DETR-Seg-Nano (WW_USE_SEG_MODEL=1) classes: "bag" is the seg
    # model's carried-item equivalent of the bbox model's "handbag";
    # "waste" is a directly-detected dumped-waste object (new — the bbox
    # model had no equivalent class, only inferred waste_object COCO
    # furniture/appliance classes below).
    "bag": "carried_item",
    "waste": "waste_object",
    "bottle": "waste_object", "wine_glass": "waste_object", "cup": "waste_object",
    "bowl": "waste_object",
    "chair": "waste_object", "couch": "waste_object",
    "potted_plant": "waste_object", "bed": "waste_object", "dining_table": "waste_object",
    "toilet": "waste_object", "tv": "waste_object", "microwave": "waste_object",
    "oven": "waste_object", "sink": "waste_object", "refrigerator": "waste_object",
}

# Vehicle classes that trigger LPR.
VEHICLE_CLASSES = {"bicycle", "car", "motorcycle", "bus", "truck"}

# Waste objects that serve as direct evidence of dumping.
WASTE_EVIDENCE_CLASSES = {
    "bottle", "chair", "couch", "potted_plant", "bed",
    "dining_table", "toilet", "tv", "microwave", "oven",
    "sink", "refrigerator",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_element(factory_name: str, name: str) -> Gst.Element:
    """Create a GStreamer element; exit the process if the factory is missing."""
    elem = Gst.ElementFactory.make(factory_name, name)
    if elem is None:
        logger.critical("Could not create GStreamer element '%s' (factory: %s)", name, factory_name)
        sys.exit(1)
    return elem


# ---------------------------------------------------------------------------
# Per-camera alert flag derivation
# ---------------------------------------------------------------------------

# Alert levels in priority order (highest wins)
_ALERT_PRIORITY = {
    "escalated": 4,
    "alerting": 3,
    "violation": 2,
    "observe": 1,
    "enter": 0,
}

_ALERT_CONFIG: dict[str, dict] = {
    "escalated": {"level": "critical",    "color": "#dc2626", "text": "ESCALATED — Supervisor notified"},
    "alerting":  {"level": "critical",    "color": "#ef4444", "text": "VIOLATION — Alert dispatched"},
    "violation": {"level": "violation",   "color": "#ef4444", "text": "VIOLATION DETECTED"},
    "observe":   {"level": "suspicious",  "color": "#f59e0b", "text": "Suspicious activity — monitoring"},
    "enter":     {"level": "monitoring",  "color": "#3b82f6", "text": "Object entered zone"},
}


_ALERT_SNAPSHOT_DIR = os.environ.get("WW_EVIDENCE_DIR", "/app/evidence") + "/alerts"


def _alert_snapshot_url(camera_id: str, track_id: int, ts_ms: int) -> str:
    """Deterministic URL/filename for an alert snapshot -- pure string
    formatting, no I/O. Split out from the actual disk write so the
    message payload (Kafka publish) never has to wait on JPEG write
    latency to know the URL it should carry.
    """
    return f"/evidence/alerts/{camera_id}/alert_{track_id}_{ts_ms}.jpg"


def _write_alert_snapshot(camera_id: str, track_id: int, ts_ms: int, jpeg_bytes: bytes | None) -> None:
    """Write the JPEG for a fired alert to the path _alert_snapshot_url
    already predicted (StaticFiles mounted at "/evidence" ->
    backend/app/main.py). The async alert worker writes this before publishing
    so downstream consumers cannot race the file and receive a transient 404.
    """
    if not jpeg_bytes or len(jpeg_bytes) < 1000:
        return
    try:
        subdir = Path(_ALERT_SNAPSHOT_DIR) / camera_id
        subdir.mkdir(parents=True, exist_ok=True)
        fname = f"alert_{track_id}_{ts_ms}.jpg"
        (subdir / fname).write_bytes(jpeg_bytes)
    except Exception:
        logger.exception("Failed to save alert snapshot for %s track=%d", camera_id, track_id)


def _fire_waste_alert_async(
    kafka_producer, camera_id: str, track_id: int, jpeg_bytes: bytes | None,
    severity: str, title: str, message: str, dumped_by_track_id: int | None,
    dwell_time_s: float, bbox: dict, detected_at: float,
) -> None:
    """Write the snapshot and publish the alert off the GStreamer pad probe.

    Both used to run directly inside extract_metadata(), the pad-probe
    callback on the pipeline's real-time frame-processing path -- any
    stall there (slow disk, Kafka backpressure) delays the *next* frame,
    producing "wrong person / wrong time" symptoms downstream (the whole
    pipeline lags behind live video, so later frames' metadata no longer
    lines up with when the event actually happened). `detected_at` is
    captured on the pad-probe thread BEFORE handing off, so the alert's
    timestamp reflects the real detection moment regardless of how long
    this background thread takes to finish.

    The URL is deterministic (track_id + timestamp, no I/O needed to know it).
    The worker persists the JPEG first, then publishes that URL, preventing
    Telegram/dashboard consumers from fetching an image that is not yet
    available without stalling real-time frame processing.
    """
    has_snapshot = bool(jpeg_bytes) and len(jpeg_bytes) >= 1000
    ts_ms = int(detected_at * 1000)
    snap_url = _alert_snapshot_url(camera_id, track_id, ts_ms) if has_snapshot else None

    def _run():
        try:
            # Persist first so Telegram cannot race the API and receive a
            # transient 404 while trying to fetch the just-published URL.
            if has_snapshot:
                _write_alert_snapshot(camera_id, track_id, ts_ms, jpeg_bytes)
            kafka_producer.send_alert(
                camera_id=camera_id,
                track_id=track_id,
                severity=severity,
                title=title,
                message=message,
                dumped_by_track_id=dumped_by_track_id,
                dwell_time_s=round(dwell_time_s, 1),
                bbox=bbox,
                snapshot_url=snap_url,
                detected_at=detected_at,
            )
        except Exception:
            logger.exception("Async waste alert fire failed for %s track=%d", camera_id, track_id)

    threading.Thread(target=_run, daemon=True).start()


def _derive_camera_alert(camera_id: str, detections: list[dict]) -> dict | None:
    """Derive a per-camera alert flag from the worst FSM state in this frame.

    Each camera gets a unique alert banner based on the highest-priority
    detection across all tracked objects in that camera's frame.
    """
    worst_state = None
    worst_priority = -1
    worst_class = ""
    worst_dwell = 0.0

    for det in detections:
        fsm = det.get("fsm_state")
        if fsm and fsm in _ALERT_PRIORITY:
            prio = _ALERT_PRIORITY[fsm]
            if prio > worst_priority:
                worst_priority = prio
                worst_state = fsm
                worst_class = det.get("label", det.get("class_name", ""))
                worst_dwell = det.get("dwell_time_s", 0.0)

    if worst_state is None:
        return None

    cfg = _ALERT_CONFIG[worst_state]

    # Build contextual alert text unique to this camera
    text = cfg["text"]
    if worst_class:
        text = f"{text} — {worst_class}"
    if worst_dwell > 0:
        text = f"{text} ({worst_dwell:.0f}s)"

    return {
        "level": cfg["level"],
        "text": text,
        "color": cfg["color"],
        "since": int(time.monotonic() * 1000),
    }


# ---------------------------------------------------------------------------
# Pad probe — OSD decoration (runs BEFORE nvdsosd)
# ---------------------------------------------------------------------------

# FSM state -> OSD label suffix
_STATE_LABEL: dict[str, str] = {
    "enter": "ENTER",
    "observe": "OBSERVE",
    "violation": "ALERT",
    "alerting": "ALERT",
    "escalated": "ESCALATED",
}

# FSM state -> border width
_STATE_BORDER_WIDTH: dict[str, int] = {
    "idle": 2, "enter": 2, "observe": 3,
    "violation": 4, "alerting": 4, "escalated": 4,
}

# FSM state -> border colour (r, g, b, a)
_STATE_BORDER_COLOUR: dict[str, tuple[float, float, float, float]] = {
    "idle":      (0.55, 0.55, 0.55, 1.0),  # grey
    "enter":     (0.20, 0.50, 1.00, 1.0),  # blue
    "observe":   (1.00, 0.72, 0.10, 1.0),  # amber
    "violation": (1.00, 0.20, 0.20, 1.0),  # red
    "alerting":  (1.00, 0.20, 0.20, 1.0),  # red
    "escalated": (1.00, 0.20, 0.20, 1.0),  # red
}

# FSM state -> text background colour (r, g, b, a)
_STATE_TEXT_BG: dict[str, tuple[float, float, float, float]] = {
    "idle":      (0.0, 0.0, 0.0, 0.55),
    "enter":     (0.0, 0.0, 0.0, 0.55),
    "observe":   (0.80, 0.58, 0.08, 0.80),   # amber bg
    "violation": (0.80, 0.10, 0.10, 0.80),   # red bg
    "alerting":  (0.80, 0.10, 0.10, 0.80),
    "escalated": (0.80, 0.10, 0.10, 0.80),
}


def _derive_action_text(class_name: str, category: str, dwell_time_s: float, carried_item: str | None, fsm_state: str = "idle") -> str:
    """Return a short human-readable action string for the OSD label."""
    if carried_item:
        return f"carrying {carried_item}"
    if category == "waste_object":
        return f"dumped {class_name}"
    if category == "vehicle":
        if dwell_time_s >= 10.0:
            return "suspicious stop"
        return "stopped" if dwell_time_s >= 3.0 else "passing"
    if category == "person":
        if dwell_time_s >= 10.0:
            return "loitering"
        return "waiting" if dwell_time_s >= 3.0 else "moving"
    return class_name


def _find_carried_item(person_rect, carried_items: list[tuple]) -> str | None:
    """Return the name of any carried item whose bbox overlaps >50% with person_rect."""
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
        ox1, oy1 = max(px1, ix1), max(py1, iy1)
        ox2, oy2 = min(px2, ix2), min(py2, iy2)
        if ox2 <= ox1 or oy2 <= oy1:
            continue
        overlap = (ox2 - ox1) * (oy2 - oy1)
        item_area = item_rect.width * item_rect.height
        if item_area > 0 and overlap / item_area > 0.5:
            return item_name
    return None


def decorate_for_osd(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    ctx: dict,
) -> Gst.PadProbeReturn:
    """Populate every NvDsObjectMeta with enriched bbox + label text.

    Labels show FSM-aware enriched format when fsm_manager/track_manager
    are available:  ``#042 ROI | waiting | 12s OBSERVE``
    Falls back to minimal ``#042 person`` when managers are None (native
    C plugin path).

    Also renders a top-right stats overlay: ``Objects: N | Escalated: M``
    with green background when escalated == 0, red when > 0.
    """
    buf = info.get_buffer()
    if buf is None:
        return Gst.PadProbeReturn.OK
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    if batch_meta is None:
        return Gst.PadProbeReturn.OK

    # Extract managers from context (may be None for native C plugin path)
    ctx = ctx or {}
    fsm_mgr = ctx.get("fsm_manager")
    track_mgr = ctx.get("track_manager")
    roi_mgr = ctx.get("roi_manager")
    _osd_sm = ctx.get("source_manager")
    _osd_pad_map = _osd_sm.pad_to_camera if _osd_sm is not None else {}
    _waste_pairs = ctx.get("waste_attribution_pair", {})

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        camera_id = _osd_pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")

        # --- First pass: collect carried items + person positions --------------
        # Person centers are gathered here so the second pass can attribute
        # a dumped waste object to the nearest currently-tracked person
        # without a third traversal.
        carried_items: list[tuple[str, object]] = []
        person_centers: list[tuple[int, float, float]] = []  # (track_id, cx, cy)
        obj_meta_list = frame_meta.obj_meta_list
        while obj_meta_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
            except StopIteration:
                break
            cid = int(obj_meta.class_id)
            cname = COCO_CLASS_MAP.get(cid)
            if cname is not None and CLASS_CATEGORIES.get(cname) == "carried_item":
                carried_items.append((cname, obj_meta.rect_params))
            elif cname == "person":
                _pt = int(obj_meta.object_id)
                if _pt != 0xFFFFFFFFFFFFFFFF:
                    _pr = obj_meta.rect_params
                    person_centers.append((_pt, _pr.left + _pr.width / 2.0, _pr.top + _pr.height / 2.0))
            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        # --- Second pass: decorate each object ---------------------------------
        total_objects = 0
        escalated_count = 0
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

            category = CLASS_CATEGORIES.get(cname, "unknown")
            total_objects += 1

            track_id = int(obj_meta.object_id)
            track_id_short = track_id % 1000
            rect = obj_meta.rect_params

            # Look up FSM / track state (graceful when managers are None)
            fsm_state = "idle"
            dwell_time_s = 0.0
            in_roi = False
            if fsm_mgr is not None and track_id != 0xFFFFFFFFFFFFFFFF:
                fsm_state = fsm_mgr.get_fsm_state(camera_id, track_id) or "idle"
            if track_mgr is not None and track_id != 0xFFFFFFFFFFFFFFFF:
                track_info = track_mgr.get_track(camera_id, track_id)
                if track_info is not None:
                    dwell_time_s = track_info.dwell_time_s
                    in_roi = track_info.in_roi

            if fsm_state in ("escalated", "alerting"):
                escalated_count += 1

            # --- Check suspect flag (escalated track outside ROI) ---
            is_suspect = False
            if fsm_mgr and track_id != 0xFFFFFFFFFFFFFFFF:
                is_suspect = fsm_mgr.is_escalated(camera_id, track_id)

            # As soon as attribution connects a person and waste object,
            # recolour only the waste object's existing NvOSD box. The person
            # intentionally stays in their normal colour during the 500ms
            # placement-confirmation window; force_violation() marks them red
            # only after that timer completes. No second rectangle is drawn.
            _is_visual_pair = False
            if track_id != 0xFFFFFFFFFFFFFFFF:
                for (_pair_camera, _pair_waste_id), _pair in tuple(_waste_pairs.items()):
                    if _pair_camera != camera_id:
                        continue
                    if track_id == _pair_waste_id:
                        _is_visual_pair = True
                        break

            # --- Build label text ---
            if track_id == 0xFFFFFFFFFFFFFFFF:
                label = cname
            elif is_suspect or _is_visual_pair:
                # A confirmed dumper's persistent violation marker overrides
                # transient ROI states such as ENTER/OBSERVE. Keep the linked
                # person red and explicitly labelled VIOLATED even after they
                # cross out of the ROI.
                if category == "waste_object":
                    label = f"#{track_id_short:03d} {cname} | WASTE VIOLATION"
                else:
                    label = f"#{track_id_short:03d} {cname} | VIOLATED"
                fsm_state = "violation"  # Force red styling
            elif fsm_state == "idle" and not in_roi:
                # Minimal label for idle objects outside ROI
                label = f"#{track_id_short:03d} {cname}"
            else:
                # Enriched label: #042 ROI | waiting | 12s OBSERVE
                carried = _find_carried_item(rect, carried_items) if category == "person" else None
                action = _derive_action_text(cname, category, dwell_time_s, carried)
                state_label = _STATE_LABEL.get(fsm_state, "")
                dwell_int = int(dwell_time_s)
                parts = [f"#{track_id_short:03d}"]
                if in_roi:
                    parts.append("ROI")
                parts.append(action)
                if dwell_int > 0:
                    parts.append(f"{dwell_int}s")
                if state_label:
                    parts.append(state_label)
                label = " | ".join(parts[:2]) + " | " + " | ".join(parts[2:]) if len(parts) > 2 else " | ".join(parts)

            # --- Border ---
            border_w = _STATE_BORDER_WIDTH.get(fsm_state, 2)
            br, bg, bb, ba = _STATE_BORDER_COLOUR.get(fsm_state, _STATE_BORDER_COLOUR["idle"])
            rect.border_width = border_w
            rect.border_color.red = br
            rect.border_color.green = bg
            rect.border_color.blue = bb
            rect.border_color.alpha = ba
            rect.has_bg_color = 0

            # --- Dumped item / waste zone highlight ---
            # Fill the bbox with semi-transparent red for:
            #  (a) any waste_object (direct evidence of dumping)
            #  (b) a carried_item belonging to a suspect / escalated track
            # so the operator sees WHAT was dumped, not just the person.
            _highlight = False
            if category == "waste_object":
                _highlight = True
            elif category == "carried_item" and (is_suspect or fsm_state in ("violation", "alerting", "escalated")):
                _highlight = True
            if _highlight:
                # Confirmed/escalated tracks (violation, alerting, escalated,
                # or already flagged suspect) get a SOLID highlight instead
                # of blinking. This is deliberate, not just cosmetic: the
                # evidence keyframe saved for timeline playback
                # (evidence_writer.capture_violation_keyframe) is a single
                # still frame grabbed from this same OSD-rendered buffer --
                # if the highlight were still blinking, roughly half of all
                # saved evidence frames would happen to land on the "off"
                # phase and show the dumped item with no highlight at all.
                # Blink is reserved for the pending/not-yet-confirmed state,
                # where "flickering to grab attention" is the goal; once
                # it's confirmed, the highlight needs to reliably show up in
                # whatever single frame gets persisted as evidence.
                _confirmed = is_suspect or fsm_state in ("violation", "alerting", "escalated")
                _blink_on = _confirmed or (int(time.time() * 2) % 2 == 0)
                rect.has_bg_color = 1 if _blink_on else 0
                rect.bg_color.red = 1.0
                rect.bg_color.green = 0.15
                rect.bg_color.blue = 0.15
                rect.bg_color.alpha = 0.5 if _confirmed else (0.45 if _blink_on else 0.0)
                rect.border_width = max(border_w, 3) if _blink_on else max(border_w, 1)
                if _blink_on:
                    rect.border_color.red = 1.0
                    rect.border_color.green = 0.0
                    rect.border_color.blue = 0.0
                    rect.border_color.alpha = 1.0

            # NOTE: waste dwell -> dumper attribution + alert lives in
            # extract_metadata() below (the "suspicious proximity" and
            # "waste dumping confirmed" blocks), not here. A duplicate
            # threshold-based alert used to also fire from this OSD
            # decoration function (ctx["waste_alert_fired"], severity
            # always "warning", no snapshot attached) -- it ran earlier in
            # the per-buffer processing order than extract_metadata, so it
            # always won the race and fired first, meaning every waste
            # alert silently lacked a snapshot_url and duplicated whatever
            # extract_metadata's FSM-driven alert produced a moment later.
            # Removed; extract_metadata's version is the complete
            # implementation (per-camera configurable thresholds, FSM
            # state integration, one-shot guard via fsm_manager, and a
            # real snapshot saved via _save_alert_snapshot).

            # --- Text params ---
            txt = obj_meta.text_params
            txt.display_text = label
            txt.x_offset = max(0, int(rect.left))
            txt.y_offset = max(0, int(rect.top) - 18)
            txt.font_params.font_name = "Serif"
            txt.font_params.font_size = 11
            txt.font_params.font_color.red = 1.0
            txt.font_params.font_color.green = 1.0
            txt.font_params.font_color.blue = 1.0
            txt.font_params.font_color.alpha = 1.0
            txt.set_bg_clr = 1
            tbr, tbg, tbb, tba = _STATE_TEXT_BG.get(fsm_state, _STATE_TEXT_BG["idle"])
            txt.text_bg_clr.red = tbr
            txt.text_bg_clr.green = tbg
            txt.text_bg_clr.blue = tbb
            txt.text_bg_clr.alpha = tba

            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        # --- Top-right stats overlay -------------------------------------------
        frame_w = frame_meta.source_frame_width or pipeline_settings.input_width
        stats_text = f"Objects: {total_objects} | Escalated: {escalated_count}"
        try:
            disp_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
            disp_meta.num_labels = 1
            tp = disp_meta.text_params[0]
            tp.display_text = stats_text
            tp.x_offset = max(10, int(frame_w) - 360)
            tp.y_offset = 20
            tp.font_params.font_name = "Serif"
            tp.font_params.font_size = 14
            tp.font_params.font_color.red = 1.0
            tp.font_params.font_color.green = 1.0
            tp.font_params.font_color.blue = 1.0
            tp.font_params.font_color.alpha = 1.0
            tp.set_bg_clr = 1
            if escalated_count == 0:
                tp.text_bg_clr.red, tp.text_bg_clr.green, tp.text_bg_clr.blue = 0.10, 0.45, 0.15
            else:
                tp.text_bg_clr.red, tp.text_bg_clr.green, tp.text_bg_clr.blue = 0.80, 0.10, 0.10
            tp.text_bg_clr.alpha = 0.70
            pyds.nvds_add_display_meta_to_frame(frame_meta, disp_meta)
        except Exception:
            pass

        # ROI bounding rectangle overlay removed -- baked into every stream,
        # recording, and saved evidence snapshot (runs pre-nvosd), showing
        # an unwanted cyan "ROI: name" box on all output images.

        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK


# ---------------------------------------------------------------------------
# Pad probe — metadata extraction (Python fallback)
# ---------------------------------------------------------------------------



# Frame capture removed — DeepStream fakesink buffer is NV12 which
# can't be mapped via get_nvds_buf_surface (requires RGBA). Adding
# nvvideoconvert causes segfaults. Instead, the VLM worker grabs
# the frame directly from the RTSP stream when it needs one.


_CAPTURE_DIAG = {"n": 0, "ok": 0, "no_batch": 0, "no_map": 0, "sz_mismatch": 0, "last_err": ""}

# Min gap between JPEG re-encodes per source_id. This probe used to encode
# every single frame (RGBA->numpy->PIL resize->JPEG, pure CPU, no GPU
# accel) just to keep a cache warm in case an alert fires -- that's a lot
# of steady-state CPU/GIL contention spent on frames that are thrown away
# unused. Alert freshness only needs sub-second granularity (the leaky=1
# queue fix already guarantees "the latest frame that got encoded", not
# "every frame"), so throttling the encode itself to this interval cuts
# CPU roughly proportional to source FPS / this interval while keeping
# the cached image well within any noticeable staleness.
_CAPTURE_MIN_INTERVAL_S = 0.2
_capture_last_ts: dict[int, float] = {}

# Scene-cut (looping test source) detection state -- see capture_frame_probe.
_SCENE_SIG: dict[int, "object"] = {}  # source_id -> 16x16 grayscale numpy array
_SCENE_CUT_DIFF_THRESHOLD = 35.0  # mean abs diff (0-255 scale) to call it a cut


def capture_frame_probe(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    ctx: dict,
) -> Gst.PadProbeReturn:
    """Pull RGBA bytes from nvvideoconvert's system-mem buffer, encode JPEG,
    cache per source_id (throttled to _CAPTURE_MIN_INTERVAL_S)."""
    _CAPTURE_DIAG["n"] += 1
    if _CAPTURE_DIAG["n"] % 50 == 1:
        logger.info("capture probe tick n=%d ok=%d no_batch=%d no_map=%d sz_mismatch=%d last_err=%r",
                    _CAPTURE_DIAG["n"], _CAPTURE_DIAG["ok"], _CAPTURE_DIAG["no_batch"],
                    _CAPTURE_DIAG["no_map"], _CAPTURE_DIAG["sz_mismatch"], _CAPTURE_DIAG["last_err"][:120])
    buf = info.get_buffer()
    if buf is None:
        return Gst.PadProbeReturn.OK
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    if batch_meta is None:
        _CAPTURE_DIAG["no_batch"] += 1
        return Gst.PadProbeReturn.OK

    cache: dict[int, bytes] = ctx.get("frame_jpeg_cache", {})
    if cache is None:
        return Gst.PadProbeReturn.OK

    now = time.monotonic()
    # Cheap pass first: is ANY source in this batch actually due for a
    # re-encode? If not, skip the buffer map + PIL work entirely.
    due_sources: set[int] = set()
    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break
        sid = frame_meta.source_id
        if now - _capture_last_ts.get(sid, 0.0) >= _CAPTURE_MIN_INTERVAL_S:
            due_sources.add(sid)
        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break
    if not due_sources:
        return Gst.PadProbeReturn.OK

    success, map_info = buf.map(Gst.MapFlags.READ)
    if not success:
        _CAPTURE_DIAG["no_map"] += 1
        return Gst.PadProbeReturn.OK
    try:
        import numpy as np
        from PIL import Image
        import io

        raw = bytes(map_info.data)
        w = pipeline_settings.muxer_output_width
        h = pipeline_settings.muxer_output_height
        frame_size = w * h * 4

        if len(raw) < frame_size:
            _CAPTURE_DIAG["sz_mismatch"] += 1
            _CAPTURE_DIAG["last_err"] = f"raw={len(raw)} expected>={frame_size}"
            return Gst.PadProbeReturn.OK

        frame_meta_list = batch_meta.frame_meta_list
        while frame_meta_list is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
            except StopIteration:
                break
            if frame_meta.source_id not in due_sources:
                try:
                    frame_meta_list = frame_meta_list.next
                except StopIteration:
                    break
                continue
            batch_id = frame_meta.batch_id
            offset = batch_id * frame_size
            if offset + frame_size > len(raw):
                _CAPTURE_DIAG["sz_mismatch"] += 1
                break
            try:
                arr = np.frombuffer(raw[offset:offset + frame_size], dtype=np.uint8).reshape((h, w, 4))
                bgr = arr[:, :, :3]

                # Scene-cut detection: a looping RTSP test source (mediamtx
                # replaying a file) gives no EOS/discontinuity signal and
                # RTSP frame numbering stays continuous across the loop, so
                # this is the only way to notice it. Downsample to a tiny
                # grayscale grid and compare mean abs diff against the
                # previous tick's grid for this source -- a real loop cut
                # (end of clip -> start of clip) produces a large jump;
                # normal motion/lighting between throttled ticks
                # (_CAPTURE_MIN_INTERVAL_S apart) does not.
                _sid = frame_meta.source_id
                _small = np.asarray(Image.fromarray(bgr).resize((16, 16), Image.BILINEAR).convert("L"), dtype=np.float32)
                _prev_sig = _SCENE_SIG.get(_sid)
                if _prev_sig is not None:
                    _diff = float(np.abs(_small - _prev_sig).mean())
                    if _diff >= _SCENE_CUT_DIFF_THRESHOLD:
                        ctx.setdefault("scene_cut_flags", {})[_sid] = True
                        logger.info("Scene cut detected on source_id=%d (mean diff=%.1f)", _sid, _diff)
                _SCENE_SIG[_sid] = _small

                img = Image.fromarray(bgr)
                if img.width > 1280:
                    new_h = int(img.height * (1280 / img.width))
                    img = img.resize((1280, new_h), Image.BILINEAR)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=70)
                cache[frame_meta.source_id] = out.getvalue()
                _capture_last_ts[frame_meta.source_id] = now
                _CAPTURE_DIAG["ok"] += 1
            except Exception as exc:
                _CAPTURE_DIAG["last_err"] = str(exc)[:200]
            try:
                frame_meta_list = frame_meta_list.next
            except StopIteration:
                break
    finally:
        buf.unmap(map_info)

    return Gst.PadProbeReturn.OK


def extract_metadata(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    ctx: dict,
) -> Gst.PadProbeReturn:
    """GStreamer pad probe that extracts NvDsBatchMeta and publishes to Kafka.

    VLM requests are batched conditionally: the FrameBatcher collects 1 frame
    every 10s ONLY when FSM is in OBSERVE/VIOLATION/ALERTING/ESCALATED state.
    After 6 frames (60s window), or on track exit, the batch is sent to VLM.
    This gives the VLM temporal context instead of a single snapshot.
    """
    kafka_producer: PipelineKafkaProducer = ctx["kafka_producer"]
    health: CameraHealth = ctx["health"]
    track_manager: TrackStateManager = ctx["track_manager"]
    roi_manager: ROIManager = ctx["roi_manager"]
    fsm_manager: EventFSMManager = ctx["fsm_manager"]
    action_fsm = ctx.get("action_fsm")
    frame_batcher: FrameBatcher = ctx["frame_batcher"]

    gst_buffer = info.get_buffer()
    if gst_buffer is None:
        return Gst.PadProbeReturn.OK

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if batch_meta is None:
        return Gst.PadProbeReturn.OK

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        # Look up the actual camera name from the source_id via source_manager.
        # Read the live pad_to_camera property each frame (see comment at
        # probe_ctx construction) rather than a frozen snapshot dict.
        _sm = ctx.get("source_manager") if isinstance(ctx, dict) else None
        _pad_map = _sm.pad_to_camera if _sm is not None else {}
        camera_id = _pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")

        # Consume a scene-cut flag raised by capture_frame_probe (pixel-diff
        # based, since a looping RTSP test source gives no EOS/discontinuity
        # signal). Reset this camera's track/FSM/escalation state so both
        # person AND waste tracks get fresh lifecycles on the new footage
        # instead of the same track_id carrying stale dwell/VIOLATION state
        # across the cut forever.
        if ctx.get("scene_cut_flags", {}).pop(frame_meta.source_id, False):
            logger.info("Resetting track/FSM state for %s after scene cut", camera_id)
            track_manager.reset_camera(camera_id)
            fsm_manager.reset_camera(camera_id)
            for _d in (
                ctx.get("waste_dumper_map"),
                ctx.get("waste_attribution_pair"),
                ctx.get("waste_attribution_started_at"),
                ctx.get("waste_repeat_alert_at"),
            ):
                if _d:
                    for _k in [k for k in _d if k[0] == camera_id]:
                        del _d[_k]
        # nvinfer outputs bbox coords in muxer resolution, NOT source resolution.
        # source_frame_width may be 1280 (original) while rect.left is in 1920 (muxer).
        # Always use muxer dimensions for normalization.
        frame_width = pipeline_settings.muxer_output_width
        frame_height = pipeline_settings.muxer_output_height

        # Per-camera detection threshold overrides (cameras.detection_config,
        # set via PATCH /api/cameras/{id}). Missing keys fall back to the
        # pipeline-wide defaults in pipeline_settings.
        _rs = ctx.get("roi_syncer") if isinstance(ctx, dict) else None
        _dcfg = _rs.get_detection_config(camera_id) if _rs is not None else {}
        _cam_violation_threshold_s = _dcfg.get(
            "dwell_violation_threshold_s", pipeline_settings.dwell_violation_threshold_s
        )
        # Attribution is spatial: on the waste track's FIRST observed frame,
        # the new waste bbox must be within 40 source-image pixels of a
        # person's bbox. This is edge-to-edge distance (not center-to-center,
        # which incorrectly measures from the person's torso). The detector
        # bboxes are in muxer coordinates, so the gap is converted back to
        # source-image pixels below before applying this threshold.
        _cam_waste_proximity_px = _dcfg.get(
            "waste_proximity_px", pipeline_settings.waste_proximity_px
        )
        _cam_waste_proximity_window_s = max(0.0, min(1.0, float(_dcfg.get(
            "waste_proximity_alert_window_s", pipeline_settings.waste_dump_alert_threshold_s
        ))))

        detections: list[dict] = []

        # --- Pre-scan: collect carried_item bboxes for person association,
        # and person bboxes for first-frame "who dumped it" attribution on
        # waste tracks below. ---
        carried_item_bboxes: list[tuple[str, dict]] = []  # (class_name, bbox_dict)
        carried_item_rects: list[tuple[float, float, float, float]] = []
        person_bboxes: list[tuple[int, tuple[float, float, float, float]]] = []
        _prescan = frame_meta.obj_meta_list
        while _prescan is not None:
            try:
                _om = pyds.NvDsObjectMeta.cast(_prescan.data)
            except StopIteration:
                break
            _cid = int(_om.class_id)
            _cn = COCO_CLASS_MAP.get(_cid)
            _r = _om.rect_params
            if _cn and CLASS_CATEGORIES.get(_cn) == "carried_item":
                carried_item_rects.append((
                    _r.left, _r.top, _r.left + _r.width, _r.top + _r.height,
                ))
                carried_item_bboxes.append((_cn, {
                    "x": _r.left / (frame_meta.source_frame_width or pipeline_settings.input_width),
                    "y": _r.top / (frame_meta.source_frame_height or pipeline_settings.input_height),
                    "w": _r.width / (frame_meta.source_frame_width or pipeline_settings.input_width),
                    "h": _r.height / (frame_meta.source_frame_height or pipeline_settings.input_height),
                }))
            elif _cn == "person":
                _pt = None if int(_om.object_id) == 0xFFFFFFFFFFFFFFFF else int(_om.object_id)
                if _pt is not None:
                    person_bboxes.append((
                        _pt,
                        (_r.left, _r.top, _r.left + _r.width, _r.top + _r.height),
                    ))
            try:
                _prescan = _prescan.next
            except StopIteration:
                break

        # Track which current-frame people overlap a carried item. This is
        # stronger release evidence than generic proximity and lets a new
        # placement episode be recognised even when ByteTrack reuses an old,
        # previously-unmatched waste ID across a looping/occluded sequence.
        _carrying_person_ids: set[int] = set()
        for _pid0, _pbox0 in person_bboxes:
            for _ibox0 in carried_item_rects:
                _ix1 = max(_pbox0[0], _ibox0[0])
                _iy1 = max(_pbox0[1], _ibox0[1])
                _ix2 = min(_pbox0[2], _ibox0[2])
                _iy2 = min(_pbox0[3], _ibox0[3])
                _iarea = max(0.0, _ibox0[2] - _ibox0[0]) * max(0.0, _ibox0[3] - _ibox0[1])
                if _iarea > 0 and _ix2 > _ix1 and _iy2 > _iy1:
                    if ((_ix2 - _ix1) * (_iy2 - _iy1)) / _iarea > 0.3:
                        _carrying_person_ids.add(_pid0)
                        break

        # Diagnostic: count ALL raw nvinfer objects before any filtering,
        # so we can tell at a glance whether nvinfer is producing 0 boxes
        # (parser/threshold problem) vs producing boxes that the Python
        # COCO_CLASS_MAP is filtering out (class-id mapping problem).
        raw_obj_count = 0
        raw_class_histogram: dict[int, int] = {}
        obj_meta_list = frame_meta.obj_meta_list
        while obj_meta_list is not None:
            try:
                obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
            except StopIteration:
                break

            class_id: int = obj_meta.class_id
            raw_obj_count += 1
            raw_class_histogram[class_id] = raw_class_histogram.get(class_id, 0) + 1
            if class_id in COCO_CLASS_MAP:
                rect = obj_meta.rect_params
                class_name = COCO_CLASS_MAP[class_id]
                track_id = None if int(obj_meta.object_id) == 0xFFFFFFFFFFFFFFFF else int(obj_meta.object_id)
                bbox_dict = {
                    "x": round(rect.left / frame_width, 6),
                    "y": round(rect.top / frame_height, 6),
                    "w": round(rect.width / frame_width, 6),
                    "h": round(rect.height / frame_height, 6),
                }
                detection_dict: dict = {
                    "class_id": class_id,
                    "label": class_name,
                    "category": CLASS_CATEGORIES.get(class_name, "unknown"),
                    "confidence": round(float(obj_meta.confidence), 4),
                    "bbox": bbox_dict,
                    "track_id": track_id,
                }

                if track_id is not None:
                    # bbox center (normalized)
                    bbox_center_x = bbox_dict["x"] + bbox_dict["w"] / 2
                    bbox_center_y = bbox_dict["y"] + bbox_dict["h"] / 2
                    # ROI presence/dwell tracking runs for person AND waste.
                    # Carried items (bag) still don't get their own dwell/FSM
                    # -- a bag sitting in the zone geometrically isn't a
                    # person occupying it, and it flows through
                    # carried_item_bboxes for person-carrying-item
                    # association above, independent of in_roi. Waste DOES
                    # need its own dwell tracking (previously excluded
                    # entirely, so a dumped item's own on-screen timer/FSM
                    # state never advanced past IDLE no matter how long it
                    # sat there).
                    in_roi = (
                        class_name in ("person", "waste")
                        and roi_manager.is_in_any_roi(camera_id, bbox_center_x, bbox_center_y)
                    )

                    track_info = track_manager.update_track(camera_id, track_id, class_name, bbox_dict, in_roi)

                    # Capture attribution on a new waste track's first observed
                    # frame. A recycled unmatched waste ID gets one safe retry
                    # only when a current-frame person is visibly carrying an
                    # item; generic later bystanders remain ineligible. Only
                    # current-frame tracker metadata is considered.
                    _dumper_map = ctx.setdefault("waste_dumper_map", {})
                    _attribution_started_map = ctx.setdefault("waste_attribution_started_at", {})
                    _waste_key = (camera_id, track_id)
                    _retry_recycled_waste = (
                        _waste_key in _dumper_map
                        and _dumper_map[_waste_key] is None
                        and bool(_carrying_person_ids)
                    )
                    if (
                        class_name == "waste"
                        and (_waste_key not in _dumper_map or _retry_recycled_waste)
                        and not fsm_manager.is_escalated(camera_id, track_id)
                    ):
                        _wbox0 = (
                            rect.left, rect.top,
                            rect.left + rect.width, rect.top + rect.height,
                        )
                        _best_d0, _best_pid0, _best_pbox0 = None, None, None
                        _source_w0 = float(frame_meta.source_frame_width or frame_width)
                        _source_h0 = float(frame_meta.source_frame_height or frame_height)
                        _x_to_source0 = _source_w0 / float(frame_width)
                        _y_to_source0 = _source_h0 / float(frame_height)
                        for _pid0, _pbox0 in person_bboxes:
                            # Retrying an old unmatched waste ID is allowed
                            # only for a person carrying an item on this frame;
                            # this prevents a later ordinary bystander from
                            # inheriting the incident.
                            if _retry_recycled_waste and _pid0 not in _carrying_person_ids:
                                continue
                            _dx0 = max(_pbox0[0] - _wbox0[2], _wbox0[0] - _pbox0[2], 0.0)
                            _dy0 = max(_pbox0[1] - _wbox0[3], _wbox0[1] - _pbox0[3], 0.0)
                            # Calibrate from muxer coordinates to the source
                            # image: "20px" now means 20px in the camera frame
                            # regardless of streammux scaling/resolution.
                            _d0 = math.hypot(
                                _dx0 * _x_to_source0,
                                _dy0 * _y_to_source0,
                            )
                            if _best_d0 is None or _d0 < _best_d0:
                                _best_d0, _best_pid0, _best_pbox0 = _d0, _pid0, _pbox0
                        if _best_d0 is None or _best_d0 > _cam_waste_proximity_px:
                            _best_pid0, _best_pbox0 = None, None
                        # Freeze the result for this placement attempt.
                        _dumper_map[_waste_key] = _best_pid0
                        if _best_pid0 is not None:
                            # The 500ms persistence clock belongs to this
                            # placement episode, not the possibly-recycled
                            # waste track's original first_seen timestamp.
                            _attribution_started_map[_waste_key] = time.monotonic()
                            ctx.setdefault("waste_attribution_pair", {})[_waste_key] = {
                                "person_id": _best_pid0,
                                "waste_id": track_id,
                                "person_bbox": {
                                    "x": _best_pbox0[0] / frame_width,
                                    "y": _best_pbox0[1] / frame_height,
                                    "w": (_best_pbox0[2] - _best_pbox0[0]) / frame_width,
                                    "h": (_best_pbox0[3] - _best_pbox0[1]) / frame_height,
                                },
                                "waste_bbox": dict(bbox_dict),
                            }
                            logger.info(
                                "WASTE ATTRIBUTION CAPTURE %s frame=%d waste=%d person=%d "
                                "gap=%.1fpx threshold=%.1fpx source=%dx%d",
                                camera_id, int(frame_meta.frame_num), track_id, _best_pid0,
                                _best_d0, _cam_waste_proximity_px,
                                int(_source_w0), int(_source_h0),
                            )
                        else:
                            logger.info(
                                "WASTE ATTRIBUTION CAPTURE %s frame=%d waste=%d person=none "
                                "nearest_gap=%s threshold=%.1fpx source=%dx%d",
                                camera_id, int(frame_meta.frame_num), track_id,
                                "n/a" if _best_d0 is None else f"{_best_d0:.1f}px",
                                _cam_waste_proximity_px,
                                int(_source_w0), int(_source_h0),
                            )

                    # Ground-confirmed violation: fire the full confirmed/
                    # critical alert once a waste object has persisted for
                    # _cam_waste_proximity_window_s (default
                    # 500ms, config: waste_proximity_alert_window_s) -- NOT at
                    # age_s==0 (raw track creation), since a
                    # single-frame detector blip can spawn a track_id that
                    # never really represents an object on the ground.
                    # Also NOT the full 15s dwell_violation_threshold_s,
                    # which is for PERSON loitering/dwell behavior
                    # (unrelated FSM, still intact below) -- a waste
                    # object doesn't need 15s to confirm it's a violation,
                    # just long enough to debounce detector noise.
                    if (
                        class_name == "waste"
                        and _attribution_started_map.get(_waste_key) is not None
                        and time.monotonic() - _attribution_started_map[_waste_key]
                            >= _cam_waste_proximity_window_s
                        and _dumper_map.get(_waste_key) is not None
                        and not fsm_manager.is_escalated(camera_id, track_id)
                    ):
                        # This paired spatial/persistence rule overrides the
                        # normal dwell FSM for BOTH tracks. The waste and its
                        # frozen first-frame person turn solid red together
                        # within 500ms (hard-capped at 1s for camera overrides).
                        fsm_manager.force_violation(
                            camera_id, track_id, class_name,
                            dwell_time_s=track_info.dwell_time_s,
                            bbox=bbox_dict,
                        )
                        # Seed the periodic-reminder timer here too -- same
                        # bug class as the dwell-confirm path: an unseeded
                        # entry defaults to 0.0, so the periodic-reminder
                        # check fires again as soon as the FSM naturally
                        # reaches VIOLATION/ALERTING (dwell_violation_
                        # threshold_s later), producing a second alert
                        # ~15-25s after this immediate one instead of
                        # waiting a full waste_repeat_alert_interval_s.
                        ctx.setdefault("waste_repeat_alert_at", {})[(camera_id, track_id)] = time.monotonic()
                        _cache = ctx.get("frame_jpeg_cache", {})
                        # Use the proximity attribution captured during the
                        # 500ms placement window, never a later bystander.
                        _nearest_pid = _dumper_map.get((camera_id, track_id))
                        if _nearest_pid is not None:
                            # The dumper remains a suspect after crossing the
                            # ROI boundary; only losing the track/explicit
                            # resolution clears the persistent marker.
                            _person_track = track_manager.get_track(camera_id, _nearest_pid)
                            _person_bbox = (
                                _person_track.bbox_history[-1]
                                if _person_track is not None and _person_track.bbox_history
                                else None
                            )
                            fsm_manager.force_violation(
                                camera_id, _nearest_pid, "person",
                                dwell_time_s=(
                                    _person_track.dwell_time_s if _person_track is not None else 0.0
                                ),
                                bbox=_person_bbox,
                            )
                        # The paired-alert condition above guarantees an ID;
                        # never send a misleading "unidentified person" alert.
                        _who = f"person #{_nearest_pid % 1000:03d}"
                        _fire_waste_alert_async(
                            kafka_producer, camera_id, track_id,
                            _cache.get(frame_meta.source_id),
                            severity="critical",
                            title="Waste dumping confirmed",
                            message=f"Waste dumped by {_who} — item detected in monitored zone",
                            dumped_by_track_id=_nearest_pid,
                            dwell_time_s=track_info.dwell_time_s,
                            bbox=bbox_dict,
                            detected_at=time.time(),
                        )
                        logger.info(
                            "WASTE ALERT (immediate) %s track=%d: dumped by %s",
                            camera_id, track_id, _who,
                        )
                    # Check if this person has a carried_item overlapping
                    has_carried = False
                    if class_name == "person" and carried_item_bboxes:
                        px, py, pw, ph = bbox_dict["x"], bbox_dict["y"], bbox_dict["w"], bbox_dict["h"]
                        for _ci_name, _ci_bb in carried_item_bboxes:
                            ix, iy, iw, ih = _ci_bb["x"], _ci_bb["y"], _ci_bb["w"], _ci_bb["h"]
                            ox1 = max(px, ix); oy1 = max(py, iy)
                            ox2 = min(px+pw, ix+iw); oy2 = min(py+ph, iy+ih)
                            if ox2 > ox1 and oy2 > oy1:
                                overlap = (ox2-ox1) * (oy2-oy1)
                                item_area = iw * ih
                                if item_area > 0 and overlap / item_area > 0.3:
                                    has_carried = True
                                    break

                    # Waste does not participate in the legacy 15-second
                    # ROI-dwell FSM. Its only violation path is the paired
                    # first-frame person association + <=1s persistence rule
                    # above; otherwise an unmatched waste track would turn
                    # red 15 seconds later and look like a delayed alert.
                    if class_name == "waste":
                        fsm_event = None
                    elif action_fsm is not None:
                        fsm_event = action_fsm.update(
                            camera_id, track_id, class_name,
                            obj_meta=obj_meta,
                            in_roi=in_roi,
                            dwell_time_s=track_info.dwell_time_s,
                            bbox=bbox_dict,
                            has_carried_item=has_carried,
                            violation_threshold_s=_cam_violation_threshold_s,
                        )
                    else:
                        fsm_event = fsm_manager.update(
                            camera_id, track_id, class_name,
                            in_roi=in_roi,
                            dwell_time_s=track_info.dwell_time_s,
                            bbox=bbox_dict,
                            violation_threshold_s=_cam_violation_threshold_s,
                            has_carried_item=has_carried,
                        )

                    if fsm_event:
                        kafka_producer.send_event(fsm_event)

                        # --- OBSERVE triggers: one-shot per track ---
                        # Get the FSM object to check/set one-shot guards
                        _fsm_obj = fsm_manager._fsms.get((camera_id, track_id))

                        # ENTER: start recording AND fire VLM immediately
                        # (Phase 0.1: cuts ~3-5s wall clock vs waiting for
                        # VIOLATION). Track bbox is known here, motion gate
                        # applied downstream by alert_consumer.
                        if fsm_event.state == FSMState.ENTER:
                            _ew = ctx.get("evidence_writer")
                            if _ew:
                                _ew.start_evidence(camera_id, track_id, class_name)

                            _category = CLASS_CATEGORIES.get(class_name, "unknown")
                            _actionable = (
                                class_name in VEHICLE_CLASSES or
                                _category in ("person", "carried_item", "waste_object")
                            )
                            if _actionable and _fsm_obj and not _fsm_obj.vlm_fired:
                                _fsm_obj.vlm_fired = True
                                _speed = track_info.speed_px_s
                                _area = track_info.bbox_area
                                _cache = ctx.get("frame_jpeg_cache", {})
                                _jpeg = _cache.get(frame_meta.source_id)
                                _vlm = ctx.get("vlm_client")
                                if _vlm is not None:
                                    _vlm.submit(
                                        camera_id=camera_id, track_id=track_id,
                                        class_name=class_name,
                                        dwell_time_s=track_info.dwell_time_s,
                                        path_summary=f"enter;speed_norm={_speed:.4f};area_norm={_area:.5f}",
                                        trigger_bbox=bbox_dict,
                                        jpeg_bytes=_jpeg,
                                    )

                        # VIOLATION (red bbox): VLM already fired on ENTER
                        # (Phase 0.1). Capture keyframe for evidence only.
                        # Re-fire VLM only if ENTER fire was skipped.
                        elif fsm_event.state == FSMState.VIOLATION and _fsm_obj and not _fsm_obj.violation_triggered:
                            _fsm_obj.violation_triggered = True

                            _category = CLASS_CATEGORIES.get(class_name, "unknown")
                            _actionable = (
                                class_name in VEHICLE_CLASSES or
                                _category in ("person", "carried_item", "waste_object")
                            )
                            _cache = ctx.get("frame_jpeg_cache", {})
                            _jpeg = _cache.get(frame_meta.source_id)

                            if _actionable and not _fsm_obj.vlm_fired:
                                _fsm_obj.vlm_fired = True
                                _speed = track_info.speed_px_s
                                _area = track_info.bbox_area
                                _vlm = ctx.get("vlm_client")
                                if _vlm is not None:
                                    _vlm.submit(
                                        camera_id=camera_id, track_id=track_id,
                                        class_name=class_name,
                                        dwell_time_s=track_info.dwell_time_s,
                                        path_summary=f"violation_fallback;speed_norm={_speed:.4f};area_norm={_area:.5f}",
                                        trigger_bbox=bbox_dict,
                                        jpeg_bytes=_jpeg,
                                    )

                            _ew = ctx.get("evidence_writer")
                            if _ew:
                                # Use the same JPEG sent to VLM — it is the
                                # red-bbox frame showing the correct target.
                                _ew.capture_violation_keyframe(
                                    camera_id, track_id,
                                    jpeg_bytes=_jpeg if _actionable else None,
                                )

                        # LPR triggers immediately on violation (no batching needed)
                        if fsm_event.state.value == "violation":
                            if class_name in VEHICLE_CLASSES:
                                kafka_producer.send_lpr_request(
                                    camera_id=camera_id,
                                    track_id=track_id,
                                    vehicle_bbox=bbox_dict,
                                    class_name=class_name,
                                )
                            elif (
                                class_name == "waste"
                                and not fsm_manager.is_escalated(camera_id, track_id)
                                and ctx.get("waste_dumper_map", {}).get((camera_id, track_id)) is not None
                            ):
                                # Dwell-confirmed dumped item -- only reached
                                # for a track that somehow wasn't already
                                # escalated by the immediate-fire check above
                                # (e.g. detector missed it on the very first
                                # frames it appeared). Guarded on
                                # is_escalated so it never duplicates the
                                # immediate alert, which now fires the
                                # moment the waste object is first detected
                                # instead of waiting for this 15s dwell
                                # confirmation. Attribution was frozen when
                                # this waste track first appeared; the person
                                # nearest now may only be a bystander.
                                fsm_manager.force_violation(
                                    camera_id, track_id, class_name,
                                    dwell_time_s=track_info.dwell_time_s,
                                    bbox=bbox_dict,
                                )
                                # Use only the first-seen attribution. A
                                # cached None deliberately means the first
                                # frame had no attributable person.
                                _nearest_pid = ctx.get("waste_dumper_map", {}).get((camera_id, track_id))
                                if _nearest_pid is not None:
                                    _person_track = track_manager.get_track(camera_id, _nearest_pid)
                                    _person_bbox = (
                                        _person_track.bbox_history[-1]
                                        if _person_track is not None and _person_track.bbox_history
                                        else None
                                    )
                                    fsm_manager.force_violation(
                                        camera_id, _nearest_pid, "person",
                                        dwell_time_s=(
                                            _person_track.dwell_time_s if _person_track is not None else 0.0
                                        ),
                                        bbox=_person_bbox,
                                    )
                                _who = f"person #{_nearest_pid % 1000:03d}"
                                _msg = (
                                    f"Waste dumped by {_who} — item remains in monitored zone"
                                )
                                _cache = ctx.get("frame_jpeg_cache", {})
                                _fire_waste_alert_async(
                                    kafka_producer, camera_id, track_id,
                                    _cache.get(frame_meta.source_id),
                                    severity="critical",
                                    title="Waste dumping confirmed",
                                    message=_msg,
                                    dumped_by_track_id=_nearest_pid,
                                    dwell_time_s=track_info.dwell_time_s,
                                    bbox=bbox_dict,
                                    detected_at=time.time(),
                                )
                                logger.info(
                                    "WASTE ALERT %s track=%d: %s", camera_id, track_id, _msg
                                )
                                # Seed the periodic-reminder timer now -- it
                                # was never touched by this confirm-alert
                                # path before, so a brand-new track's entry
                                # defaulted to 0.0 and the periodic-reminder
                                # check (below) fired immediately on the
                                # same or next tick, sending a duplicate
                                # Telegram message ~1s after the confirm
                                # alert instead of waiting a full
                                # waste_repeat_alert_interval_s.
                                ctx.setdefault("waste_repeat_alert_at", {})[(camera_id, track_id)] = time.monotonic()
                        # Flush partial batch on track exit — keep batcher clean but
                        # do NOT dispatch VLM for non-escalated tracks (noise).
                        if fsm_event.state.value == "exited":
                            # A dumper leaving the ROI does not end the waste
                            # incident or reset its 500ms placement timer.
                            if CLASS_CATEGORIES.get(class_name) == "waste_object":
                                # class_name is the raw COCO label (e.g.
                                # "bottle", "cup"), never the literal string
                                # "waste" -- the old `== "waste"` check never
                                # matched a real detection, so this map entry
                                # was never cleaned up on the waste object's
                                # own exit.
                                ctx.get("waste_dumper_map", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_attribution_pair", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_attribution_started_at", {}).pop((camera_id, track_id), None)
                            frame_batcher.on_track_exit(camera_id, track_id)
                            # Finalize evidence in background — finalize sleeps
                            # SEGMENT_SECS+2 to flush the trailing segment, so
                            # we must not block the DeepStream callback.
                            _ew = ctx.get("evidence_writer")
                            if _ew:
                                import threading
                                # Read from the escalation set — it persists
                                # across FSM cleanup. _fsm_obj is already None
                                # here because update() → EXITED cleans the
                                # FSM before we get a chance to inspect it.
                                _was_violation = fsm_manager.is_escalated(camera_id, track_id)
                                _outcome = "violation" if _was_violation else "clear"
                                threading.Thread(
                                    target=_ew.finalize,
                                    kwargs=dict(
                                        camera_id=camera_id, track_id=track_id,
                                        outcome=_outcome,
                                        dwell_time_s=track_info.dwell_time_s,
                                        action=class_name,
                                    ),
                                    daemon=True,
                                ).start()

                    # Enrich detection with tracking data
                    detection_dict["dwell_time_s"] = round(track_info.dwell_time_s, 1)
                    detection_dict["in_roi"] = in_roi
                    _current_fsm = fsm_manager.get_fsm_state(camera_id, track_id)
                    detection_dict["fsm_state"] = _current_fsm
                    # Suspect flag — persists after leaving ROI until track lost
                    detection_dict["is_suspect"] = fsm_manager.is_escalated(camera_id, track_id)

                    # --- Periodic re-alert for a still-unresolved waste VIOLATION ---
                    # The confirmed-dump alert above (kafka_producer.send_alert,
                    # severity="critical") fires exactly once, on the FSM's
                    # transition INTO VIOLATION -- fsm_manager.update() only
                    # returns an FSMEvent on a state CHANGE, never for a track
                    # that's still sitting in the same state frame after
                    # frame. That's correct for a real, one-time dump, but a
                    # dumped item that's still there 5 minutes later and
                    # nobody's dealt with it should keep reminding operators,
                    # not alert once and go silent forever. (Also literally
                    # what surfaced this: this camera's RTSP source is a
                    # looping test video -- DeepStream's tracker doesn't
                    # detect the loop as a scene change, so the same
                    # track_id and its VIOLATION state persist indefinitely
                    # across every loop instead of resetting.)
                    if (
                        CLASS_CATEGORIES.get(class_name, "unknown") == "waste_object"
                        and _current_fsm in ("violation", "alerting", "escalated")
                        and track_id != 0xFFFFFFFFFFFFFFFF
                        and ctx.get("waste_dumper_map", {}).get((camera_id, track_id)) is not None
                    ):
                        _repeat_key = (camera_id, track_id)
                        _repeat_state = ctx.setdefault("waste_repeat_alert_at", {})
                        _now_mono = time.monotonic()
                        _last_fire = _repeat_state.get(_repeat_key, 0.0)
                        if _now_mono - _last_fire >= pipeline_settings.waste_repeat_alert_interval_s:
                            _repeat_state[_repeat_key] = _now_mono
                            # Reminders retain the original placement-time
                            # attribution; never substitute a later bystander.
                            _nid2 = ctx.get("waste_dumper_map", {}).get(_repeat_key)
                            _who2 = f"person #{_nid2 % 1000:03d}"
                            _cache2 = ctx.get("frame_jpeg_cache", {})
                            _fire_waste_alert_async(
                                kafka_producer, camera_id, track_id,
                                _cache2.get(frame_meta.source_id),
                                severity="critical",
                                title="Waste dumping confirmed",
                                message=(
                                    f"Reminder: waste dumped by {_who2} still not cleared "
                                    f"in monitored zone"
                                ),
                                dumped_by_track_id=_nid2,
                                dwell_time_s=track_info.dwell_time_s,
                                bbox=bbox_dict,
                                detected_at=time.time(),
                            )
                            logger.info(
                                "WASTE ALERT (repeat) %s track=%d: still unresolved after %.0fs",
                                camera_id, track_id, track_info.dwell_time_s,
                            )

                    # Track evidence bbox path for active events
                    _ew = ctx.get("evidence_writer")
                    if _ew and _current_fsm in ("observe", "violation", "alerting", "escalated"):
                        _ew.add_frame(camera_id, track_id, bbox_dict, float(obj_meta.confidence), _current_fsm)

                    # VLM dispatch moved to OBSERVE+carried / VIOLATION only (red bbox).
                    # The frame_batcher periodic-batch path was firing VLM on every
                    # 60s window regardless of track state, producing ghost alerts
                    # that described whoever happened to be in frame. Dropped.
                    current_fsm = detection_dict.get("fsm_state")

                detections.append(detection_dict)

            try:
                obj_meta_list = obj_meta_list.next
            except StopIteration:
                break

        # --- Per-camera alert flag ---
        # Derive the camera-level alert from the worst FSM state across all
        # detections in this frame. Each camera gets a unique flag.
        camera_alert = _derive_camera_alert(camera_id, detections)

        metadata: dict = {
            "camera_id": camera_id,
            "frame_num": frame_meta.frame_num,
            "timestamp_ms": int(time.monotonic() * 1000),
            "detections": detections,
            "camera_alert": camera_alert,
            # Diagnostic — raw nvinfer object count and per-class histogram
            # BEFORE the Python COCO_CLASS_MAP filter. Tells us at a glance
            # whether nvinfer/parser is producing boxes at all vs the
            # Python probe dropping them on class-id mismatch.
            "raw_obj_count": raw_obj_count,
            "raw_class_hist": {str(k): v for k, v in raw_class_histogram.items()},
        }

        kafka_producer.send_metadata(camera_id, metadata)
        kafka_producer.poll()
        health.on_frame(camera_id)

        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------


def build_pipeline() -> tuple[Gst.Pipeline, Gst.Element, Gst.Element, Gst.Element, bool, Optional[Gst.Element], OutputStreamManager, Gst.Element]:
    """Construct and return the DeepStream GStreamer pipeline.

    Returns:
        (pipeline, streammux, fakesink, nvdsanalytics, use_native_meta,
         ww_meta_elem, output_manager)
        The caller needs streammux to hand to SourceManager, fakesink to attach
        the metadata pad probe, nvdsanalytics for ROI/dwell queries,
        use_native_meta to select the probe path, ww_meta_elem (may be None)
        for the native C metadata processor, and output_manager for per-camera
        RTSP output stream management.
    """
    cfg = pipeline_settings

    pipeline: Gst.Pipeline = Gst.Pipeline.new("wastewatch-pipeline")

    # --- Stream muxer -------------------------------------------------------
    streammux = make_element("nvstreammux", "stream_muxer")
    streammux.set_property("batch-size", cfg.batch_size)
    streammux.set_property("width", cfg.muxer_output_width)
    streammux.set_property("height", cfg.muxer_output_height)
    # batched-push-timeout: force streammux to push a partial batch if
    # a single camera's frames don't fill the batch within 40ms (25fps
    # tick). Previous 4-second timeout meant a single-camera pipeline
    # would wait up to 4s between pushes, leaving the probe dormant
    # and the video pipeline appearing "frozen".
    streammux.set_property("batched-push-timeout", 40_000)
    # live-source hint — streammux adjusts its buffering strategy for
    # RTSP/live input (no attempt to resample to file-mode timestamps).
    try:
        streammux.set_property("live-source", 1)
    except Exception:
        pass
    # Drop frames if downstream falls behind instead of accumulating.
    try:
        streammux.set_property("drop-pipeline-eos", 1)
    except Exception:
        pass
    pipeline.add(streammux)

    # --- Inference (RF-DETR via nvinfer) ------------------------------------
    # WW_USE_SEG_MODEL=1 switches the primary GIE to RF-DETR-Seg-Nano
    # (bag/person/waste, instance-segmentation, network-type=3). Default
    # OFF: keep the known-working 2-class bbox model
    # (nvinfer_rfdetr.txt) live unless explicitly opted in. See
    # pipeline/configs/nvinfer_rfdetr_seg.txt for the new model's
    # class-index/mask-decode notes.
    use_seg_model = os.environ.get("WW_USE_SEG_MODEL", "0") == "1"
    pgie_config = "configs/nvinfer_rfdetr_seg.txt" if use_seg_model else "configs/nvinfer_rfdetr.txt"
    logger.info(f"primary_infer config: {pgie_config} (WW_USE_SEG_MODEL={'1' if use_seg_model else '0'})")
    nvinfer = make_element("nvinfer", "primary_infer")
    nvinfer.set_property("config-file-path", pgie_config)
    pipeline.add(nvinfer)

    # --- Object tracker (ByteTrack via nvtracker) ---------------------------
    nvtracker = make_element("nvtracker", "tracker")
    nvtracker.set_property("tracker-width", 640)
    nvtracker.set_property("tracker-height", 384)
    nvtracker.set_property("ll-lib-file", cfg.tracker_ll_lib)
    nvtracker.set_property("ll-config-file", cfg.tracker_config_path)
    nvtracker.set_property("gpu-id", 0)
    # DeepStream 7.0 removed `enable-batch-process` from nvtracker —
    # batch processing is now always on. Set it only on legacy DS 6.x
    # (where it exists as a GObject property).
    _tracker_props = {p.name for p in nvtracker.list_properties()}
    if "enable-batch-process" in _tracker_props:
        nvtracker.set_property("enable-batch-process", 1)
    pipeline.add(nvtracker)

    # --- Action classification (SGIE, operates on tracked PGIE objects) -----
    # Runs after the tracker so `operate-on-gie-id=1`/tracked object_id are
    # stable. Feeds ActionAwareFSM's fast-alert path (see
    # action_fsm_integration.py) — previously built but never linked into
    # the pipeline, so the FSM only ever saw dwell time, never actions.
    action_sgie = make_element("nvinfer", "action_classifier_sgie")
    action_sgie.set_property("config-file-path", "configs/nvinfer_action_sgie.txt")
    pipeline.add(action_sgie)

    # --- Analytics (ROI / line-crossing via nvdsanalytics) ------------------
    nvdsanalytics = make_element("nvdsanalytics", "analytics")
    nvdsanalytics.set_property("config-file", cfg.analytics_config_path)
    pipeline.add(nvdsanalytics)

    # --- Custom wastewatchmeta plugin (optional) ----------------------------
    use_native_meta = False
    ww_meta_elem = None
    try:
        from app.plugin_loader import ensure_plugin_loaded, create_wastewatch_element, configure_element

        use_native_meta = ensure_plugin_loaded()
        if use_native_meta:
            ww_meta_elem = create_wastewatch_element("ww_meta_processor")
            if ww_meta_elem:
                configure_element(
                    ww_meta_elem,
                    enter_threshold_s=cfg.dwell_enter_threshold_s,
                    observe_threshold_s=cfg.dwell_observe_threshold_s,
                    exit_timeout_s=300.0,
                    roi_config_path=cfg.analytics_config_path,
                    input_width=cfg.input_width,
                    input_height=cfg.input_height,
                )
                pipeline.add(ww_meta_elem)
                logger.info("Using native C wastewatchmeta plugin for metadata processing")
            else:
                use_native_meta = False
                logger.warning("wastewatchmeta element creation failed — falling back to Python metadata processing")
        else:
            logger.warning("wastewatchmeta plugin not found — falling back to Python metadata processing")
    except ImportError:
        logger.warning("plugin_loader module not available — falling back to Python metadata processing")

    # --- On-screen display --------------------------------------------------
    nvosd = make_element("nvdsosd", "on_screen_display")
    # display-text/display-bbox default to 1 but set explicitly so a bad
    # pre-loaded config cache can't hide them. process-mode=1 forces the
    # hardware rasteriser, which is the only mode that honours the
    # per-object rect_params we populate in the pre-osd pad probe.
    try:
        nvosd.set_property("process-mode", 1)
    except Exception:
        pass
    try:
        nvosd.set_property("display-bbox", 1)
        nvosd.set_property("display-text", 1)
        nvosd.set_property("display-clock", 0)
    except Exception:
        pass
    pipeline.add(nvosd)

    # --- Tee — splits stream to RTSP output branch and metadata branch ------
    tee = make_element("tee", "stream_tee")
    pipeline.add(tee)

    # --- Branch 1: Per-camera RTSP output via nvstreamdemux -----------------
    # Instead of a single muxed output, we demux back to per-camera streams.
    # Each camera gets its own H.264 encoding chain → UDP → MediaMTX.
    output_manager = OutputStreamManager(
        pipeline=pipeline,
        mediamtx_host="mediamtx",
        mediamtx_rtp_base_port=20000,
    )
    demux = output_manager.setup_demux()
    queue_demux = make_element("queue", "queue_demux")
    # leaky=downstream means: if the output branch (rtspclientsink ->
    # mediamtx) stalls, the queue drops OLD buffers instead of
    # backpressuring the tee. Without this, any stall on the output
    # side propagates up through the tee and blocks the metadata
    # branch too — which made the pad probe fire exactly once per
    # pipeline restart and then freeze, producing zero detection
    # events in Kafka even though nvinfer was running.
    queue_demux.set_property("leaky", 2)  # 2 = downstream
    queue_demux.set_property("max-size-buffers", 5)
    queue_demux.set_property("max-size-bytes", 0)
    queue_demux.set_property("max-size-time", 0)
    pipeline.add(queue_demux)

    # --- Branch 2: metadata extraction (fakesink) ---------------------------
    queue_meta = make_element("queue", "queue_meta")
    # Same leaky discipline — if the Kafka producer or probe is slow,
    # drop old frames rather than block the main pipeline.
    queue_meta.set_property("leaky", 2)
    queue_meta.set_property("max-size-buffers", 5)
    queue_meta.set_property("max-size-bytes", 0)
    queue_meta.set_property("max-size-time", 0)
    fakesink = make_element("fakesink", "fakesink_meta")
    fakesink.set_property("sync", False)
    fakesink.set_property("async", False)

    for elem in (queue_meta, fakesink):
        pipeline.add(elem)

    # --- Branch 3: synchronous frame capture (RGBA → JPEG cache) ------------
    # nvvideoconvert lifts NV12/NVMM to RGBA in system memory so Python can
    # map the buffer without the segfault you hit on raw NV12. A per-source
    # JPEG cache is populated from this probe at the same tick as the
    # detection metadata; the extract_metadata probe (branch 2) reads
    # the cache when it needs to attach a frame to a VLM request.
    queue_capture = make_element("queue", "queue_capture")
    # leaky=1 (upstream): when full, drop the OLDEST queued buffer so the
    # JPEG-encode probe (PIL resize+encode is comparatively slow, see
    # capture_frame_probe) always processes the freshest frame available
    # instead of falling behind and serving a stale cached snapshot for
    # alert evidence. leaky=2 (downstream) did the opposite -- it dropped
    # incoming NEW frames and kept old ones queued, which is why the saved
    # evidence image lagged behind the actual moment of the alert.
    queue_capture.set_property("leaky", 1)
    queue_capture.set_property("max-size-buffers", 3)
    queue_capture.set_property("max-size-bytes", 0)
    queue_capture.set_property("max-size-time", 0)
    nvconv_capture = make_element("nvvideoconvert", "nvconv_capture")
    capsfilter_capture = make_element("capsfilter", "capsfilter_capture")
    try:
        capsfilter_capture.set_property(
            "caps", Gst.Caps.from_string("video/x-raw, format=RGBA")
        )
    except Exception:
        logger.exception("capsfilter_capture caps set failed")
    fakesink_capture = make_element("fakesink", "fakesink_capture")
    fakesink_capture.set_property("sync", False)
    fakesink_capture.set_property("async", False)
    for elem in (queue_capture, nvconv_capture, capsfilter_capture, fakesink_capture):
        pipeline.add(elem)

    # --- Link main chain ----------------------------------------------------
    # streammux -> nvinfer -> nvtracker -> action_sgie -> nvdsanalytics -> [wastewatchmeta] -> nvosd -> tee
    if not streammux.link(nvinfer):
        logger.critical("Failed to link streammux -> nvinfer")
        sys.exit(1)
    if not nvinfer.link(nvtracker):
        logger.critical("Failed to link nvinfer -> nvtracker")
        sys.exit(1)
    if not nvtracker.link(action_sgie):
        logger.critical("Failed to link nvtracker -> action_sgie")
        sys.exit(1)
    if not action_sgie.link(nvdsanalytics):
        logger.critical("Failed to link action_sgie -> nvdsanalytics")
        sys.exit(1)

    if ww_meta_elem is not None:
        # Native path: nvdsanalytics -> wastewatchmeta -> nvosd
        if not nvdsanalytics.link(ww_meta_elem):
            logger.critical("Failed to link nvdsanalytics -> wastewatchmeta")
            sys.exit(1)
        if not ww_meta_elem.link(nvosd):
            logger.critical("Failed to link wastewatchmeta -> nvosd")
            sys.exit(1)
    else:
        # Fallback path: nvdsanalytics -> nvosd (original behavior)
        if not nvdsanalytics.link(nvosd):
            logger.critical("Failed to link nvdsanalytics -> nvosd")
            sys.exit(1)

    if not nvosd.link(tee):
        logger.critical("Failed to link nvosd -> tee")
        sys.exit(1)

    # --- Link branch 1: tee -> queue_demux -> nvstreamdemux
    # Per-camera output branches are added dynamically by OutputStreamManager
    # when cameras are registered via ConnectionMonitor.
    tee_src_demux = tee.get_request_pad("src_%u")
    queue_demux_sink = queue_demux.get_static_pad("sink")
    if tee_src_demux.link(queue_demux_sink) != Gst.PadLinkReturn.OK:
        logger.critical("Failed to link tee -> queue_demux")
        sys.exit(1)

    if not queue_demux.link(demux):
        logger.critical("Failed to link queue_demux -> nvstreamdemux")
        sys.exit(1)

    # --- Link branch 2: tee -> queue_meta -> fakesink -------------------------
    tee_src_meta = tee.get_request_pad("src_%u")
    queue_meta_sink = queue_meta.get_static_pad("sink")
    if tee_src_meta.link(queue_meta_sink) != Gst.PadLinkReturn.OK:
        logger.critical("Failed to link tee -> queue_meta")
        sys.exit(1)

    if not queue_meta.link(fakesink):
        logger.critical("Failed to link queue_meta -> fakesink")
        sys.exit(1)

    # --- Link branch 3: tee -> queue_capture -> nvvideoconvert -> caps -> fakesink ---
    tee_src_capture = tee.get_request_pad("src_%u")
    qc_sink = queue_capture.get_static_pad("sink")
    if tee_src_capture.link(qc_sink) != Gst.PadLinkReturn.OK:
        logger.critical("Failed to link tee -> queue_capture")
        sys.exit(1)
    if not queue_capture.link(nvconv_capture):
        logger.critical("Failed to link queue_capture -> nvvideoconvert")
        sys.exit(1)
    if not nvconv_capture.link(capsfilter_capture):
        logger.critical("Failed to link nvvideoconvert -> capsfilter_capture")
        sys.exit(1)
    if not capsfilter_capture.link(fakesink_capture):
        logger.critical("Failed to link capsfilter_capture -> fakesink_capture")
        sys.exit(1)

    return pipeline, streammux, fakesink, nvdsanalytics, use_native_meta, ww_meta_elem, output_manager, fakesink_capture


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    Gst.init(None)

    pipeline, streammux, fakesink, nvdsanalytics, use_native_meta, ww_meta_elem, output_manager, fakesink_capture = build_pipeline()  # noqa: F841

    # Per-source JPEG cache populated by the capture probe. Written by
    # capture_frame_probe in microseconds (Python encodes RGBA → JPEG with
    # PIL), read by extract_metadata when OBSERVE/VIOLATION fires.
    frame_jpeg_cache: dict[int, bytes] = {}
    # Shared with capture_frame_probe (separate tee branch, separate probe
    # ctx dict) -- set True per source_id when a scene-cut is detected,
    # consumed (and cleared) by extract_metadata to reset that camera's
    # track/FSM state. See capture_frame_probe's scene-cut detection block.
    scene_cut_flags: dict[int, bool] = {}
    # Shared between metadata attribution and the pre-NvOSD decoration probe.
    # It lets the waste object's original box turn red immediately. The
    # person's box is deliberately left unchanged until the 500ms FSM
    # confirmation completes.
    waste_attribution_pair: dict[tuple[str, int], dict] = {}

    kafka_producer = PipelineKafkaProducer()
    vlm_client = VLMGrpcClient()
    health = CameraHealth(timeout_s=30.0)
    source_manager = SourceManager(pipeline, streammux)

    # Evidence writer — manages JSON metadata, keyframes, and recording retention
    from app.evidence_writer import EvidenceWriter
    evidence_writer = EvidenceWriter()
    _rotation_counter = {"ticks": 0}  # rotate once per ~360 ticks (1 hour at 10s interval)

    # Attach metadata probe to fakesink's sink pad
    fakesink_sink_pad = fakesink.get_static_pad("sink")
    if fakesink_sink_pad is None:
        logger.critical("Could not get fakesink sink pad")
        sys.exit(1)

    # Frame batcher for conditional VLM analysis (used by both paths)
    frame_batcher = FrameBatcher(
        sample_interval_s=5.0,    # 1 frame every 5 seconds
        batch_size=12,            # 12 frames per batch (60s window)
        batch_timeout_s=90.0,     # Yield partial batch after 90s
    )

    if use_native_meta and ww_meta_elem is not None:
        # --- Native C plugin path: simplified probe, no Python managers -----
        from app.meta_probe import extract_metadata_v2, create_probe_context

        probe_ctx = create_probe_context(kafka_producer, health)
        probe_ctx["frame_batcher"] = frame_batcher
        probe_ctx["waste_attribution_pair"] = waste_attribution_pair
        fakesink_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            extract_metadata_v2,
            probe_ctx,
        )

        # These managers are not needed — the C plugin handles ROI, FSM,
        # dwell time, and tracking internally.
        track_manager = None
        roi_manager = None
        fsm_manager = None
        action_fsm = None
        roi_preprocessor = None

        logger.info("Metadata probe: using optimized extract_metadata_v2 (C plugin handles ROI/FSM/tracking)")
    else:
        # --- Python fallback path: full Python managers ----------------------
        track_manager = TrackStateManager(lost_timeout_s=pipeline_settings.dwell_observe_threshold_s * 2)
        roi_manager = ROIManager()
        # Hydrate the ROI manager from the FastAPI backend so the polygons
        # the operator drew in the UI actually gate FSM transitions. Without
        # this the in_roi flag is False forever and dumping events never
        # fire even when a person dwells in a marked zone.
        from app.roi_sync import ROISyncer
        roi_syncer = ROISyncer(
            roi_manager=roi_manager,
            api_base=os.environ.get("WW_API_BASE", "http://api:8080"),
            interval_s=10.0,
            nvdsanalytics_elem=nvdsanalytics,
            source_manager=source_manager,
            config_width=pipeline_settings.muxer_output_width,
            config_height=pipeline_settings.muxer_output_height,
        )
        roi_syncer.start()
        fsm_manager = EventFSMManager(
            enter_threshold_s=pipeline_settings.dwell_enter_threshold_s,
            observe_threshold_s=pipeline_settings.dwell_observe_threshold_s,
            violation_threshold_s=pipeline_settings.dwell_violation_threshold_s,
        )
        # Action-classifier fast path — wraps fsm_manager so a confident
        # dumping_waste classification from action_sgie skips the dwell
        # timer entirely, and suspicious actions (carrying_object,
        # loading_unloading) temporarily lower the OBSERVE threshold.
        from app.action_classifier import ActionClassifierExtractor
        from app.action_fsm_integration import ActionAwareFSM
        action_extractor = ActionClassifierExtractor()
        action_fsm = ActionAwareFSM(fsm_manager, action_extractor)
        roi_preprocessor = ROIPreprocessor(
            roi_manager,
            frame_width=pipeline_settings.input_width,
            frame_height=pipeline_settings.input_height,
        )

        probe_ctx = {
            "kafka_producer": kafka_producer,
            "vlm_client": vlm_client,
            "health": health,
            "track_manager": track_manager,
            "roi_manager": roi_manager,
            "fsm_manager": fsm_manager,
            "action_fsm": action_fsm,
            "roi_preprocessor": roi_preprocessor,
            "frame_batcher": frame_batcher,
            # NOT a snapshot: pad_to_camera is a computed property (fresh
            # dict per access) on source_manager. Storing source_manager.
            # pad_to_camera here would freeze it at pipeline-startup time —
            # empty, since no camera is registered yet on cold start — and
            # every later dynamic camera add would never be reflected,
            # silently falling back to the "cam-XX" default below and
            # mislabeling every real camera's metadata. Store the manager
            # itself so callers read the live property each frame.
            "source_manager": source_manager,
            "evidence_writer": evidence_writer,
            "frame_jpeg_cache": frame_jpeg_cache,
            "roi_syncer": roi_syncer,
            "scene_cut_flags": scene_cut_flags,
            "waste_attribution_pair": waste_attribution_pair,
        }
        fakesink_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            extract_metadata,
            probe_ctx,
        )

        # Attach synchronous frame capture probe to branch 3's sink.
        capture_sink = fakesink_capture.get_static_pad("sink")
        if capture_sink is not None:
            capture_sink.add_probe(
                Gst.PadProbeType.BUFFER,
                capture_frame_probe,
                {"frame_jpeg_cache": frame_jpeg_cache, "scene_cut_flags": scene_cut_flags},
            )
            logger.info("Frame capture probe attached to fakesink_capture.sink")

        logger.warning("Custom plugin not available — using Python fallback")

    # --- Attach the OSD decoration probe (both paths share it) --------------
    # Runs on nvdsosd's sink pad so it can populate rect_params + text_params
    # before the OSD plugin rasterises them into the frame. This is what
    # turns the invisible (border_width=0) primary detections from the
    # Yolo custom parser into visible bounding boxes + labels on the
    # per-camera RTSP/WebRTC output streams.
    osd_elem = pipeline.get_by_name("on_screen_display")
    if osd_elem is not None:
        osd_sink = osd_elem.get_static_pad("sink")
        if osd_sink is not None:
            osd_sink.add_probe(
                Gst.PadProbeType.BUFFER,
                decorate_for_osd,
                {
                    "fsm_manager": fsm_manager,
                    "track_manager": track_manager,
                    "roi_manager": roi_manager,
                    "source_manager": source_manager,
                    "waste_attribution_pair": waste_attribution_pair,
                },
            )
            logger.info("OSD decoration probe attached to nvdsosd.sink")

    # Connection monitor — manages both input sources and per-camera output
    # streams, with RTSP probing, health checks, and auto-reconnection.
    connection_monitor = ConnectionMonitor(
        source_manager=source_manager,
        output_manager=output_manager,
        health=health,
        kafka_producer=kafka_producer,
        poll_interval_s=10.0,
    )

    # Redis command channel — backend publishes add_camera/remove_camera/
    # update_camera/clear_violation/etc, this pipeline process consumes
    # them. Previously built but never instantiated here, so dynamic
    # camera management from the UI silently did nothing: the backend's
    # POST /api/cameras published to Redis, but nothing was listening.
    command_receiver = CommandReceiver(redis_url=os.environ.get("WW_REDIS_URL", "redis://localhost:6379/0"))
    command_handler = CommandHandler(
        fsm_manager=fsm_manager,
        kafka_producer=kafka_producer,
        connection_monitor=connection_monitor,
        frame_batcher=frame_batcher,
    )
    command_receiver.start()

    # Load initial sources from WW_SOURCES env var
    # Format: "cam-01=rtsp://mediamtx:8554/cam-01,cam-02=rtsp://mediamtx:8554/cam-02"
    sources_env = os.environ.get("WW_SOURCES", "")
    static_cam_ids: set[str] = set()
    if sources_env:
        for entry in sources_env.split(","):
            entry = entry.strip()
            if "=" not in entry:
                logger.warning("Skipping malformed source entry: %r", entry)
                continue
            cam_id, _, uri = entry.partition("=")
            cam_id = cam_id.strip()
            uri = uri.strip()
            if cam_id and uri:
                connection_monitor.register_camera(cam_id, uri)
                static_cam_ids.add(cam_id)
    else:
        logger.warning(
            "No WW_SOURCES configured — checking backend for dynamically-added cameras."
        )

    # Restore dynamically-added cameras (created via POST /api/cameras ->
    # add_camera Redis command while a previous pipeline process was
    # running) from the backend on startup. Without this, ConnectionMonitor
    # starts with an empty _connections dict every restart and any camera
    # added through the UI is silently dropped from the pipeline forever
    # until someone re-adds it -- its ROI still gets polled by ROISyncer
    # (which reads straight from the backend), giving the false impression
    # it's still tracked, but it was never re-registered as an input source.
    try:
        import httpx
        api_base = os.environ.get("WW_API_BASE", "http://api:8080")
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{api_base}/api/cameras")
            resp.raise_for_status()
            for cam in resp.json() or []:
                cam_id = cam.get("id")
                uri = cam.get("stream_url")
                if not cam_id or not uri or cam_id in static_cam_ids:
                    continue
                logger.info("Restoring dynamically-added camera %s (%s) from backend", cam_id, uri)
                connection_monitor.register_camera(cam_id, uri)
    except Exception:
        logger.exception("Failed to restore dynamically-added cameras from backend")

    # Initial ROI preprocessor update after sources are registered (fallback path only)
    if roi_preprocessor is not None:
        active_cams = list(source_manager.active_sources.keys())
        if active_cams:
            crops = roi_preprocessor.update_crops(active_cams)
            if crops:
                savings = roi_preprocessor.get_gpu_savings_estimate()
                for cam_id, pct in savings.items():
                    logger.info("Camera %s: ROI crop saves ~%.0f%% GPU inference", cam_id, pct)

    # GLib main loop
    loop = GLib.MainLoop()

    # Bus message handler
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(bus: Gst.Bus, message: Gst.Message) -> bool:  # noqa: ARG001
        msg_type = message.type
        if msg_type == Gst.MessageType.EOS:
            logger.info("End-of-stream received — stopping")
            loop.quit()
        elif msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            src_name = message.src.get_name() if message.src is not None else ""
            logger.error("GStreamer error: %s | debug: %s | src=%s", err.message, debug, src_name)
            # A single dead/misconfigured camera's RTSP source (its
            # URIDecodeBin is named "src_<camera_id>", see source_manager.py)
            # or its output rtspclientsink (named "rtspsink_<camera_id>",
            # see output_manager.py — e.g. mediamtx being briefly
            # unreachable when a camera's output branch tries to connect)
            # must not take down the whole pipeline -- every other camera
            # would go dark too. Isolate the failure to that one camera and
            # keep running; only a truly pipeline-wide error (not tied to a
            # per-camera element) is fatal.
            if src_name.startswith("src_"):
                cam_id = src_name[len("src_"):]
                logger.error(
                    "Source error isolated to camera %s — removing that source, pipeline continues",
                    cam_id,
                )
                try:
                    connection_monitor.unregister_camera(cam_id)
                except Exception:
                    logger.exception("Failed to remove failed camera %s after source error", cam_id)
            elif src_name.startswith("rtspsink_"):
                cam_id = src_name[len("rtspsink_"):]
                logger.error(
                    "Output sink error isolated to camera %s — removing that camera, pipeline continues",
                    cam_id,
                )
                try:
                    connection_monitor.unregister_camera(cam_id)
                except Exception:
                    logger.exception("Failed to remove failed camera %s after output sink error", cam_id)
            else:
                logger.critical("Fatal pipeline-wide GStreamer error — shutting down")
                loop.quit()
        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            logger.warning("GStreamer warning: %s | debug: %s", warn.message, debug)
        return True

    bus.connect("message", on_bus_message)

    # Periodic health check — runs every 10 seconds
    def health_check_cb() -> bool:
        # Drain and apply any queued commands from the backend (add/remove/
        # update camera, clear violation, etc) before the rest of this
        # tick's housekeeping, so a just-added camera is picked up promptly.
        for cmd in command_receiver.drain():
            try:
                command_handler.handle(cmd)
            except Exception:
                logger.exception("Failed to handle command: %s", cmd)

        # Connection monitor handles health checks, reconnection, and
        # per-camera output stream lifecycle
        connection_monitor.poll()

        if fsm_manager is not None and track_manager is not None:
            # Python fallback path — prune stale FSM entries and lost tracks.
            # Must go through action_fsm (when action classification is
            # active) rather than the raw fsm_manager directly — timeout
            # pruning bypasses ActionAwareFSM.update(), so calling the raw
            # manager here leaked entries in action_fsm._fast_alerted and
            # action_extractor's per-track history for any track that timed
            # out instead of exiting normally through in_roi=False.
            if action_fsm is not None:
                stale_events = action_fsm.prune_stale()
            else:
                stale_events = fsm_manager.prune_stale()
            for evt in stale_events:
                kafka_producer.send_event(evt)
            lost_tracks = track_manager.prune_lost()
            # Clear suspect highlight when tracker fully loses the object
            for lost in lost_tracks:
                fsm_manager.clear_escalated(lost.camera_id, lost.track_id)
                if CLASS_CATEGORIES.get(lost.class_name) == "waste_object":
                    _lost_key = (lost.camera_id, lost.track_id)
                    probe_ctx.get("waste_dumper_map", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_attribution_pair", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_attribution_started_at", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_repeat_alert_at", {}).pop(_lost_key, None)

        # Prune stale frame batchers. No VLM dispatch here either —
        # stale-timeout batches are pre-red and never needed alert-level review.
        frame_batcher.prune_stale()

        # Update ROI preprocessor crops if sources changed (fallback path only)
        if roi_preprocessor is not None:
            current_cams = list(source_manager.active_sources.keys())
            roi_preprocessor.update_crops(current_cams)

        # Evidence rotation — run once per hour (360 ticks × 10s = 1 hour)
        _rotation_counter["ticks"] += 1
        if _rotation_counter["ticks"] >= 360:
            _rotation_counter["ticks"] = 0
            evidence_writer.rotate()

        return True  # Keep the timeout alive

    GLib.timeout_add_seconds(10, health_check_cb)

    # Start pipeline
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        logger.critical("Failed to set pipeline to PLAYING state")
        sys.exit(1)

    logger.info("Pipeline running — press Ctrl-C to stop")
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Shutting down pipeline")
        command_receiver.stop()
        pipeline.set_state(Gst.State.NULL)
        kafka_producer.flush()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
