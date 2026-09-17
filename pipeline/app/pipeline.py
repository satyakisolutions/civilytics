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
from app.confirmation import RollingConfirmation
from app.config import pipeline_settings
from app.overlay import resolve_person_overlay
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
    level=getattr(logging, os.environ.get("WW_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
# httpx logs "HTTP Request: GET ... 200 OK" at INFO for every request (ROI
# sync polls every 10s, camera-info lookups, etc.) -- pure noise next to
# the staging/checkpoint logs (STAGE_TIMING, ATTRIBUTION_SELECTED,
# PERSON_FORCE_VIOLATION, EVIDENCE_CAPTURED). Push it down to DEBUG.
logging.getLogger("httpx").setLevel(logging.WARNING)

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

# RF-DETR-Seg-Nano 4-class indices (post class-0-background-drop, see
# nvdsinfer_custom_impl_Seg/nvdsparseseg_RfdetrSeg.cpp and
# pipeline/configs/labels_seg.txt / nvinfer_rfdetr_seg.txt). Selected
# instead of BBOX_CLASS_MAP when WW_USE_SEG_MODEL=1.
#
# v12 retrain added old_waste back as a real class (bag/new_waste/
# old_waste/person) at channel 2, but old_waste is dropped entirely by
# the custom parser (nvdsparseseg_RfdetrSeg.cpp: kOldWasteChannel) --
# it never becomes an object at all (no bbox, no OSD, no tracker ID),
# so classId 2 is unreachable here. Explicit instruction: "completely
# ignore old waste... don't bbox or highlight it, don't consider it in
# the algorithm too."
SEG_CLASS_MAP: dict[int, str] = {
    0: "bag",
    1: "waste",   # new_waste -- the only waste class that ever reaches here
    3: "person",
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
_PERSON_HIGHLIGHT_DELAY_FRAMES = 0
# Frame spacing between attribution RETRIES when nobody was within
# waste_proximity_px on the previous attempt. The FIRST attempt happens
# immediately on the waste object's first-seen frame (the dumper is most
# likely still right there); only a failed attempt waits this long before
# checking again, instead of retrying every single frame (10x cheaper).
_WASTE_ATTRIBUTION_DELAY_FRAMES = 11  # was 10, +5%
# N-of-M rolling waste confirmation (RollingConfirmation, app/confirmation.py).
# WASTE_CONFIRM_FRAMES=1 (the old value) was not a confirmation at all -- a
# single misclassified frame immediately kicked off the full alert flow.
# Requires the object to be detector-confirmed as "waste" on at least N of
# the M most recent ELIGIBLE observations for this waste_key (an observation
# is recorded once per inference pass where this track_id's frame_num
# advanced -- a gap in frame_num between two sightings backfills that many
# misses, bounded, so an occasional single missed detection doesn't erase
# progress, but a track that's mostly absent still fails to confirm).
# Full confidence/mask-area/position-stability checks are a larger follow-up
# (see deepstream-dev production-hardening audit).
_WASTE_CONFIRM_N = 1
_WASTE_CONFIRM_M = 1
assert 1 <= _WASTE_CONFIRM_N <= _WASTE_CONFIRM_M
# Compact 4-line diagnostic overlay (ATTR/FSM/OSD/CAPTURE) -- always drawn
# on the actual alert-evidence capture window for an active incident; set
# WW_DEBUG_OVERLAY=1 to also draw it continuously for every active incident
# regardless of capture timing (verbose, for live debugging only).
_DEBUG_OVERLAY_ALWAYS = os.environ.get("WW_DEBUG_OVERLAY", "0") == "1"
# Kill switch for alert delivery (Kafka/Telegram/dashboard) -- default
# ENABLED. Dashboard alerts are a hard requirement; set WW_ALERTS_DISABLED=1
# to go back to observation-only mode (logs/overlay, no live alert noise)
# if ever needed for debugging.
_ALERTS_DISABLED = os.environ.get("WW_ALERTS_DISABLED", "0") == "1"
# Multiple call sites fire _fire_waste_alert_async for the same physical
# incident within milliseconds of each other (e.g. the waste-attribution
# path and the generic per-class VIOLATION path both fire off the same
# paired-waste-override transition) -- this collapses near-duplicate
# alerts on the same camera into one instead of double-notifying.
_ALERT_DEDUP_WINDOW_S = 0.8
_alert_dedup_lock = threading.Lock()
_alert_last_fired_at: dict[str, float] = {}
# Above this many frames of lateness, a "captured requested evidence" match
# is flagged as degraded (queue drop, not the exact target) instead of
# logged as a routine success.
_MAX_EVIDENCE_LATENESS_FRAMES = 30
# Give up retrying attribution after this long since the waste object was
# first seen -- previously unbounded, retrying every 10 frames forever if
# nobody ever came within waste_proximity_px. Kept short relative to a
# short looping test source (~37s) so a track that's genuinely never
# approached gives up within roughly half a loop instead of spanning
# almost the entire clip.
_WASTE_ATTRIBUTION_TIMEOUT_S = 15.0

# Same fixed phrase as backend/app/tts/engine.py's TTS_FIXED_DETERRENT --
# kept as a separate literal (not a cross-service import) since pipeline
# and backend are different containers/images. Used for the dashboard
# alert message text, not audio.
_KANNADA_DETERRENT_MESSAGE = "ಎಚ್ಚರಿಕೆ! ಕಸ ಎಸೆಯಬೇಡಿ. ನಿಮ್ಮ ಚಟುವಟಿಕೆ ಕ್ಯಾಮೆರಾದಲ್ಲಿ ದಾಖಲಾಗುತ್ತಿದೆ."
_EVIDENCE_CAPTURE_DELAY_FRAMES = 10
# Hard safety-net: force-expire a waste track_id (as if the tracker had
# lost it) once it's been alive this long, regardless of whether
# DeepStream's tracker naturally reports it lost. A looping test source
# can keep the same track_id continuously alive forever (no last_seen
# gap for prune_lost to ever catch), so age is the only reliable signal
# left to guarantee eventual cleanup.
#
# Deliberately treated as a brand-new incident (full attribution reset,
# not just dwell/bbox bookkeeping) -- explicit product decision: after
# this long, re-open attribution to whoever is nearest now rather than
# keeping the original dumper forever. Chosen knowingly; this is the
# opposite of the "preserve original attribution" fix from earlier this
# session, done here on purpose for this specific timeout.
# Was 35.0. Lowered to match the test rig's short looping clips
# (rtsp-sim-streamer restarts each source every ~10-20s) -- at 35s this
# safety net never fired before the tracker's own 30s shadow-tracking
# window renewed activity first, so attribution/the red mask stuck
# forever instead of ever clearing on a loop restart. Real deployments
# with continuous, non-looping footage would want this higher again.
_WASTE_TRACK_MAX_LIFETIME_S = 15.0


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


def _log_stage(ctx: dict, waste_key: tuple, stage: str, frame_num: int | None = None) -> None:
    """Record and log one incident-lifecycle stage's wall-clock timing.

    Logs elapsed time since the incident's first_seen stage AND since the
    immediately preceding stage, so a slow leg is visible directly in the
    log line instead of requiring manual timestamp subtraction across
    scattered log lines.
    """
    stage_ts = ctx.setdefault("stage_timestamps", {})
    timings = stage_ts.setdefault(waste_key, {})
    now = time.monotonic()
    first_seen = timings.get("first_seen", now)
    prev_stage, prev_ts = None, None
    for _s, _t in timings.items():
        if prev_ts is None or _t > prev_ts:
            prev_stage, prev_ts = _s, _t
    timings[stage] = now
    since_start = now - first_seen
    since_prev = now - prev_ts if prev_ts is not None else 0.0
    logger.debug(
        "STAGE_TIMING waste_key=%s stage=%s frame=%s elapsed_since_start=%.3fs "
        "elapsed_since_prev_stage(%s)=%.3fs",
        waste_key, stage, frame_num if frame_num is not None else "?",
        since_start, prev_stage or "first_seen", since_prev,
    )


def _fire_waste_alert_async(
    kafka_producer, camera_id: str, track_id: int, jpeg_bytes: bytes | None,
    severity: str, title: str, message: str, dumped_by_track_id: int | None,
    dwell_time_s: float, bbox: dict, detected_at: float,
    snapshot_cache: dict[int, bytes] | None = None,
    snapshot_captured_at: dict[int, float] | None = None,
    snapshot_source_id: int | None = None,
    snapshot_visual_pair: dict | None = None,
    snapshot_exact_cache: dict[tuple[int, int], bytes] | None = None,
    snapshot_target_frame: int | None = None,
    snapshot_capture_requests: set[tuple[int, int]] | None = None,
) -> None:
    """Write the snapshot and publish the alert off the GStreamer pad probe.

    No-ops entirely when _ALERTS_DISABLED (WW_ALERTS_DISABLED=1, default)
    -- attribution/FSM/OSD/evidence-capture logic upstream of this call all
    still run unaffected, only Kafka delivery and snapshot writing are
    skipped, so attribution can be observed via logs/overlay in isolation.

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
    if _ALERTS_DISABLED:
        return

    # Collapse near-duplicate alerts on the same camera (e.g. the waste-
    # attribution path and the generic per-class VIOLATION path both
    # firing for the same paired-waste-override transition, milliseconds
    # apart) into a single notification.
    now_monotonic = time.monotonic()
    with _alert_dedup_lock:
        last_fired = _alert_last_fired_at.get(camera_id, 0.0)
        if now_monotonic - last_fired < _ALERT_DEDUP_WINDOW_S:
            logger.debug(
                "Alert deduplicated for %s (%.0fms since last)",
                camera_id, (now_monotonic - last_fired) * 1000,
            )
            return
        _alert_last_fired_at[camera_id] = now_monotonic
    has_snapshot = (
        (bool(jpeg_bytes) and len(jpeg_bytes) >= 1000)
        or (snapshot_cache is not None and snapshot_source_id is not None)
    )
    ts_ms = int(detected_at * 1000)
    snap_url = _alert_snapshot_url(camera_id, track_id, ts_ms) if has_snapshot else None

    def _publish(snapshot_bytes: bytes | None) -> None:
        snapshot_available = bool(snapshot_bytes) and len(snapshot_bytes) >= 1000
        # Persist first so Telegram cannot race the API and receive a
        # transient 404 while trying to fetch the just-published URL.
        if snapshot_available:
            _write_alert_snapshot(camera_id, track_id, ts_ms, snapshot_bytes)
        kafka_producer.send_alert(
            camera_id=camera_id,
            track_id=track_id,
            severity=severity,
            title=title,
            message=message,
            dumped_by_track_id=dumped_by_track_id,
            dwell_time_s=round(dwell_time_s, 1),
            bbox=bbox,
            snapshot_url=snap_url if snapshot_available else None,
            detected_at=detected_at,
        )

    def _run():
        try:
            # Publish the alert NOW with whatever was already captured
            # synchronously on the pad-probe thread -- delivery must never
            # wait on evidence. A capture-branch stall, a dropped frame, a
            # disk error, or the source stopping mid-wait previously meant
            # the operational alert itself was delayed up to 8s (or lost
            # entirely on exception) behind a snapshot that hadn't arrived
            # yet. `jpeg_bytes` is the immediate frame_jpeg_cache entry
            # frozen at fire time -- often not the confirmed red-box frame.
            #
            # `jpeg_bytes` is the immediate frame_jpeg_cache entry frozen at
            # fire time -- often not the confirmed red-box frame, but always
            # available and always timely. A prior attempt to wait briefly
            # for the confirmed frame here made things worse for fast
            # walk-away dumpers: by the time the wait elapsed, the person
            # had already left frame, so the photo showed an empty scene
            # instead of at least showing them (wrong box color but present).
            # Reverted -- publish immediately, unchanged from before.
            _publish(jpeg_bytes)
        except Exception:
            logger.exception("Async waste alert publish failed for %s track=%d", camera_id, track_id)
            return

        # Best-effort evidence upgrade: if a better (exact target-frame, or
        # OSD-confirmed) JPEG becomes available shortly after, overwrite the
        # SAME deterministic file path with it. snap_url never changes, so
        # any consumer that already fetched the immediate frame just gets a
        # sharper/more-accurate one on a later re-fetch; nothing re-publishes
        # to Kafka -- this thread never blocks the alert that already went out.
        try:
            upgraded_bytes: bytes | None = None
            if (
                snapshot_exact_cache is not None
                and snapshot_source_id is not None
                and snapshot_target_frame is not None
            ):
                _target_key = (snapshot_source_id, snapshot_target_frame)
                refresh_deadline = time.monotonic() + 8.0
                while time.monotonic() < refresh_deadline:
                    refreshed = snapshot_exact_cache.pop(_target_key, None)
                    if refreshed and len(refreshed) >= 1000:
                        upgraded_bytes = refreshed
                        break
                    time.sleep(0.01)
                if snapshot_capture_requests is not None:
                    snapshot_capture_requests.discard(_target_key)
                snapshot_exact_cache.pop(_target_key, None)
            elif snapshot_visual_pair is not None:
                refresh_deadline = time.monotonic() + 3.0
                while time.monotonic() < refresh_deadline:
                    rendered_at = snapshot_visual_pair.get("person_visual_rendered_at")
                    captured_at = (
                        snapshot_captured_at.get(snapshot_source_id, 0.0)
                        if snapshot_captured_at is not None and snapshot_source_id is not None
                        else 0.0
                    )
                    if rendered_at is not None and captured_at >= rendered_at:
                        break
                    time.sleep(0.02)
                refreshed = (
                    snapshot_cache.get(snapshot_source_id)
                    if snapshot_cache is not None and snapshot_source_id is not None
                    else None
                )
                if refreshed and len(refreshed) >= 1000:
                    upgraded_bytes = refreshed
            if upgraded_bytes and len(upgraded_bytes) >= 1000:
                _write_alert_snapshot(camera_id, track_id, ts_ms, upgraded_bytes)
        except Exception:
            logger.exception("Evidence upgrade failed for %s track=%d (alert already sent)", camera_id, track_id)

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
    _overlay_causes = ctx.get("person_overlay_causes", {})
    _osd_overlay_confirmed = ctx.get("osd_overlay_confirmed")

    frame_meta_list = batch_meta.frame_meta_list
    while frame_meta_list is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(frame_meta_list.data)
        except StopIteration:
            break

        camera_id = _osd_pad_map.get(frame_meta.source_id, f"cam-{frame_meta.source_id + 1:02d}")

        # Strip nvdsanalytics' own native display_meta -- the ROI polygon
        # outline + zone-name text it self-attaches whenever roi-filtering
        # is configured (nvdsanalytics_config.txt, written by roi_sync.py's
        # _write_nvdsanalytics_config). Runs on nvdsosd's SINK pad, i.e.
        # after nvdsanalytics but before rasterization, so removing its
        # display_meta here only strips the VISUAL overlay -- the actual
        # per-object ROI status metadata nvdsanalytics computes is
        # untouched, and our own in_roi checks use roi_manager.is_in_any_roi
        # independently anyway, not this.
        _existing_disp = frame_meta.display_meta_list
        while _existing_disp is not None:
            try:
                _dm = pyds.NvDsDisplayMeta.cast(_existing_disp.data)
            except StopIteration:
                break
            _next_disp = _existing_disp.next
            pyds.nvds_remove_display_meta_from_frame(frame_meta, _dm)
            _existing_disp = _next_disp

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
        # Per-frame overlay completeness, accumulated across this frame's
        # objects and published to the shared osd_overlay_confirmed dict
        # once the loop finishes -- capture_frame_probe (a different probe,
        # no object-metadata access) reads this to classify a capture as
        # complete (both waste and attributed-person overlays confirmed
        # applied on the SAME rendered buffer) vs PARTIAL.
        _frame_overlay_state: dict[tuple, dict] = {}
        _pending_face_covers: list[tuple] = []
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

            # --- Check suspect flag (escalated track outside ROI) ---
            is_suspect = False
            if fsm_mgr and track_id != 0xFFFFFFFFFFFFFFFF:
                is_suspect = fsm_mgr.is_escalated(camera_id, track_id)

            # As soon as attribution connects a person and waste object,
            # recolour only the waste object's existing NvOSD box. The person
            # intentionally stays in their normal colour until fifteen further
            # frames containing that waste object have been detected;
            # force_violation() marks them red only afterward. No second
            # rectangle is drawn.
            _is_visual_pair = False
            _waste_overlay_key_here = None
            # Set whenever this person IS an attribution target for some
            # waste pair, regardless of is_suspect -- used below to report
            # the ACTUAL rendered state even when it's still wrong (e.g.
            # still yellow OBSERVE), not just when it's already correct.
            _attribution_target_waste_key = None
            if track_id != 0xFFFFFFFFFFFFFFFF:
                for (_pair_camera, _pair_waste_id), _pair in tuple(_waste_pairs.items()):
                    if _pair_camera != camera_id:
                        continue
                    if track_id == _pair_waste_id:
                        _is_visual_pair = True
                        _waste_overlay_key_here = (int(frame_meta.source_id), "waste", _pair_waste_id)
                        break
                    if track_id == _pair.get("person_id"):
                        _attribution_target_waste_key = (int(frame_meta.source_id), "waste", _pair_waste_id)
                        if is_suspect:
                            # Record the exact first OSD render for the
                            # evidence worker. Persistent redness itself
                            # comes from the FSM escalation and lasts until
                            # this person track is lost.
                            _pair.setdefault("person_visual_rendered_at", time.monotonic())
                        break

            # --- Incident-linked overlay cause (independent of fsm_mgr) ---
            # person_overlay_causes[(source_id, "person", track_id)] is a SET
            # of waste_keys currently forcing this person red -- set at the
            # exact moment force_violation(person) fires (pipeline.py's
            # extract_metadata), keyed by the raw source_id int rather than
            # the derived camera_id string both probes independently resolve
            # (a mismatch there -- e.g. a pad_to_camera registration race --
            # would make `is_suspect` alone silently never match here even
            # though the person WAS escalated). This does not replace
            # is_suspect; it's an OR, so either mechanism can force red.
            _person_causes = None
            if category == "person" and track_id != 0xFFFFFFFFFFFFFFFF:
                _person_causes = _overlay_causes.get((int(frame_meta.source_id), "person", track_id))
            # resolve_person_overlay (app/overlay.py) is the actual,
            # independently-unit-tested decision -- pure, no pyds
            # dependency, order-independent (see test_overlay.py).
            _person_decision = resolve_person_overlay(is_suspect, _person_causes) if category == "person" else None
            _has_overlay_cause = bool(_person_causes)

            if _osd_overlay_confirmed is not None:
                if _is_visual_pair and _waste_overlay_key_here is not None:
                    _frame_overlay_state.setdefault(_waste_overlay_key_here, {})["waste_ok"] = True
                if _has_overlay_cause:
                    for _wk in _person_causes:
                        _frame_overlay_state.setdefault(_wk, {})["person_ok"] = True
                        _frame_overlay_state[_wk]["person_track_id"] = track_id

            # --- Build label text ---
            if track_id == 0xFFFFFFFFFFFFFFFF:
                label = cname
            elif category == "person" and _person_decision is not None and _person_decision.is_red:
                # Incident-linked (or generic FSM) violation, decided by
                # resolve_person_overlay -- takes priority over the waste's
                # own _is_visual_pair path below so the label always names
                # the cause explicitly when one exists.
                label = f"#{track_id_short:03d} {cname} | {_person_decision.label_suffix}"
                fsm_state = "violation"
                escalated_count += 1
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
                escalated_count += 1
            elif fsm_state == "idle" and not in_roi:
                # Minimal label for idle objects outside ROI
                label = f"#{track_id_short:03d} {cname}"
            else:
                if fsm_state in ("escalated", "alerting"):
                    escalated_count += 1
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
            # Person boxes: yellow outline while merely suspicious
            # (FSM OBSERVE -- in ROI, dwelling, no waste linked yet), solid
            # red once escalated (attributed dumper: waste found close to
            # them, waste itself inside the ROI). Otherwise no outline.
            # The red case is a solid mask (has_bg_color), not just a
            # border -- pure OSD metadata (rect.bg_color), not real pixel
            # blur, which crashed the pipeline earlier this session and
            # was reverted.
            if category == "person":
                rect.border_width = 0
                _dumper_map_here = ctx.get("waste_dumper_map") or {}
                _is_attributed_here = any(
                    wk[0] == camera_id and pid == track_id
                    for wk, pid in _dumper_map_here.items()
                ) or (fsm_mgr is not None and fsm_mgr.is_forced_by_waste(camera_id, track_id))
                # Red only when this person is actually tied to real
                # evidence -- waste_dumper_map (attribution flow) OR
                # fsm.forced_by_waste (bag proximity+dwell fast-track,
                # which sets the same flag but never touches dumper_map)
                # -- NOT on generic dwell-based FSM "violation" alone,
                # which fires from loitering time only and isn't evidence
                # anyone dumped anything.
                if _is_attributed_here:
                    rect.has_bg_color = 0
                    rect.border_width = 8
                    rect.border_color.red = 1.0
                    rect.border_color.green = 0.0
                    rect.border_color.blue = 0.0
                    rect.border_color.alpha = 1.0
                elif fsm_state == "observe":
                    rect.has_bg_color = 0
                    rect.border_width = 4
                    rect.border_color.red = 1.0
                    rect.border_color.green = 0.85
                    rect.border_color.blue = 0.0
                    rect.border_color.alpha = 1.0
                else:
                    rect.has_bg_color = 0

                # Face-region privacy cover: opaque box over the top ~20%
                # of every person's bbox (rough face-height estimate --
                # there is no dedicated face detector in this pipeline).
                # Deliberately OSD-metadata only (a second display_meta
                # rect), not a real pixel blur -- the NV12->RGBA insertion
                # needed for actual blur crashed the main display chain
                # earlier this session.
                # GPU-mode nvosd only reliably renders rect_params attached
                # to an object meta (proven by the attributed-person border
                # above) -- a frame-level display_meta rect silently failed
                # to render (confirmed live: correct coords logged, nothing
                # drawn). Synthesizing a throwaway obj_meta instead requires
                # calling nvds_add_obj_meta_to_frame, which must NOT happen
                # while we're still manually walking frame_meta.obj_meta_list
                # (mutating the GList mid-traversal risks corrupting/looping
                # the iterator, and a synthesized "person" obj would even be
                # re-visited and recurse). Queue it; added in a second pass
                # once this while loop over obj_meta_list is done.
                # Real pixel-level pixelation (per-camera, post-demux RGBA
                # convert) was tried and reverted -- silently stalled the
                # output stream (see output_manager.py). This OSD-metadata
                # opaque cover is the confirmed-working fallback.
                _face_h = rect.height * 0.20
                if _face_h >= 1 and rect.width >= 1:
                    _pending_face_covers.append(
                        (obj_meta.unique_component_id, obj_meta.class_id,
                         rect.left, rect.top, rect.width, _face_h)
                    )
            else:
                border_w = _STATE_BORDER_WIDTH.get(fsm_state, 2)
                br, bg, bb, ba = _STATE_BORDER_COLOUR.get(fsm_state, _STATE_BORDER_COLOUR["idle"])
                rect.border_width = border_w
                rect.border_color.red = br
                rect.border_color.green = bg
                rect.border_color.blue = bb
                rect.border_color.alpha = ba
                rect.has_bg_color = 0

            # PERSON_OSD_DECORATION: report the ACTUAL rendered state for
            # any person that IS an attribution target (regardless of
            # whether that ended up red or is still stuck yellow -- that's
            # exactly the mismatch this checkpoint exists to catch).
            if category == "person" and _attribution_target_waste_key is not None:
                _rendered_red = fsm_state == "violation"
                _color_word = "RED" if _rendered_red else "YELLOW"
                _overlay_state_word = (
                    "ATTRIBUTED_VIOLATION" if _has_overlay_cause
                    else ("VIOLATED" if _rendered_red else (_STATE_LABEL.get(fsm_state, fsm_state) or fsm_state).upper().replace(" ", "_"))
                )
                logger.debug(
                    "PERSON_OSD_DECORATION source_id=%d frame_num=%d person_track_id=%d "
                    "person_key=%s is_escalated=%s has_overlay_cause=%s final_label=%s final_border=%s",
                    int(frame_meta.source_id), int(frame_meta.frame_num), track_id,
                    (int(frame_meta.source_id), "person", track_id), is_suspect, _has_overlay_cause,
                    label, _color_word.lower(),
                )
                ctx.setdefault("diagnostic_lines", {}).setdefault(_attribution_target_waste_key, {})["osd"] = (
                    f"OSD: P{track_id % 1000} {_color_word} {_overlay_state_word}"
                )

            # --- Dumped item / waste zone highlight ---
            # Fill the bbox with semi-transparent red for:
            #  (a) any waste_object (direct evidence of dumping)
            #  (b) a carried_item belonging to a suspect / escalated track
            # so the operator sees WHAT was dumped, not just the person.
            _highlight = False
            if category == "waste_object":
                # Only mask/highlight waste sitting inside a monitored ROI
                # -- waste outside every ROI is still detected/tracked and
                # labeled, just not drawn or attributed, so the operator's
                # attention stays on zones that actually matter.
                _highlight = in_roi
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
                # Always solid, never blinking -- flicker on the pending
                # state used to double as an evidence-keyframe hazard (a
                # frame grabbed mid "off" phase showed no highlight at
                # all); solid sidesteps that too.
                _confirmed = is_suspect or fsm_state in ("violation", "alerting", "escalated")
                _blink_on = True
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
            # Person: no label at all (no box either, see above).
            if category == "person":
                obj_meta.text_params.display_text = ""
            else:
                txt = obj_meta.text_params
                if category == "waste_object":
                    txt.display_text = "waste | ಕಸ"
                    txt.font_params.font_name = "Noto Sans Kannada"
                    txt.font_params.font_size = 18
                elif category == "carried_item":
                    txt.display_text = "Bag"
                    txt.font_params.font_name = "Noto Sans Kannada"
                    txt.font_params.font_size = 16
                else:
                    txt.display_text = label
                    txt.font_params.font_name = "Serif"
                    txt.font_params.font_size = 11
                txt.x_offset = max(0, int(rect.left))
                txt.y_offset = max(0, int(rect.top) - 26)
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

        # Second pass: apply queued face-region covers now that the
        # obj_meta_list walk above is finished (safe to mutate the frame's
        # object list here).
        for _uc_id, _cls_id, _fleft, _ftop, _fwidth, _fheight in _pending_face_covers:
            try:
                _face_obj = pyds.nvds_acquire_obj_meta_from_pool(batch_meta)
                _face_obj.unique_component_id = _uc_id
                _face_obj.confidence = 1.0
                _face_obj.object_id = 0xFFFFFFFFFFFFFFFF
                _face_obj.class_id = _cls_id
                _forp = _face_obj.rect_params
                _forp.left = _fleft
                _forp.top = _ftop
                _forp.width = _fwidth
                _forp.height = _fheight
                _forp.border_width = 0
                _forp.has_bg_color = 1
                _forp.bg_color.red = 0.05
                _forp.bg_color.green = 0.05
                _forp.bg_color.blue = 0.05
                _forp.bg_color.alpha = 1.0
                _face_obj.text_params.display_text = ""
                _face_obj.text_params.set_bg_clr = 0
                pyds.nvds_add_obj_meta_to_frame(frame_meta, _face_obj, None)
            except Exception:
                logger.exception("face-cover obj-meta failed")

        # Publish this frame's overlay confirmation for any active waste
        # incident touched this frame -- capture_frame_probe (a different
        # probe, pixels only, no object metadata) reads this to classify a
        # capture as complete only when BOTH the waste and its attributed
        # person were actually rendered with their violation overlay on
        # THIS exact buffer, instead of assuming force_violation() having
        # been called implies the render succeeded.
        if _osd_overlay_confirmed is not None:
            _visible_person_ids = [pid for pid, _, _ in person_centers]
            for _wk, _state in _frame_overlay_state.items():
                _osd_overlay_confirmed[(int(frame_meta.source_id), int(frame_meta.frame_num), _wk)] = {
                    "waste_ok": _state.get("waste_ok", False),
                    "person_ok": _state.get("person_ok", False),
                    "person_track_id": _state.get("person_track_id"),
                    "visible_person_ids": _visible_person_ids,
                }

        # --- Compact diagnostic overlay (explicit debug mode only) ---------
        # Four lines, one per correlated checkpoint (ATTR/FSM/OSD/CAPTURE),
        # reporting the actual observed state at each -- not the expected
        # one. Rendered via NvDsDisplayMeta text (DeepStream OSD), never
        # OpenCV, so it only ever exists for the lifetime of this buffer
        # callback -- no pyds metadata is retained past this frame.
        _diag_lines_map = ctx.get("diagnostic_lines")
        # Do not automatically burn this panel into the live stream/evidence
        # while a capture is pending. Besides exposing internal diagnostics
        # to operators, its black background changes the pixels consumed by
        # the post-OSD scene-cut detector at exactly the alert boundary. That
        # could turn the diagnostic panel itself into a false scene cut and
        # clear the attribution on the following frame. It remains available
        # when explicitly requested with WW_DEBUG_OVERLAY=1.
        if _diag_lines_map and _DEBUG_OVERLAY_ALWAYS:
            _sid_now = int(frame_meta.source_id)
            _fnum_now = int(frame_meta.frame_num)
            for _dk, _dlines in list(_diag_lines_map.items()):
                if _dk[0] != _sid_now:
                    continue
                _diag_text = "\n".join([
                    _dlines.get("attr", "ATTR: NONE"),
                    _dlines.get("fsm", "FSM: NONE"),
                    _dlines.get("osd", "OSD: NONE"),
                    _dlines.get("capture", "CAPTURE: PENDING"),
                ])
                try:
                    _diag_disp = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
                    _diag_disp.num_labels = 1
                    _dtp = _diag_disp.text_params[0]
                    _dtp.display_text = _diag_text
                    # Top-left, below the camera timestamp overlay; font
                    # size scales with source resolution so it stays
                    # readable without covering tracked objects on either a
                    # 480p or 4K source.
                    _src_h = int(frame_meta.source_frame_height or pipeline_settings.input_height)
                    _dtp.x_offset = 10
                    _dtp.y_offset = 40
                    _dtp.font_params.font_name = "Monospace"
                    _dtp.font_params.font_size = max(9, min(14, _src_h // 60))
                    _dtp.font_params.font_color.red = 1.0
                    _dtp.font_params.font_color.green = 1.0
                    _dtp.font_params.font_color.blue = 1.0
                    _dtp.font_params.font_color.alpha = 1.0
                    _dtp.set_bg_clr = 1
                    _dtp.text_bg_clr.red = 0.0
                    _dtp.text_bg_clr.green = 0.0
                    _dtp.text_bg_clr.blue = 0.0
                    _dtp.text_bg_clr.alpha = 0.55
                    pyds.nvds_add_display_meta_to_frame(frame_meta, _diag_disp)
                except Exception:
                    pass

        # "Attributed" top-left overlay and the top-right Objects/Escalated
        # stats banner were both removed -- privacy/minimal-UI: dashboard
        # alerting is the source of truth, the video itself only shows the
        # Kannada waste/bag labels and the attributed person's red mask
        # (see the per-object loop above).

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
        logger.debug("capture probe tick n=%d ok=%d no_batch=%d no_map=%d sz_mismatch=%d last_err=%r",
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
    exact_cache: dict[tuple[int, int], bytes] = ctx.get("frame_jpeg_exact_cache", {})
    capture_requests: set[tuple[int, int]] = ctx.get("frame_capture_requests", set())

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
        _frame_key = (int(sid), int(frame_meta.frame_num))
        _requested_now = any(
            _requested_sid == int(sid) and _requested_frame <= int(frame_meta.frame_num)
            for _requested_sid, _requested_frame in tuple(capture_requests)
        )
        if (
            now - _capture_last_ts.get(sid, 0.0) >= _CAPTURE_MIN_INTERVAL_S
            or _requested_now
        ):
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
                        logger.debug("Scene cut detected on source_id=%d (mean diff=%.1f)", _sid, _diff)
                _SCENE_SIG[_sid] = _small

                img = Image.fromarray(bgr)
                if img.width > 1280:
                    new_h = int(img.height * (1280 / img.width))
                    img = img.resize((1280, new_h), Image.BILINEAR)
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=70)
                _jpeg_bytes = out.getvalue()
                cache[frame_meta.source_id] = _jpeg_bytes
                _capture_last_ts[frame_meta.source_id] = now
                _frame_key = (int(frame_meta.source_id), int(frame_meta.frame_num))
                _fulfilled_requests = [
                    _request_key
                    for _request_key in tuple(capture_requests)
                    if _request_key[0] == _frame_key[0] and _request_key[1] <= _frame_key[1]
                ]
                for _request_key in _fulfilled_requests:
                    # Store under the requested target key so the waiting
                    # alert worker can consume it. `requested_frame <=
                    # actual_frame` means this is a nearest-FUTURE capture,
                    # not necessarily an exact one -- if the leaky capture
                    # queue dropped the exact target frame, this could be
                    # many frames later. Report lateness explicitly rather
                    # than implying "exact" for every match.
                    exact_cache[_request_key] = _jpeg_bytes
                    capture_requests.discard(_request_key)
                    _lateness = _frame_key[1] - _request_key[1]
                    if _lateness > _MAX_EVIDENCE_LATENESS_FRAMES:
                        logger.warning(
                            "Captured requested evidence source_id=%d target_frame=%d "
                            "actual_frame=%d lateness=%d frames (exceeds max %d -- "
                            "degraded capture, likely queue drop)",
                            _request_key[0], _request_key[1], _frame_key[1],
                            _lateness, _MAX_EVIDENCE_LATENESS_FRAMES,
                        )
                    else:
                        logger.debug(
                            "Captured requested evidence source_id=%d target_frame=%d "
                            "actual_frame=%d lateness=%d frames",
                            _request_key[0], _request_key[1], _frame_key[1], _lateness,
                        )

                    # EVIDENCE_CAPTURED: classify complete vs PARTIAL using
                    # decorate_for_osd's own rendering confirmation for THIS
                    # exact actual_frame -- not an assumption that
                    # force_violation() having been called implies the
                    # overlay was actually rendered on the captured buffer.
                    _waste_overlay_key_req = ctx.get("frame_capture_waste_key", {}).pop(_request_key, None)
                    if _waste_overlay_key_req is not None:
                        _confirm_map = ctx.get("osd_overlay_confirmed", {})
                        _confirm = _confirm_map.pop(
                            (_request_key[0], _frame_key[1], _waste_overlay_key_req), None
                        )
                        _waste_ok = bool(_confirm and _confirm.get("waste_ok"))
                        _person_ok = bool(_confirm and _confirm.get("person_ok"))
                        _status = "READY" if (_waste_ok and _person_ok) else "PARTIAL"
                        if _status == "PARTIAL":
                            _missing = "ATTRIBUTED_PERSON" if not _person_ok else "WASTE_VIOLATION"
                            logger.debug(
                                "EVIDENCE_CAPTURED source_id=%d target_frame=%d actual_frame=%d "
                                "waste_track_id=%s attributed_person_id=%s visible_person_ids=%s "
                                "status=PARTIAL missing_overlay=%s",
                                _request_key[0], _request_key[1], _frame_key[1],
                                _waste_overlay_key_req[2],
                                _confirm.get("person_track_id") if _confirm else None,
                                _confirm.get("visible_person_ids") if _confirm else [],
                                _missing,
                            )
                        else:
                            logger.debug(
                                "EVIDENCE_CAPTURED source_id=%d target_frame=%d actual_frame=%d "
                                "waste_track_id=%s attributed_person_id=%s visible_person_ids=%s "
                                "status=READY waste_overlay=WASTE_VIOLATION person_overlay=ATTRIBUTED_WASTE_VIOLATION",
                                _request_key[0], _request_key[1], _frame_key[1],
                                _waste_overlay_key_req[2], _confirm.get("person_track_id"),
                                _confirm.get("visible_person_ids"),
                            )
                        ctx.setdefault("diagnostic_lines", {}).setdefault(_waste_overlay_key_req, {})["capture"] = (
                            f"CAPTURE: F{_request_key[1]} TO F{_frame_key[1]} LATE {_lateness}"
                        )
                        _log_stage(ctx, _waste_overlay_key_req, f"evidence_captured_{_status.lower()}", _frame_key[1])
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
        _scene_cut = ctx.get("scene_cut_flags", {}).pop(frame_meta.source_id, False)
        if _scene_cut:
            # The capture branch sees post-OSD pixels.  Never let a visual
            # transition associated with evidence capture (red bbox, labels,
            # or an explicitly enabled debug panel) clear an active incident.
            # A genuine scene boundary becomes eligible once the attributed
            # person is no longer being observed.
            _now_mono = time.monotonic()
            _live_attributions = []
            for (_wc, _wt), _pid in ctx.get("waste_dumper_map", {}).items():
                if _wc != camera_id or _pid is None:
                    continue
                _person_track = track_manager.get_track(camera_id, _pid)
                if _person_track is not None and _now_mono - _person_track.last_seen <= 2.0:
                    _live_attributions.append((_wt, _pid))
            if _live_attributions:
                logger.info(
                    "SCENE CUT RESET SUPPRESSED %s: active_attributions=%s",
                    camera_id, _live_attributions,
                )
                _scene_cut = False

        if _scene_cut:
            logger.info("Resetting track/FSM state for %s after confirmed scene cut", camera_id)
            track_manager.reset_camera(camera_id)
            fsm_manager.reset_camera(camera_id)
            for _d in (
                ctx.get("waste_dumper_map"),
                ctx.get("waste_attribution_pair"),
                ctx.get("waste_confirm_windows"),
                ctx.get("waste_confirm_last_frame"),
                ctx.get("waste_attribution_due_frame"),
                ctx.get("waste_person_violation_frames_remaining"),
                ctx.get("waste_repeat_alert_at"),
            ):
                if _d:
                    for _k in [k for k in _d if k[0] == camera_id]:
                        del _d[_k]
            # person_overlay_causes/osd_overlay_confirmed are keyed by
            # source_id, not camera_id -- clear this source's entries.
            _sid = int(frame_meta.source_id)
            _overlay_causes_map = ctx.get("person_overlay_causes")
            if _overlay_causes_map:
                for _pk in [k for k in _overlay_causes_map if k[0] == _sid]:
                    del _overlay_causes_map[_pk]
            _overlay_confirmed_map = ctx.get("osd_overlay_confirmed")
            if _overlay_confirmed_map:
                for _ck in [k for k in _overlay_confirmed_map if k[0] == _sid]:
                    del _overlay_confirmed_map[_ck]
            for _d in (ctx.get("stage_timestamps"), ctx.get("diagnostic_lines")):
                if _d:
                    for _k in [k for k in _d if k[0] == _sid]:
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
        _cam_enter_threshold_s = _dcfg.get(
            "dwell_enter_threshold_s", pipeline_settings.dwell_enter_threshold_s
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

        # bag_proximity_px setup -- edge-to-edge, source-pixel measurement,
        # used below (per-person, main object loop) to gate has_carried.
        _cam_bag_proximity_px = _dcfg.get(
            "bag_proximity_px", pipeline_settings.bag_proximity_px
        )
        _bag_source_w = float(frame_meta.source_frame_width or frame_width)
        _bag_source_h = float(frame_meta.source_frame_height or frame_height)
        _bag_x_to_source = _bag_source_w / float(frame_width)
        _bag_y_to_source = _bag_source_h / float(frame_height)

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

                    # The ONE log kept at INFO -- fires exactly ONCE per
                    # waste track_id: the FIRST frame where this waste
                    # object and at least one person are BOTH present
                    # together (same frame_num), not every frame they
                    # co-occur and not each one's own separate first
                    # sighting. person_bboxes is fully populated by this
                    # point (built in the pre-scan pass before this main
                    # per-object loop starts).
                    if CLASS_CATEGORIES.get(class_name) == "waste_object" and person_bboxes and in_roi:
                        _co_key = (camera_id, track_id)
                        _co_seen = ctx.setdefault("person_waste_co_seen", set())
                        if _co_key not in _co_seen:
                            _co_seen.add(_co_key)
                            logger.info(
                                "PERSON_AND_WASTE_TOGETHER %s waste_track=%d frame=%d person_count=%d",
                                camera_id, track_id, int(frame_meta.frame_num), len(person_bboxes),
                            )

                    track_info = track_manager.update_track(camera_id, track_id, class_name, bbox_dict, in_roi)

                    # Attribute to whoever is closest, resolved at a fixed
                    # frame offset after the waste object was first seen --
                    # no distance threshold. Waiting for someone to come
                    # within a fixed px gap could take many seconds if the
                    # dumper wanders (observed ~17s in practice), even
                    # though the closest person at any given moment right
                    # after the drop is virtually always the actual dumper.
                    # This resolves deterministically ~_WASTE_ATTRIBUTION_
                    # DELAY_FRAMES after first detection, always.
                    _dumper_map = ctx.setdefault("waste_dumper_map", {})
                    _waste_confirm_windows: dict[tuple, RollingConfirmation] = ctx.setdefault("waste_confirm_windows", {})
                    _waste_confirm_last_frame = ctx.setdefault("waste_confirm_last_frame", {})
                    _person_violation_frames_map = ctx.setdefault(
                        "waste_person_violation_frames_remaining", {}
                    )
                    _waste_key = (camera_id, track_id)
                    _attrib_due_map = ctx.setdefault("waste_attribution_due_frame", {})
                    if (
                        class_name == "waste"
                        and in_roi
                        and _waste_key not in _attrib_due_map
                        and _waste_key not in _dumper_map
                        and _waste_key not in ctx.get("waste_reminders_suppressed", set())
                    ):
                        # Check immediately on first sight -- this is when
                        # the actual dumper is most likely still standing
                        # right there. Delaying the FIRST check gives them
                        # time to walk away, so a random nearby bystander
                        # gets attributed instead once the delay elapses.
                        # Only a failed attempt's RETRY (below) is spaced
                        # out, not this initial one.
                        _attrib_due_map[_waste_key] = int(frame_meta.frame_num)
                        _log_stage(ctx, (int(frame_meta.source_id), "waste", track_id), "first_seen", int(frame_meta.frame_num))
                    if (
                        class_name == "waste"
                        and in_roi
                        and _waste_key in _attrib_due_map
                        and int(frame_meta.frame_num) >= _attrib_due_map[_waste_key]
                        and _waste_key not in _dumper_map
                        and not fsm_manager.is_escalated(camera_id, track_id)
                    ):
                        _attrib_due_map.pop(_waste_key, None)
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
                            _dx0 = max(_pbox0[0] - _wbox0[2], _wbox0[0] - _pbox0[2], 0.0)
                            _dy0 = max(_pbox0[1] - _wbox0[3], _wbox0[1] - _pbox0[3], 0.0)
                            # Calibrate from muxer coordinates to the source
                            # image: distances are measured in the camera's
                            # actual pixel space regardless of streammux
                            # scaling/resolution.
                            _d0 = math.hypot(
                                _dx0 * _x_to_source0,
                                _dy0 * _y_to_source0,
                            )
                            if _best_d0 is None or _d0 < _best_d0:
                                _best_d0, _best_pid0, _best_pbox0 = _d0, _pid0, _pbox0
                        # Distance gate still applies -- closest-at-a-fixed-
                        # frame without one confidently blamed people who
                        # were genuinely far away (observed gap=130px+).
                        # If nobody qualifies yet, re-arm for another check
                        # _WASTE_ATTRIBUTION_DELAY_FRAMES later -- UNLESS
                        # either give-up condition is met, in which case
                        # stop retrying entirely (leave _waste_key out of
                        # _attrib_due_map so this block never re-enters for
                        # this track): (a) nobody is visible in frame at all
                        # right now -- no point re-checking against an empty
                        # scene, or (b) _WASTE_ATTRIBUTION_TIMEOUT_S has
                        # elapsed since first_seen -- previously unbounded,
                        # retrying every 10 frames forever for an item
                        # nobody ever approaches.
                        if _best_d0 is not None and _best_d0 <= _cam_waste_proximity_px:
                            # Incident lock: the first valid spatial match is
                            # the person present at placement time. Never
                            # replace that ID with whoever happens to walk
                            # closest to the stationary waste later; evidence
                            # capture must use this same locked subject.
                            _dumper_map[_waste_key] = _best_pid0
                        else:
                            _best_pid0, _best_pbox0 = None, None
                            _first_seen_ts = ctx.get("stage_timestamps", {}).get(
                                (int(frame_meta.source_id), "waste", track_id), {}
                            ).get("first_seen")
                            _timed_out = (
                                _first_seen_ts is not None
                                and time.monotonic() - _first_seen_ts >= _WASTE_ATTRIBUTION_TIMEOUT_S
                            )
                            # Retry only until the first valid match. Once a
                            # person is selected, the condition above prevents
                            # this block from running again for the incident.
                            if _timed_out:
                                logger.debug(
                                    "WASTE ATTRIBUTION ABANDONED %s frame=%d waste=%d "
                                    "reason=timeout_%.0fs -- no longer retrying",
                                    camera_id, int(frame_meta.frame_num), track_id,
                                    _WASTE_ATTRIBUTION_TIMEOUT_S,
                                )
                            else:
                                _attrib_due_map[_waste_key] = int(frame_meta.frame_num) + _WASTE_ATTRIBUTION_DELAY_FRAMES
                        if _best_pid0 is not None:
                            _waste_confirm_windows[_waste_key] = RollingConfirmation(
                                _WASTE_CONFIRM_N, _WASTE_CONFIRM_M
                            )
                            _waste_confirm_last_frame[_waste_key] = int(frame_meta.frame_num)
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
                            logger.debug(
                                "WASTE ATTRIBUTION CAPTURE %s frame=%d waste=%d person=%d "
                                "gap=%.1fpx threshold=%.1fpx source=%dx%d",
                                camera_id, int(frame_meta.frame_num), track_id, _best_pid0,
                                _best_d0, _cam_waste_proximity_px, int(_source_w0), int(_source_h0),
                            )
                            logger.info(
                                "ATTRIBUTION_SELECTED source_id=%d frame_num=%d waste_track_id=%d "
                                "attributed_person_id=%d waste_key=%s person_key=%s",
                                int(frame_meta.source_id), int(frame_meta.frame_num), track_id, _best_pid0,
                                _waste_key, (camera_id, "person", _best_pid0),
                            )
                            ctx.setdefault("diagnostic_lines", {}).setdefault(
                                (int(frame_meta.source_id), "waste", track_id), {}
                            )["attr"] = (
                                f"ATTR: S{int(frame_meta.source_id)} F{int(frame_meta.frame_num)} "
                                f"W{track_id % 1000} P{_best_pid0 % 1000}"
                            )
                            _log_stage(ctx, (int(frame_meta.source_id), "waste", track_id), "attributed", int(frame_meta.frame_num))
                        else:
                            logger.debug(
                                "WASTE ATTRIBUTION CAPTURE %s frame=%d waste=%d person=none "
                                "nearest_gap=%s threshold=%.1fpx source=%dx%d -- retrying in %d frames",
                                camera_id, int(frame_meta.frame_num), track_id,
                                "n/a" if _best_d0 is None else f"{_best_d0:.1f}px",
                                _cam_waste_proximity_px, int(_source_w0), int(_source_h0),
                                _WASTE_ATTRIBUTION_DELAY_FRAMES,
                            )

                    # N-of-M rolling confirmation (RollingConfirmation): the
                    # waste object must be detector-confirmed as "waste" on
                    # at least _WASTE_CONFIRM_N of its last _WASTE_CONFIRM_M
                    # eligible observations before its own FSM is forced into
                    # VIOLATION -- NOT a bare 1-frame counter (a single
                    # misclassified frame used to be enough), NOT wall-clock
                    # time, and NOT the full 15s dwell_violation_threshold_s
                    # (that's for PERSON loitering/dwell behavior, unrelated
                    # FSM, still intact below). This step only marks the
                    # waste track itself as confirmed and starts the
                    # person's highlight countdown -- it does NOT fire the
                    # alert. The alert fires later, exactly when the
                    # person's red violation box actually renders (see the
                    # highlight block below).
                    #
                    # This per-object loop only runs when class_name=="waste"
                    # was actually detected this frame, so a genuine "miss"
                    # (the object briefly not detected) can't be observed
                    # directly here. Approximate it from the frame_num gap
                    # since the last sighting: each skipped frame between
                    # sightings backfills one miss (bounded to M so a scene
                    # cut or long occlusion can't retroactively erase an
                    # otherwise-confirmable window with an enormous gap).
                    _window = _waste_confirm_windows.get(_waste_key)
                    if (
                        class_name == "waste"
                        and _window is not None
                        and not _window.already_fired
                        and _dumper_map.get(_waste_key) is not None
                        and not fsm_manager.is_escalated(camera_id, track_id)
                    ):
                        _cur_frame = int(frame_meta.frame_num)
                        _last_frame = _waste_confirm_last_frame.get(_waste_key, _cur_frame)
                        _gap_misses = min(max(_cur_frame - _last_frame - 1, 0), _WASTE_CONFIRM_M)
                        for _ in range(_gap_misses):
                            _window.observe(False)
                        _window.observe(True)
                        _waste_confirm_last_frame[_waste_key] = _cur_frame
                        if _window.should_fire():
                            _window.mark_fired()
                            fsm_manager.force_violation(
                                camera_id, track_id, class_name,
                                dwell_time_s=track_info.dwell_time_s,
                                bbox=bbox_dict,
                            )
                            _log_stage(ctx, (int(frame_meta.source_id), "waste", track_id), "waste_confirmed", int(frame_meta.frame_num))
                            # Seed the repeat-reminder timer HERE, not only
                            # when the alert eventually fires post-highlight
                            # (~_PERSON_HIGHLIGHT_DELAY_FRAMES frames later).
                            # force_violation() above immediately puts this
                            # waste track's own FSM into "violation", which
                            # the periodic repeat-reminder check (further
                            # down, keyed on fsm_state in violation/alerting/
                            # escalated) sees right away. An unseeded entry
                            # in that gap defaults to 0.0, so the reminder
                            # fired a "still unresolved" alert before the
                            # actual first alert had even gone out.
                            ctx.setdefault("waste_repeat_alert_at", {})[_waste_key] = time.monotonic()
                            _person_violation_frames_map[_waste_key] = _PERSON_HIGHLIGHT_DELAY_FRAMES

                    # Count only frames where this exact waste track is
                    # actually detected. Global frame numbers can advance
                    # while a tiny waste object is temporarily missed, which
                    # previously allowed the person to turn red on a frame
                    # where no waste box was visible.
                    _person_frames_left = _person_violation_frames_map.get(_waste_key)
                    if class_name == "waste" and _person_frames_left is not None:
                        _person_frames_left -= 1
                        if _person_frames_left <= 0:
                            _person_violation_frames_map.pop(_waste_key, None)
                            _person_pid = _dumper_map.get(_waste_key)
                            if _person_pid is not None:
                                _person_track = track_manager.get_track(camera_id, _person_pid)
                                # Liveness guard: this fires
                                # _PERSON_HIGHLIGHT_DELAY_FRAMES frames after
                                # the waste was confirmed, counted only on
                                # frames where the WASTE was seen -- the
                                # PERSON could already be gone (occluded,
                                # tracker lost them) by the time this timer
                                # expires. Firing anyway produced a
                                # dwell=0.0 violation immediately followed by
                                # an "exited" event for a track that was
                                # already dead -- a spurious alert for noise,
                                # confirmed live via the FSM log. Skip if the
                                # person hasn't actually been seen in the
                                # last ~1s (a few frames' grace, not an
                                # instant cutoff).
                                _person_stale = (
                                    _person_track is None
                                    or time.monotonic() - _person_track.last_seen > 1.0
                                )
                                if _person_stale:
                                    logger.debug(
                                        "PERSON_FORCE_VIOLATION_SKIPPED source_id=%d frame_num=%d "
                                        "person=%d reason=stale_track",
                                        int(frame_meta.source_id), int(frame_meta.frame_num), _person_pid,
                                    )
                                    _person_pid = None
                            if _person_pid is not None:
                                _person_bbox = (
                                    _person_track.bbox_history[-1]
                                    if _person_track is not None and _person_track.bbox_history
                                    else None
                                )
                                # Keyed by frame_meta.source_id (a raw int
                                # intrinsic to the buffer) rather than the
                                # derived camera_id string -- extract_metadata
                                # and decorate_for_osd each independently
                                # resolve camera_id from source_id via
                                # source_manager.pad_to_camera, and if those
                                # two resolutions ever disagreed for the same
                                # physical source (registration race, stale
                                # cache), a camera_id-keyed cause would never
                                # be found by the other probe. source_id
                                # itself can't disagree -- it's the same field
                                # on the same buffer.
                                _person_key = (int(frame_meta.source_id), "person", _person_pid)
                                _state_before = fsm_manager.get_fsm_state(camera_id, _person_pid)
                                fsm_manager.force_violation(
                                    camera_id, _person_pid, "person",
                                    dwell_time_s=(
                                        _person_track.dwell_time_s if _person_track is not None else 0.0
                                    ),
                                    bbox=_person_bbox,
                                )
                                _state_after = fsm_manager.get_fsm_state(camera_id, _person_pid)
                                logger.debug(
                                    "PERSON_FORCE_VIOLATION source_id=%d frame_num=%d "
                                    "requested_person_key=%s fsm_key=(%s,%d) state_before=%s state_after=%s",
                                    int(frame_meta.source_id), int(frame_meta.frame_num), _person_key,
                                    camera_id, _person_pid, _state_before, _state_after,
                                )
                                _log_stage(ctx, (int(frame_meta.source_id), "waste", _waste_key[1]), "person_highlighted_alert_fired", int(frame_meta.frame_num))
                                _waste_overlay_key = (int(frame_meta.source_id), "waste", _waste_key[1])
                                ctx.setdefault("diagnostic_lines", {}).setdefault(_waste_overlay_key, {})["fsm"] = (
                                    f"FSM: P{_person_pid % 1000} {(_state_before or 'NONE').upper()} "
                                    f"TO {(_state_after or 'NONE').upper()}"
                                )
                                # Incident-linked overlay cause: independent of
                                # the generic person FSM. Reference-counted by
                                # set membership so a second, unrelated waste
                                # incident later attributing the SAME person
                                # remains independently represented. Once a
                                # person is marked, waste exit/loss must NOT
                                # clear their red box: its visual lifetime is
                                # owned by the person's tracker lifetime and
                                # ends only on person loss or a scene reset.
                                _overlay_causes = ctx.setdefault("person_overlay_causes", {})
                                _overlay_causes.setdefault(_person_key, set()).add(_waste_overlay_key)
                                # Fire the alert HERE -- specifically right
                                # after the red bbox + violation label just
                                # rendered for the person. frame_num is
                                # intrinsic to the buffer (assigned once by
                                # the source), not branch-relative, so a
                                # target of current_frame + _EVIDENCE_CAPTURE_
                                # DELAY_FRAMES is always safely in the
                                # pipeline's future relative to right now --
                                # no extra wait needed before requesting it.
                                # The alert payload is generated immediately
                                # (message, severity, attribution -- all
                                # already known); the evidence JPEG is
                                # requested for a frame a few frames from now
                                # and captured in parallel on a background
                                # thread (_fire_waste_alert_async's worker
                                # waits on snapshot_exact_cache for that exact
                                # frame, writes it, THEN publishes the Kafka
                                # message) -- so the payload and the evidence
                                # image are produced concurrently, and the
                                # alert is only actually sent once the image
                                # is ready.
                                ctx.setdefault("waste_repeat_alert_at", {})[(camera_id, track_id)] = time.monotonic()
                                _visual_pair = ctx.get("waste_attribution_pair", {}).get(_waste_key)
                                _cache = ctx.get("frame_jpeg_cache", {})
                                _snapshot_source_id = int(frame_meta.source_id)
                                _snapshot_target_frame = (
                                    int(frame_meta.frame_num) + _EVIDENCE_CAPTURE_DELAY_FRAMES
                                )
                                _snapshot_target_key = (
                                    _snapshot_source_id, _snapshot_target_frame
                                )
                                ctx.setdefault("frame_capture_requests", set()).add(
                                    _snapshot_target_key
                                )
                                ctx.setdefault("frame_capture_waste_key", {})[_snapshot_target_key] = _waste_overlay_key
                                _who = f"person #{_person_pid % 1000:03d}"
                                _fire_waste_alert_async(
                                    kafka_producer, camera_id, _waste_key[1], _cache.get(frame_meta.source_id),
                                    severity="critical",
                                    title="Waste dumping confirmed",
                                    message=f"Waste dumped by {_who} — item detected in monitored zone",
                                    dumped_by_track_id=_person_pid,
                                    dwell_time_s=track_info.dwell_time_s,
                                    bbox=bbox_dict,
                                    detected_at=time.time(),
                                    snapshot_cache=_cache,
                                    snapshot_captured_at=_capture_last_ts,
                                    snapshot_source_id=_snapshot_source_id,
                                    snapshot_visual_pair=_visual_pair,
                                    snapshot_exact_cache=ctx.get("frame_jpeg_exact_cache"),
                                    snapshot_target_frame=_snapshot_target_frame,
                                    snapshot_capture_requests=ctx.get("frame_capture_requests"),
                                )
                                logger.debug(
                                    "WASTE ALERT (post-highlight) %s waste=%d person=%d: dumped by %s; "
                                    "trigger_frame=%d evidence_frame=%d",
                                    camera_id, _waste_key[1], _person_pid, _who,
                                    int(frame_meta.frame_num), _snapshot_target_frame,
                                )
                        else:
                            _person_violation_frames_map[_waste_key] = _person_frames_left
                    # Check if this person is within bag_proximity_px of a
                    # carried_item -- edge-to-edge, source-pixel space, same
                    # measurement as the waste/person attribution gate
                    # below (was a padded normalized-bbox overlap test,
                    # which didn't match the configured px threshold at all).
                    has_carried = False
                    if class_name == "person" and carried_item_rects:
                        _person_box = (rect.left, rect.top, rect.left + rect.width, rect.top + rect.height)
                        for _ibox1 in carried_item_rects:
                            _dx1 = max(_person_box[0] - _ibox1[2], _ibox1[0] - _person_box[2], 0.0)
                            _dy1 = max(_person_box[1] - _ibox1[3], _ibox1[1] - _person_box[3], 0.0)
                            _d1 = math.hypot(_dx1 * _bag_x_to_source, _dy1 * _bag_y_to_source)
                            if _d1 <= _cam_bag_proximity_px:
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
                            enter_threshold_s=_cam_enter_threshold_s,
                            speed_px_s=track_info.speed_px_s,
                        )
                    else:
                        fsm_event = fsm_manager.update(
                            camera_id, track_id, class_name,
                            speed_px_s=track_info.speed_px_s,
                            in_roi=in_roi,
                            dwell_time_s=track_info.dwell_time_s,
                            bbox=bbox_dict,
                            violation_threshold_s=_cam_violation_threshold_s,
                            enter_threshold_s=_cam_enter_threshold_s,
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

                            # VLM analysis disabled -- alerting is pure
                            # logic (FSM/dwell/ROI/attribution) now, not
                            # gated on a VLM confirmation. ENTER no longer
                            # submits anything; the actual alert fires on
                            # VIOLATION below.
                            pass

                        # OBSERVE (yellow bbox, "suspicious"): fire a
                        # warning-severity alert, one-shot per track.
                        elif fsm_event.state == FSMState.OBSERVE and _fsm_obj and not _fsm_obj.observe_triggered:
                            _fsm_obj.observe_triggered = True
                            _cache = ctx.get("frame_jpeg_cache", {})
                            _jpeg = _cache.get(frame_meta.source_id)
                            _fire_waste_alert_async(
                                kafka_producer,
                                camera_id=camera_id,
                                track_id=track_id,
                                jpeg_bytes=_jpeg,
                                severity="warning",
                                title="Suspicious activity",
                                message=f"{class_name} suspicious (loitering) at {camera_id}",
                                dumped_by_track_id=None,
                                dwell_time_s=fsm_event.dwell_time_s,
                                bbox=bbox_dict,
                                detected_at=time.time(),
                            )

                        # VIOLATION (red bbox): fire a critical alert
                        # directly, one-shot per track -- pure logic, no
                        # VLM confirmation required.
                        elif fsm_event.state == FSMState.VIOLATION and _fsm_obj and not _fsm_obj.violation_triggered:
                            _fsm_obj.violation_triggered = True

                            _category = CLASS_CATEGORIES.get(class_name, "unknown")
                            _actionable = (
                                class_name in VEHICLE_CLASSES or
                                _category in ("person", "carried_item", "waste_object")
                            )
                            _cache = ctx.get("frame_jpeg_cache", {})
                            _jpeg = _cache.get(frame_meta.source_id)

                            # The waste-attribution flow above
                            # (_fire_waste_alert_async triggered from actual
                            # dumping evidence) still fires its own,
                            # separately-attributed critical alert -- this
                            # is the generic per-class dwell/bag-violation
                            # path's own alert, covering the cases that
                            # never went through waste attribution (bag
                            # dwell, non-person classes, person dwell
                            # fallback).
                            _fire_waste_alert_async(
                                kafka_producer,
                                camera_id=camera_id,
                                track_id=track_id,
                                jpeg_bytes=_jpeg if _actionable else None,
                                severity="critical",
                                title="Violation confirmed",
                                message=f"{class_name} confirmed in violation at {camera_id}",
                                dumped_by_track_id=None,
                                dwell_time_s=fsm_event.dwell_time_s,
                                bbox=bbox_dict,
                                detected_at=time.time(),
                            )
                            _fsm_obj.vlm_fired = True

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
                                logger.debug(
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
                            # incident or reset its confirm-frame counter.
                            if CLASS_CATEGORIES.get(class_name) == "waste_object":
                                # class_name is the raw COCO label (e.g.
                                # "bottle", "cup"), never the literal string
                                # "waste" -- the old `== "waste"` check never
                                # matched a real detection, so this map entry
                                # was never cleaned up on the waste object's
                                # own exit.
                                #
                                # Deliberately NOT popping waste_dumper_map
                                # here -- same reasoning as the lost_tracks
                                # health-check cleanup: this FSM "exited"
                                # event fires on the WASTE object's own
                                # track loss (small bbox, flickers/occludes
                                # easily), and the red-mask lookup in
                                # decorate_for_osd reads this map keyed by
                                # (camera, waste_track). Popping it here was
                                # clearing the still-visible violator's mask
                                # the moment the waste item next lost its
                                # tracker lock. Now only cleared when the
                                # attributed PERSON's own track is lost.
                                ctx.get("person_waste_co_seen", set()).discard((camera_id, track_id))
                                ctx.get("waste_attribution_pair", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_confirm_windows", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_confirm_last_frame", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_attribution_due_frame", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_person_violation_frames_remaining", {}).pop((camera_id, track_id), None)
                                ctx.get("waste_reminders_suppressed", set()).discard((camera_id, track_id))
                                _exited_waste_key = (int(frame_meta.source_id), "waste", track_id)
                                # Deliberately retain person_overlay_causes.
                                # The waste detector can briefly lose a small
                                # or occluded item while its attributed person
                                # is still plainly visible. Clearing the cause
                                # here made the person's red box last only a
                                # few frames. Person-track loss owns cleanup.
                                ctx.get("stage_timestamps", {}).pop(_exited_waste_key, None)
                                ctx.get("diagnostic_lines", {}).pop(_exited_waste_key, None)
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
                        and (camera_id, track_id) not in ctx.get("waste_reminders_suppressed", set())
                    ):
                        _repeat_key = (camera_id, track_id)
                        _repeat_state = ctx.setdefault("waste_repeat_alert_at", {})
                        _now_mono = time.monotonic()
                        # Default to NOW, not 0.0 -- an unseeded key (this
                        # incident's very first violation tick) previously
                        # looked "infinitely overdue" against 0.0, firing a
                        # "Reminder: ... still not cleared" alert seconds
                        # BEFORE the real first "Waste dumping confirmed"
                        # alert. Confirmed live via DB timestamps. Starting
                        # the clock at "just now" means a genuinely new
                        # violation waits one full interval before its
                        # first reminder, same as every subsequent one.
                        _last_fire = _repeat_state.setdefault(_repeat_key, _now_mono)
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
                            logger.debug(
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
    logger.debug(f"primary_infer config: {pgie_config} (WW_USE_SEG_MODEL={'1' if use_seg_model else '0'})")
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
                logger.debug("Using native C wastewatchmeta plugin for metadata processing")
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
    #
    # Tried process-mode=0 (CPU/Pango+Cairo, which shapes Indic combining
    # vowel signs correctly) to fix garbled Kannada labels -- it breaks
    # caps negotiation upstream (GST_PAD_LINK_NOFORMAT in source_manager's
    # decodebin pad-added handler, RTSP source never links, pipeline dies
    # within 3s of every restart). Confirmed live. Do not retry without a
    # real caps-fix for the whole chain, not just nvosd's property.
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
    # leaky=2 (downstream): per `gst-inspect-1.0 queue`, "downstream"
    # leaks/drops OLD buffers already queued, keeping the newest ones --
    # "upstream" (1) does the opposite, dropping NEW arriving buffers and
    # keeping stale ones queued. An earlier pass through this code had
    # these backwards (set to 1, believing it dropped old buffers) --
    # that actually made the capture branch prefer STALE frames under
    # load, which is the wrong direction for alert-evidence freshness.
    queue_capture.set_property("leaky", 2)
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
    # One-shot exact-frame evidence. Metadata schedules frame N+20, the
    # capture branch stores that JPEG here even if its normal 200ms cache
    # throttle would skip it, and the async alert worker consumes it.
    frame_jpeg_exact_cache: dict[tuple[int, int], bytes] = {}
    frame_capture_requests: set[tuple[int, int]] = set()
    # Shared with capture_frame_probe (separate tee branch, separate probe
    # ctx dict) -- set True per source_id when a scene-cut is detected,
    # consumed (and cleared) by extract_metadata to reset that camera's
    # track/FSM state. See capture_frame_probe's scene-cut detection block.
    scene_cut_flags: dict[int, bool] = {}
    # Shared between metadata attribution and the pre-NvOSD decoration probe.
    # It lets the waste object's original box turn red immediately. The
    # person's box is deliberately left unchanged until fifteen further frames
    # containing the confirmed waste object have passed.
    waste_attribution_pair: dict[tuple[str, int], dict] = {}
    # (camera_id, waste_track_id) -> attributed person_track_id. Written by
    # extract_metadata at attribution time, read by decorate_for_osd for the
    # always-on "Attributed" overlay -- pre-declared here (like the other
    # shared dicts below) so both probes share the SAME object; extract_metadata
    # still creates it lazily via ctx.setdefault(...), which is a no-op once
    # this key is already present.
    waste_dumper_map: dict[tuple[str, int], int] = {}
    # Incident-linked overlay causes: person_key -> set of waste_keys
    # currently forcing that person's box red. Reference-counted by
    # incident (set membership) so clearing ONE waste incident (via
    # person_overlay_remove_cause) never wipes out another active
    # incident's claim on the same person, and the render decision never
    # depends solely on the generic person FSM (fsm_manager.is_escalated).
    # Keys are fully qualified: (camera_id, "person"|"waste", track_id).
    person_overlay_causes: dict[tuple[str, str, int], set] = {}
    # Per-frame overlay confirmation written by decorate_for_osd (which has
    # object metadata) and read by capture_frame_probe (which only has raw
    # pixels) to classify a capture as complete vs PARTIAL. Keyed by
    # (source_id, frame_num, waste_key) -> {"waste_ok", "person_ok",
    # "person_track_id"}. Bounded: only written for frames where an active
    # waste incident's objects were actually seen, popped once consumed.
    osd_overlay_confirmed: dict[tuple, dict] = {}
    # (source_id, target_frame) -> waste_key, so capture_frame_probe can
    # look up which incident a given evidence-capture request belongs to
    # for the EVIDENCE_CAPTURED log and completeness check.
    frame_capture_waste_key: dict[tuple[int, int], tuple] = {}
    # Four-line diagnostic overlay text per waste_overlay_key, updated at
    # each of the four correlated checkpoints (ATTR/FSM in extract_metadata,
    # OSD in decorate_for_osd, CAPTURE in capture_frame_probe) and rendered
    # by decorate_for_osd only on alert-evidence frames or when
    # WW_DEBUG_OVERLAY=1 -- never on the normal production stream.
    diagnostic_lines: dict[tuple, dict[str, str]] = {}
    # Per-incident wall-clock stage timings (_log_stage), shared between
    # extract_metadata and capture_frame_probe so "elapsed_since_prev_stage"
    # spans both probes and actually shows where a slow leg is.
    stage_timestamps: dict[tuple, dict[str, float]] = {}

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

        logger.debug("Metadata probe: using optimized extract_metadata_v2 (C plugin handles ROI/FSM/tracking)")
    else:
        # --- Python fallback path: full Python managers ----------------------
        # Was 6s (coincidental reuse), then 30s (matched nvtracker's shadow
        # window), then 15s (matched the test rig's loop length) -- all
        # made the mask clear later than the person actually exiting.
        # Explicit requirement: clear as close to the real exit as
        # possible, only a short (~2s) grace period for a brief real
        # occlusion, not for matching a loop cycle or a long shadow window.
        track_manager = TrackStateManager(lost_timeout_s=2.0)
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
            person_violation_threshold_s=pipeline_settings.person_dwell_violation_threshold_s,
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
            "frame_jpeg_exact_cache": frame_jpeg_exact_cache,
            "frame_capture_requests": frame_capture_requests,
            "roi_syncer": roi_syncer,
            "scene_cut_flags": scene_cut_flags,
            "waste_attribution_pair": waste_attribution_pair,
            "person_overlay_causes": person_overlay_causes,
            "osd_overlay_confirmed": osd_overlay_confirmed,
            "frame_capture_waste_key": frame_capture_waste_key,
            "diagnostic_lines": diagnostic_lines,
            "stage_timestamps": stage_timestamps,
            "waste_dumper_map": waste_dumper_map,
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
                {
                    "frame_jpeg_cache": frame_jpeg_cache,
                    "frame_jpeg_exact_cache": frame_jpeg_exact_cache,
                    "frame_capture_requests": frame_capture_requests,
                    "scene_cut_flags": scene_cut_flags,
                    "osd_overlay_confirmed": osd_overlay_confirmed,
                    "frame_capture_waste_key": frame_capture_waste_key,
                    "diagnostic_lines": diagnostic_lines,
                    "stage_timestamps": stage_timestamps,
                },
            )
            logger.debug("Frame capture probe attached to fakesink_capture.sink")

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
                    "person_overlay_causes": person_overlay_causes,
                    "osd_overlay_confirmed": osd_overlay_confirmed,
                    "frame_capture_requests": frame_capture_requests,
                    "frame_capture_waste_key": frame_capture_waste_key,
                    "diagnostic_lines": diagnostic_lines,
                    "waste_dumper_map": waste_dumper_map,
                },
            )
            logger.debug("OSD decoration probe attached to nvdsosd.sink")

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
                logger.debug("Restoring dynamically-added camera %s (%s) from backend", cam_id, uri)
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
                    logger.debug("Camera %s: ROI crop saves ~%.0f%% GPU inference", cam_id, pct)

    # GLib main loop
    loop = GLib.MainLoop()

    # Bus message handler
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_bus_message(bus: Gst.Bus, message: Gst.Message) -> bool:  # noqa: ARG001
        msg_type = message.type
        if msg_type == Gst.MessageType.EOS:
            logger.debug("End-of-stream received — stopping")
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
            # camera_id -> source_id, for translating TrackInfo.camera_id
            # (that's all prune_lost gives us) into the source_id-qualified
            # keys person_overlay_causes uses.
            _cam_to_source = {v: k for k, v in source_manager.pad_to_camera.items()}
            # Hard safety-net timeout: force-expire any waste track whose
            # first_seen (stage_timestamps) is older than
            # _WASTE_TRACK_MAX_LIFETIME_S, even if the DeepStream tracker
            # itself never naturally loses it (prune_lost only fires on
            # last_seen staleness -- a looping test source, or any object
            # that just sits there, keeps the SAME track_id alive
            # indefinitely, with no natural gap for prune_lost to catch).
            #
            # This does NOT feed into the standard lost_tracks cleanup
            # loop below -- that loop clears waste_dumper_map/
            # waste_attribution_pair -- deliberately, as of this version:
            # after _WASTE_TRACK_MAX_LIFETIME_S the incident is treated as
            # fully NEW, exactly as if the tracker had lost and re-found
            # the object. The tracker itself never loses it (same
            # track_id, continuously visible on a looping/static test
            # source), so this is the only way to get a second, distinct
            # attribution pass on the same physical item -- explicit
            # product choice, not the earlier same-40s "preserve original
            # attribution" behavior (that fix still applies to *unwanted*
            # re-attribution churn; this is an intentional periodic reset).
            _pad_to_cam = source_manager.pad_to_camera
            for _wk in list(probe_ctx.get("stage_timestamps", {}).keys()):
                if _wk[1] != "waste":
                    continue
                _first_seen_ts = probe_ctx["stage_timestamps"][_wk].get("first_seen")
                if _first_seen_ts is None:
                    continue
                if time.monotonic() - _first_seen_ts < _WASTE_TRACK_MAX_LIFETIME_S:
                    continue
                _cam = _pad_to_cam.get(_wk[0])
                if _cam is None:
                    continue
                _fe_key = (_cam, _wk[2])
                # Don't reset an incident whose attributed person is still
                # actively standing in frame right now -- narrower than the
                # earlier "attribution lock" (reverted: it also froze
                # re-attribution against a closer bystander, which broke
                # wrong-person alerting). This only guards the 35s force-
                # expire's mask/dumper_map clear; re-attribution to a
                # currently-closer person still happens normally elsewhere.
                _fe_dumper_pid = probe_ctx.get("waste_dumper_map", {}).get(_fe_key)
                if _fe_dumper_pid is not None:
                    _fe_person_track = track_manager.get_track(_cam, _fe_dumper_pid)
                    if _fe_person_track is not None and time.monotonic() - _fe_person_track.last_seen <= 2.0:
                        continue
                if track_manager.force_expire(_cam, _wk[2]) is not None:
                    logger.debug(
                        "WASTE TRACK FORCE-EXPIRED %s track=%d after %.0fs (max lifetime) "
                        "-- treated as new incident, attribution reopened",
                        _cam, _wk[2], _WASTE_TRACK_MAX_LIFETIME_S,
                    )
                    fsm_manager.clear_escalated(_cam, _wk[2])
                    probe_ctx.get("waste_dumper_map", {}).pop(_fe_key, None)
                    probe_ctx.get("person_waste_co_seen", set()).discard(_fe_key)
                    probe_ctx.get("waste_attribution_pair", {}).pop(_fe_key, None)
                    probe_ctx.get("waste_confirm_windows", {}).pop(_fe_key, None)
                    probe_ctx.get("waste_confirm_last_frame", {}).pop(_fe_key, None)
                    probe_ctx.get("waste_attribution_due_frame", {}).pop(_fe_key, None)
                    probe_ctx.get("waste_person_violation_frames_remaining", {}).pop(_fe_key, None)
                    probe_ctx.get("waste_repeat_alert_at", {}).pop(_fe_key, None)
                    # Artificial max-age expiry must not reopen an old,
                    # stationary waste item and attribute the next passerby.
                    # Natural waste-track loss (below) removes suppression,
                    # allowing a genuinely new track/incident to start.
                    probe_ctx.setdefault("waste_reminders_suppressed", set()).add(_fe_key)
                    probe_ctx.get("stage_timestamps", {}).pop(_wk, None)
                    probe_ctx.get("diagnostic_lines", {}).pop(_wk, None)
            # Clear suspect highlight when tracker fully loses the object
            for lost in lost_tracks:
                fsm_manager.clear_escalated(lost.camera_id, lost.track_id)
                _lost_source_id = _cam_to_source.get(lost.camera_id)
                if CLASS_CATEGORIES.get(lost.class_name) == "waste_object":
                    _lost_key = (lost.camera_id, lost.track_id)
                    # Deliberately NOT popping waste_dumper_map here -- the
                    # red-mask lookup in decorate_for_osd reads this map, and
                    # waste bboxes are small/frequently occluded, losing
                    # their track on a blip while the attributed person is
                    # still fully visible in frame. That was clearing the
                    # violator's mask mid-frame while they kept walking
                    # around unmasked. The map entry is now only cleared
                    # when the attributed PERSON's own track is lost (below)
                    # or on the periodic force-expire "new incident" reset.
                    probe_ctx.get("person_waste_co_seen", set()).discard(_lost_key)
                    probe_ctx.get("waste_attribution_pair", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_confirm_windows", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_confirm_last_frame", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_attribution_due_frame", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_person_violation_frames_remaining", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_repeat_alert_at", {}).pop(_lost_key, None)
                    probe_ctx.get("waste_reminders_suppressed", set()).discard(_lost_key)
                    if _lost_source_id is not None:
                        _lost_waste_overlay_key = (_lost_source_id, "waste", lost.track_id)
                        # Keep the attributed person's overlay cause until
                        # that PERSON track is lost. Waste detections are
                        # small and frequently disappear behind occlusion;
                        # their loss must not turn a still-visible violator
                        # yellow again.
                        probe_ctx.get("stage_timestamps", {}).pop(_lost_waste_overlay_key, None)
                        probe_ctx.get("diagnostic_lines", {}).pop(_lost_waste_overlay_key, None)
                elif CLASS_CATEGORIES.get(lost.class_name) == "person":
                    if _lost_source_id is not None:
                        probe_ctx.get("person_overlay_causes", {}).pop(
                            (_lost_source_id, "person", lost.track_id), None
                        )
                    # Preserve waste attribution (dumped_by_track_id stays
                    # in the message/DB for the record), but end the visual
                    # lifetime when this person actually leaves the frame so
                    # a future recycled tracker ID is not coloured, AND stop
                    # the repeat-reminder cycle for whichever waste_key they
                    # were attributed for -- continuing to fire fresh
                    # snapshots after the dumper is gone just captures
                    # whichever unrelated bystander happens to walk by next,
                    # which reads as a wrong-person alert even though
                    # attribution itself was correct.
                    for _wk, _dumper_pid in list(probe_ctx.get("waste_dumper_map", {}).items()):
                        if _dumper_pid == lost.track_id:
                            # Add to the suppression set rather than popping
                            # the repeat-alert timer -- the periodic check
                            # (further up) defaults a MISSING timer to 0.0,
                            # which would make it fire immediately instead
                            # of stopping (same bug class fixed earlier this
                            # session for the confirm-time seed). Explicit
                            # suppression is checked before the timer at all.
                            probe_ctx.setdefault("waste_reminders_suppressed", set()).add(_wk)
                            # The person who earned the red mask is gone --
                            # this is the ONE place waste_dumper_map should
                            # drop the entry now (see the waste_object-lost
                            # branch above for why it's not cleared there).
                            probe_ctx["waste_dumper_map"].pop(_wk, None)
                            logger.info(
                                "ATTRIBUTED PERSON LEFT VIEW %s person=%d waste=%d -- red bbox cleared",
                                lost.camera_id, lost.track_id, _wk[1],
                            )
                    for _pair in probe_ctx.get("waste_attribution_pair", {}).values():
                        if _pair.get("person_id") == lost.track_id:
                            _pair.pop("person_visual_rendered_at", None)
                    # Reset the attributed waste object's own dwell clock --
                    # otherwise it keeps climbing for as long as its (often
                    # static, looping-video) track persists, producing
                    # nonsensical "stationary 800s+" reminders long after the
                    # dumper who was actually present is gone. (This call
                    # site was dropped during an earlier attribution rewrite
                    # this session -- reset_dwell() existed but had zero
                    # callers; re-added here against the current
                    # waste_dumper_map-based attribution.)
                    for _wc, _wt in list(probe_ctx.get("waste_dumper_map", {}).keys()):
                        if _wc == lost.camera_id and probe_ctx["waste_dumper_map"].get((_wc, _wt)) == lost.track_id:
                            track_manager.reset_dwell(lost.camera_id, _wt)

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

    logger.debug("Pipeline running — press Ctrl-C to stop")
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.debug("Interrupted by user")
    finally:
        logger.debug("Shutting down pipeline")
        command_receiver.stop()
        pipeline.set_state(Gst.State.NULL)
        kafka_producer.flush()
        logger.debug("Shutdown complete")


if __name__ == "__main__":
    main()
