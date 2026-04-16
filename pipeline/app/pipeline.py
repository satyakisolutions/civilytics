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

import logging
import os
import sys
import time
from typing import Optional

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402

import pyds  # noqa: E402 — provided by the DeepStream Docker image

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
from app.stream_watcher import StreamWatcher
from app.tracking import TrackStateManager

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
COCO_CLASS_MAP: dict[int, str] = {
    # Persons
    1: "person",
    # Vehicles
    2: "bicycle",
    3: "car",
    4: "motorcycle",
    6: "bus",
    8: "truck",
    # Carried items (potential waste containers)
    27: "backpack",
    28: "umbrella",
    31: "handbag",
    33: "suitcase",
    # Waste objects (evidence of dumping)
    44: "bottle",
    46: "wine_glass",
    47: "cup",
    51: "bowl",
    62: "chair",
    63: "couch",
    64: "potted_plant",
    65: "bed",
    67: "dining_table",
    70: "toilet",
    72: "tv",
    78: "microwave",
    79: "oven",
    81: "sink",
    82: "refrigerator",
}

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
    _osd_pad_map = ctx.get("pad_to_camera", {})

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        camera_id = _osd_pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")

        # --- First pass: collect carried items ---------------------------------
        carried_items: list[tuple[str, object]] = []
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

            # --- Build label text ---
            if track_id == 0xFFFFFFFFFFFFFFFF:
                label = cname
            elif is_suspect and fsm_state in ("idle", "exited", None):
                # Suspect highlight — person was escalated, still visible
                label = f"#{track_id_short:03d} {cname} | SUSPECT"
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

        # --- ROI bounding rectangle overlay ------------------------------------
        # Draw a rect encapsulating each ROI polygon so operators can see the
        # monitored zone on the stream. Cyan dashed border + "ROI: name" label.
        if roi_mgr is not None:
            _mw = pipeline_settings.muxer_output_width
            _mh = pipeline_settings.muxer_output_height
            roi_polys = roi_mgr.get_polygons(camera_id)
            for poly in roi_polys:
                if not poly.points:
                    continue
                xs = [p[0] for p in poly.points]
                ys = [p[1] for p in poly.points]
                rx = int(min(xs) * _mw)
                ry = int(min(ys) * _mh)
                rw = int((max(xs) - min(xs)) * _mw)
                rh = int((max(ys) - min(ys)) * _mh)
                if rw < 10 or rh < 10:
                    continue
                try:
                    roi_disp = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
                    roi_disp.num_rects = 1
                    rp = roi_disp.rect_params[0]
                    rp.left = rx
                    rp.top = ry
                    rp.width = rw
                    rp.height = rh
                    rp.border_width = 2
                    rp.border_color.red = 0.0
                    rp.border_color.green = 0.75
                    rp.border_color.blue = 1.0
                    rp.border_color.alpha = 0.6
                    rp.has_bg_color = 0
                    roi_disp.num_labels = 1
                    rtp = roi_disp.text_params[0]
                    rtp.display_text = f"ROI: {poly.name}"
                    rtp.x_offset = rx
                    rtp.y_offset = max(0, ry - 18)
                    rtp.font_params.font_name = "Serif"
                    rtp.font_params.font_size = 11
                    rtp.font_params.font_color.red = 0.0
                    rtp.font_params.font_color.green = 0.75
                    rtp.font_params.font_color.blue = 1.0
                    rtp.font_params.font_color.alpha = 0.8
                    rtp.set_bg_clr = 1
                    rtp.text_bg_clr.red = 0.0
                    rtp.text_bg_clr.green = 0.0
                    rtp.text_bg_clr.blue = 0.0
                    rtp.text_bg_clr.alpha = 0.5
                    pyds.nvds_add_display_meta_to_frame(frame_meta, roi_disp)
                except Exception:
                    pass

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

        # Look up the actual camera name from the source_id via source_manager
        _pad_map = ctx.get("pad_to_camera", {}) if isinstance(ctx, dict) else {}
        camera_id = _pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")
        # nvinfer outputs bbox coords in muxer resolution, NOT source resolution.
        # source_frame_width may be 1280 (original) while rect.left is in 1920 (muxer).
        # Always use muxer dimensions for normalization.
        frame_width = pipeline_settings.muxer_output_width
        frame_height = pipeline_settings.muxer_output_height

        detections: list[dict] = []

        # --- Pre-scan: collect carried_item bboxes for person association ---
        # Used to detect "person carrying item" for fast-track FSM trigger
        carried_item_bboxes: list[tuple[str, dict]] = []  # (class_name, bbox_dict)
        _prescan = frame_meta.obj_meta_list
        while _prescan is not None:
            try:
                _om = pyds.NvDsObjectMeta.cast(_prescan.data)
            except StopIteration:
                break
            _cid = int(_om.class_id)
            _cn = COCO_CLASS_MAP.get(_cid)
            if _cn and CLASS_CATEGORIES.get(_cn) == "carried_item":
                _r = _om.rect_params
                carried_item_bboxes.append((_cn, {
                    "x": _r.left / (frame_meta.source_frame_width or pipeline_settings.input_width),
                    "y": _r.top / (frame_meta.source_frame_height or pipeline_settings.input_height),
                    "w": _r.width / (frame_meta.source_frame_width or pipeline_settings.input_width),
                    "h": _r.height / (frame_meta.source_frame_height or pipeline_settings.input_height),
                }))
            try:
                _prescan = _prescan.next
            except StopIteration:
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
                    in_roi = roi_manager.is_in_any_roi(camera_id, bbox_center_x, bbox_center_y)

                    track_info = track_manager.update_track(camera_id, track_id, class_name, bbox_dict, in_roi)

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

                    fsm_event = fsm_manager.update(
                        camera_id, track_id, class_name,
                        in_roi=in_roi,
                        dwell_time_s=track_info.dwell_time_s,
                        bbox=bbox_dict,
                        has_carried_item=has_carried,
                    )

                    if fsm_event:
                        kafka_producer.send_event(fsm_event)

                        # --- OBSERVE triggers: one-shot per track ---
                        # Get the FSM object to check/set one-shot guards
                        _fsm_obj = fsm_manager._fsms.get((camera_id, track_id))

                        if fsm_event.state == FSMState.OBSERVE and _fsm_obj and not _fsm_obj.observe_triggered:
                            _fsm_obj.observe_triggered = True
                            _category = CLASS_CATEGORIES.get(class_name, "unknown")

                            # Frames captured by VLM worker from RTSP (not here)
                            _frames = []

                            # Send VLM request WITH frame data — VLM will see the
                            # person and generate contextual deterrent with description.
                            kafka_producer.send_vlm_request(
                                camera_id=camera_id, track_id=track_id,
                                zone_name=camera_id, dwell_time_s=track_info.dwell_time_s,
                                class_name=class_name,
                                path_summary=f"observe_trigger:{track_info.dwell_time_s:.0f}s",
                                frames_base64=_frames,
                            )

                            # Start evidence collection
                            _ew = ctx.get("evidence_writer")
                            if _ew:
                                _ew.start_evidence(camera_id, track_id, class_name)

                            # Alert UI (no TTS — wait for VLM)
                            if _category == "vehicle":
                                kafka_producer.send_alert(
                                    camera_id=camera_id, track_id=track_id,
                                    severity="warning",
                                    title=f"Suspicious stop — {class_name} at {camera_id}",
                                    message=f"Vehicle stopped in restricted zone. VLM analyzing.",
                                )

                        # --- VIOLATION triggers: one-shot per track ---
                        elif fsm_event.state == FSMState.VIOLATION and _fsm_obj and not _fsm_obj.violation_triggered:
                            _fsm_obj.violation_triggered = True
                            kafka_producer.send_alert(
                                camera_id=camera_id, track_id=track_id,
                                severity="critical",
                                title=f"Violation — {class_name} at {camera_id}",
                                message=f"Confirmed violation in restricted zone. VLM analyzing.",
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

                        # Flush partial batch on track exit
                        if fsm_event.state.value == "exited":
                            exit_batch = frame_batcher.on_track_exit(camera_id, track_id)
                            if exit_batch:
                                kafka_producer.send_vlm_request(
                                    camera_id=camera_id, track_id=track_id,
                                    zone_name=camera_id, dwell_time_s=track_info.dwell_time_s,
                                    class_name=class_name,
                                    path_summary=f"exit_batch:{len(exit_batch)}frames",
                                )
                            # Finalize evidence — determine outcome based on whether violation was triggered
                            _ew = ctx.get("evidence_writer")
                            if _ew:
                                _was_violation = _fsm_obj.violation_triggered if _fsm_obj else False
                                _outcome = "violation" if _was_violation else "clear"
                                _ew.finalize(
                                    camera_id=camera_id, track_id=track_id,
                                    outcome=_outcome,
                                    dwell_time_s=track_info.dwell_time_s,
                                    action=class_name,
                                )

                    # Enrich detection with tracking data
                    detection_dict["dwell_time_s"] = round(track_info.dwell_time_s, 1)
                    detection_dict["in_roi"] = in_roi
                    _current_fsm = fsm_manager.get_fsm_state(camera_id, track_id)
                    detection_dict["fsm_state"] = _current_fsm
                    # Suspect flag — persists after leaving ROI until track lost
                    detection_dict["is_suspect"] = fsm_manager.is_escalated(camera_id, track_id)

                    # Track evidence bbox path for active events
                    _ew = ctx.get("evidence_writer")
                    if _ew and _current_fsm in ("observe", "violation", "alerting", "escalated"):
                        _ew.add_frame(camera_id, track_id, bbox_dict, float(obj_meta.confidence), _current_fsm)

                    # --- Conditional frame batching for VLM ---
                    # Instead of sending every violation frame to VLM immediately,
                    # collect frames over a 60s window (1 every 10s = 6 frames).
                    # VLM gets temporal context: "what happened over the last minute"
                    current_fsm = detection_dict.get("fsm_state")
                    vlm_batch = frame_batcher.on_frame(
                        camera_id=camera_id,
                        track_id=track_id,
                        frame_num=frame_meta.frame_num,
                        timestamp_ms=int(time.monotonic() * 1000),
                        fsm_state=current_fsm,
                        detections=detections,
                    )
                    if vlm_batch is not None:
                        # Batch ready — send to VLM with temporal context
                        kafka_producer.send_vlm_request(
                            camera_id=camera_id, track_id=track_id,
                            zone_name=camera_id, dwell_time_s=track_info.dwell_time_s,
                            class_name=class_name,
                            path_summary=f"batch:{len(vlm_batch)}frames:{current_fsm}",
                        )

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


def build_pipeline() -> tuple[Gst.Pipeline, Gst.Element, Gst.Element, Gst.Element, bool, Optional[Gst.Element], OutputStreamManager]:
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
    nvinfer = make_element("nvinfer", "primary_infer")
    nvinfer.set_property("config-file-path", "configs/nvinfer_rfdetr.txt")
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

    # --- Link main chain ----------------------------------------------------
    # streammux -> nvinfer -> nvtracker -> nvdsanalytics -> [wastewatchmeta] -> nvosd -> tee
    if not streammux.link(nvinfer):
        logger.critical("Failed to link streammux -> nvinfer")
        sys.exit(1)
    if not nvinfer.link(nvtracker):
        logger.critical("Failed to link nvinfer -> nvtracker")
        sys.exit(1)
    if not nvtracker.link(nvdsanalytics):
        logger.critical("Failed to link nvtracker -> nvdsanalytics")
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

    return pipeline, streammux, fakesink, nvdsanalytics, use_native_meta, ww_meta_elem, output_manager


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    Gst.init(None)

    pipeline, streammux, fakesink, nvdsanalytics, use_native_meta, ww_meta_elem, output_manager = build_pipeline()  # noqa: F841

    kafka_producer = PipelineKafkaProducer()
    health = CameraHealth(timeout_s=30.0, max_retries=5)
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
        roi_preprocessor = ROIPreprocessor(
            roi_manager,
            frame_width=pipeline_settings.input_width,
            frame_height=pipeline_settings.input_height,
        )

        probe_ctx = {
            "kafka_producer": kafka_producer,
            "health": health,
            "track_manager": track_manager,
            "roi_manager": roi_manager,
            "fsm_manager": fsm_manager,
            "roi_preprocessor": roi_preprocessor,
            "frame_batcher": frame_batcher,
            "pad_to_camera": source_manager.pad_to_camera,
            "evidence_writer": evidence_writer,
        }
        fakesink_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            extract_metadata,
            probe_ctx,
        )

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
                {"fsm_manager": fsm_manager, "track_manager": track_manager, "roi_manager": roi_manager, "pad_to_camera": source_manager.pad_to_camera},
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

    # Legacy stream watcher kept for backward compatibility (used in health_check_cb)
    stream_watcher = StreamWatcher(
        source_manager=source_manager,
        health=health,
        poll_interval_s=10.0,
        probe_timeout_s=5.0,
    )

    # Load initial sources from WW_SOURCES env var
    # Format: "cam-01=rtsp://mediamtx:8554/cam-01,cam-02=rtsp://mediamtx:8554/cam-02"
    sources_env = os.environ.get("WW_SOURCES", "")
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
    else:
        logger.warning(
            "No WW_SOURCES configured — pipeline will run with no input sources."
        )

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
            logger.error("GStreamer error: %s | debug: %s", err.message, debug)
            loop.quit()
        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            logger.warning("GStreamer warning: %s | debug: %s", warn.message, debug)
        return True

    bus.connect("message", on_bus_message)

    # Periodic health check — runs every 10 seconds
    def health_check_cb() -> bool:
        # Connection monitor handles health checks, reconnection, and
        # per-camera output stream lifecycle
        connection_monitor.poll()

        if fsm_manager is not None and track_manager is not None:
            # Python fallback path — prune stale FSM entries and lost tracks
            stale_events = fsm_manager.prune_stale()
            for evt in stale_events:
                kafka_producer.send_event(evt)
            lost_tracks = track_manager.prune_lost()
            # Clear suspect highlight when tracker fully loses the object
            for lost in lost_tracks:
                fsm_manager.clear_escalated(lost.camera_id, lost.track_id)

        # Prune stale frame batchers and send partial batches to VLM
        stale_batches = frame_batcher.prune_stale()
        for cam_id, trk_id, frames in stale_batches:
            kafka_producer.send_vlm_request(
                camera_id=cam_id, track_id=trk_id,
                zone_name=cam_id, dwell_time_s=0,
                class_name="unknown",
                path_summary=f"timeout_batch:{len(frames)}frames",
            )

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
        pipeline.set_state(Gst.State.NULL)
        kafka_producer.flush()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
