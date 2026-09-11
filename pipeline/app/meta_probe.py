# pipeline/app/meta_probe.py
"""Optimized metadata extraction using WasteWatch GStreamer plugin.

Replaces the original extract_metadata pad probe. Instead of computing
ROI, FSM, and tracking in Python, this reads pre-computed metadata from
the wastewatchmeta C plugin and publishes to Kafka.

The C plugin processes metadata ~10x faster than Python pad probes because:
1. No Python GIL contention on the GStreamer streaming thread
2. C-native point-in-polygon and FSM state machine
3. Metadata is attached in-pipeline, accessible by all downstream elements

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

import logging
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import pyds  # noqa: E402

from app.health import CameraHealth
from app.kafka_producer import PipelineKafkaProducer
from app.meta_bindings import (
    FSM_STATE_NAMES,
    FsmState,
    WasteWatchFrameInfo,
    WasteWatchObjectInfo,
    get_frame_meta,
    iter_enriched_objects,
)

logger = logging.getLogger(__name__)

# Vehicle classes that should trigger LPR requests.
_VEHICLE_CLASSES = frozenset({"car", "motorcycle", "bus", "truck"})


def create_probe_context(
    kafka_producer: PipelineKafkaProducer,
    health: CameraHealth,
) -> dict:
    """Create the simplified probe context for ``extract_metadata_v2``.

    Unlike the original probe context this does **not** carry a
    ``ROIManager``, ``TrackStateManager``, or ``EventFSMManager`` because
    all of those computations are now performed in C by the
    ``wastewatchmeta`` plugin.

    Args:
        kafka_producer: Kafka producer instance for publishing metadata,
            events, VLM and LPR requests.
        health: CameraHealth tracker to record frame arrivals.

    Returns:
        A dict suitable for passing as ``user_data`` to
        ``Gst.Pad.add_probe``.
    """
    return {
        "kafka_producer": kafka_producer,
        "health": health,
    }


def _build_detection_dict(
    obj_meta: pyds.NvDsObjectMeta,
    ww_info: WasteWatchObjectInfo,
    frame_width: int,
    frame_height: int,
) -> dict:
    """Build a detection dictionary from DeepStream + WasteWatch metadata.

    Normalises the bounding box to [0, 1] coordinates and merges in the
    enriched fields computed by the C plugin.
    """
    rect = obj_meta.rect_params
    bbox_dict = {
        "x": round(rect.left / frame_width, 6),
        "y": round(rect.top / frame_height, 6),
        "w": round(rect.width / frame_width, 6),
        "h": round(rect.height / frame_height, 6),
    }

    return {
        "class_id": int(obj_meta.class_id),
        "label": ww_info.class_name,
        "confidence": round(float(obj_meta.confidence), 4),
        "bbox": bbox_dict,
        "track_id": ww_info.track_id if ww_info.track_id != 0xFFFFFFFFFFFFFFFF else None,
        "in_roi": ww_info.in_roi,
        "roi_name": ww_info.roi_name,
        "dwell_time_s": round(ww_info.dwell_time_s, 1),
        "track_age_s": round(ww_info.track_age_s, 1),
        "fsm_state": FSM_STATE_NAMES.get(ww_info.fsm_state, "unknown"),
        "is_violation": ww_info.is_violation,
    }


def _handle_fsm_transition(
    kafka_producer: PipelineKafkaProducer,
    ww_info: WasteWatchObjectInfo,
    camera_id: str,
    bbox_dict: dict,
) -> None:
    """Publish Kafka messages triggered by an FSM state change.

    Called only when ``ww_info.fsm_state_changed`` is ``True``.  Publishes
    an FSM event and, when the new state is ``VIOLATION``, fires VLM and
    (for vehicles) LPR requests.
    """
    # Publish FSM transition event
    kafka_producer.send_event(
        _FsmEventAdapter(
            camera_id=camera_id,
            track_id=ww_info.track_id,
            state_value=FSM_STATE_NAMES.get(ww_info.fsm_state, "unknown"),
            prev_state_value=FSM_STATE_NAMES.get(ww_info.fsm_prev_state, "unknown"),
            dwell_time_s=ww_info.dwell_time_s,
            class_name=ww_info.class_name,
            bbox=bbox_dict,
        )
    )

    # Trigger VLM + LPR on violation
    if ww_info.fsm_state == FsmState.VIOLATION:
        path_summary = "stationary" if ww_info.dwell_time_s > 10 else "moving"
        kafka_producer.send_vlm_request(
            camera_id=camera_id,
            track_id=ww_info.track_id,
            zone_name=ww_info.roi_name or camera_id,
            dwell_time_s=ww_info.dwell_time_s,
            class_name=ww_info.class_name,
            path_summary=path_summary,
        )

        if ww_info.class_name in _VEHICLE_CLASSES:
            kafka_producer.send_lpr_request(
                camera_id=camera_id,
                track_id=ww_info.track_id,
                vehicle_bbox=bbox_dict,
                class_name=ww_info.class_name,
            )


class _FsmEventAdapter:
    """Lightweight adapter that matches the interface expected by
    ``PipelineKafkaProducer.send_event``.

    The Kafka producer accesses ``.camera_id``, ``.track_id``,
    ``.state.value``, ``.prev_state.value``, ``.dwell_time_s``,
    ``.class_name``, ``.bbox``, and ``.timestamp``.  This adapter
    exposes those attributes without importing the full
    ``EventFSMManager`` machinery.
    """

    __slots__ = (
        "camera_id",
        "track_id",
        "state",
        "prev_state",
        "dwell_time_s",
        "class_name",
        "bbox",
        "timestamp",
    )

    def __init__(
        self,
        camera_id: str,
        track_id: int,
        state_value: str,
        prev_state_value: str,
        dwell_time_s: float,
        class_name: str,
        bbox: dict | None,
    ) -> None:
        self.camera_id = camera_id
        self.track_id = track_id
        self.state = _StrValue(state_value)
        self.prev_state = _StrValue(prev_state_value)
        self.dwell_time_s = dwell_time_s
        self.class_name = class_name
        self.bbox = bbox
        self.timestamp = time.time()


class _StrValue:
    """Tiny wrapper so ``obj.state.value`` returns a plain string."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


def extract_metadata_v2(
    pad: Gst.Pad,
    info: Gst.PadProbeInfo,
    ctx: dict,
) -> Gst.PadProbeReturn:
    """GStreamer pad probe that reads pre-computed WasteWatch metadata.

    This replaces the original ``extract_metadata`` probe.  All ROI,
    FSM, and tracking computations are done in C by the
    ``wastewatchmeta`` plugin.  This probe simply reads the enriched
    ``NvDsUserMeta``, builds detection dicts, and publishes to Kafka.

    Args:
        pad: The GStreamer pad this probe is attached to (unused but
            required by the GStreamer callback signature).
        info: Probe info containing the GstBuffer.
        ctx: Context dict created by ``create_probe_context``.

    Returns:
        ``Gst.PadProbeReturn.OK`` to let the buffer continue downstream.
    """
    kafka_producer: PipelineKafkaProducer = ctx["kafka_producer"]
    health: CameraHealth = ctx["health"]

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

        # Determine camera_id — prefer the C plugin's value if available.
        frame_info: WasteWatchFrameInfo | None = get_frame_meta(frame_meta)
        if frame_info is not None and frame_info.camera_id:
            camera_id = frame_info.camera_id
        else:
            camera_id = f"cam-{frame_meta.source_id:02d}"

        frame_width = frame_meta.source_frame_width or 1920
        frame_height = frame_meta.source_frame_height or 1080

        detections: list[dict] = []

        for obj_meta, ww_info in iter_enriched_objects(frame_meta):
            bbox_dict = {
                "x": round(obj_meta.rect_params.left / frame_width, 6),
                "y": round(obj_meta.rect_params.top / frame_height, 6),
                "w": round(obj_meta.rect_params.width / frame_width, 6),
                "h": round(obj_meta.rect_params.height / frame_height, 6),
            }

            detection = _build_detection_dict(
                obj_meta, ww_info, frame_width, frame_height
            )
            detections.append(detection)

            # Handle FSM state transitions — publish events, VLM/LPR requests.
            if ww_info.fsm_state_changed:
                _handle_fsm_transition(
                    kafka_producer, ww_info, camera_id, bbox_dict
                )

        # Build and publish the per-frame metadata payload.
        metadata: dict = {
            "camera_id": camera_id,
            "frame_num": frame_meta.frame_num,
            "timestamp_ms": int(time.monotonic() * 1000),
            "detections": detections,
        }

        # Append frame-level summary from the C plugin when available.
        if frame_info is not None:
            metadata["summary"] = {
                "total_detections": frame_info.total_detections,
                "objects_in_roi": frame_info.objects_in_roi,
                "active_violations": frame_info.active_violations,
                "active_tracks": frame_info.active_tracks,
            }

        kafka_producer.send_metadata(camera_id, metadata)
        kafka_producer.poll()
        health.on_frame(camera_id)

        try:
            frame_meta_list = frame_meta_list.next
        except StopIteration:
            break

    return Gst.PadProbeReturn.OK
