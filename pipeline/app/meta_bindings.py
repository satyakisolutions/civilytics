# pipeline/app/meta_bindings.py
"""Python bindings for WasteWatch custom GStreamer plugin metadata.

The C plugin `wastewatchmeta` attaches enriched metadata (ROI, FSM, dwell,
tracking) to each NvDsObjectMeta as NvDsUserMeta. This module provides
Python accessors to read that metadata from the DeepStream buffer.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""
import ctypes
import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Optional

import pyds

logger = logging.getLogger(__name__)

# Must match NVDS_USER_META_WASTEWATCH in gstwastewatchmeta.h
# This is computed by nvds_get_user_meta_type("NVIDIA.NVINFER.USER_META_WASTEWATCH")
# We look it up dynamically at runtime.
_WASTEWATCH_META_TYPE: int | None = None

# Frame-level meta uses a separate type tag.
_WASTEWATCH_FRAME_META_TYPE: int | None = None


class FsmState(IntEnum):
    """Mirror of WwFsmState enum from gstwastewatchmeta.h."""
    IDLE = 0
    ENTER = 1
    OBSERVE = 2
    VIOLATION = 3
    ALERTING = 4
    ESCALATED = 5
    EXITED = 6


# Map integer FSM states back to their string names for Kafka messages.
FSM_STATE_NAMES: dict[int, str] = {s.value: s.name.lower() for s in FsmState}


class WasteWatchObjectMetaC(ctypes.Structure):
    """ctypes mirror of WasteWatchObjectMeta from gstwastewatchmeta.h."""
    _fields_ = [
        ("in_roi", ctypes.c_int),
        ("roi_name", ctypes.c_char * 64),
        ("fsm_state", ctypes.c_int),
        ("fsm_prev_state", ctypes.c_int),
        ("fsm_state_changed", ctypes.c_int),
        ("track_id", ctypes.c_uint64),
        ("class_name", ctypes.c_char * 64),
        ("dwell_time_s", ctypes.c_float),
        ("track_age_s", ctypes.c_float),
        ("bbox_center_x", ctypes.c_float),
        ("bbox_center_y", ctypes.c_float),
        ("is_violation", ctypes.c_int),
        ("needs_vlm", ctypes.c_int),
        ("needs_lpr", ctypes.c_int),
        ("first_seen_ts", ctypes.c_double),
        ("roi_entered_ts", ctypes.c_double),
        ("frame_ts", ctypes.c_double),
    ]


class WasteWatchFrameMetaC(ctypes.Structure):
    """ctypes mirror of WasteWatchFrameMeta from gstwastewatchmeta.h."""
    _fields_ = [
        ("camera_id", ctypes.c_char * 32),
        ("total_detections", ctypes.c_uint),
        ("objects_in_roi", ctypes.c_uint),
        ("active_violations", ctypes.c_uint),
        ("active_tracks", ctypes.c_uint),
        ("frame_ts", ctypes.c_double),
    ]


@dataclass
class WasteWatchObjectInfo:
    """Python-friendly representation of WasteWatchObjectMeta."""
    in_roi: bool
    roi_name: str
    fsm_state: FsmState
    fsm_prev_state: FsmState
    fsm_state_changed: bool
    track_id: int
    class_name: str
    dwell_time_s: float
    track_age_s: float
    bbox_center_x: float
    bbox_center_y: float
    is_violation: bool
    needs_vlm: bool
    needs_lpr: bool
    first_seen_ts: float
    roi_entered_ts: float
    frame_ts: float


@dataclass
class WasteWatchFrameInfo:
    """Python-friendly representation of WasteWatchFrameMeta."""
    camera_id: str
    total_detections: int
    objects_in_roi: int
    active_violations: int
    active_tracks: int
    frame_ts: float


def get_wastewatch_meta_type() -> int:
    """Dynamically resolve the NVDS_USER_META_WASTEWATCH type ID.

    The DeepStream user-meta type is derived from a string tag via
    ``pyds.nvds_get_user_meta_type``.  The result is cached so the hash
    computation only runs once per process.

    Returns:
        Integer meta-type identifier used to tag WasteWatch object-level
        user metadata.
    """
    global _WASTEWATCH_META_TYPE
    if _WASTEWATCH_META_TYPE is None:
        _WASTEWATCH_META_TYPE = pyds.nvds_get_user_meta_type(
            "NVIDIA.NVINFER.USER_META_WASTEWATCH"
        )
        logger.info(
            "Resolved NVDS_USER_META_WASTEWATCH type: 0x%08X",
            _WASTEWATCH_META_TYPE,
        )
    return _WASTEWATCH_META_TYPE


def _get_wastewatch_frame_meta_type() -> int:
    """Dynamically resolve the frame-level WasteWatch meta type ID.

    Separate from the object-level type so the probe can distinguish the
    two when iterating NvDsUserMeta lists.
    """
    global _WASTEWATCH_FRAME_META_TYPE
    if _WASTEWATCH_FRAME_META_TYPE is None:
        _WASTEWATCH_FRAME_META_TYPE = pyds.nvds_get_user_meta_type(
            "NVIDIA.NVINFER.USER_META_WASTEWATCH_FRAME"
        )
        logger.info(
            "Resolved NVDS_USER_META_WASTEWATCH_FRAME type: 0x%08X",
            _WASTEWATCH_FRAME_META_TYPE,
        )
    return _WASTEWATCH_FRAME_META_TYPE


def _cast_to_object_meta(user_meta_data_ptr: int) -> WasteWatchObjectMetaC:
    """Cast a raw pointer (as Python int) to a WasteWatchObjectMetaC struct."""
    return ctypes.cast(
        ctypes.c_void_p(user_meta_data_ptr),
        ctypes.POINTER(WasteWatchObjectMetaC),
    ).contents


def _cast_to_frame_meta(user_meta_data_ptr: int) -> WasteWatchFrameMetaC:
    """Cast a raw pointer (as Python int) to a WasteWatchFrameMetaC struct."""
    return ctypes.cast(
        ctypes.c_void_p(user_meta_data_ptr),
        ctypes.POINTER(WasteWatchFrameMetaC),
    ).contents


def _c_object_to_info(c_meta: WasteWatchObjectMetaC) -> WasteWatchObjectInfo:
    """Convert a ctypes C struct into the Python dataclass representation."""
    return WasteWatchObjectInfo(
        in_roi=bool(c_meta.in_roi),
        roi_name=c_meta.roi_name.decode("utf-8", errors="replace").rstrip("\x00"),
        fsm_state=FsmState(c_meta.fsm_state),
        fsm_prev_state=FsmState(c_meta.fsm_prev_state),
        fsm_state_changed=bool(c_meta.fsm_state_changed),
        track_id=int(c_meta.track_id),
        class_name=c_meta.class_name.decode("utf-8", errors="replace").rstrip("\x00"),
        dwell_time_s=float(c_meta.dwell_time_s),
        track_age_s=float(c_meta.track_age_s),
        bbox_center_x=float(c_meta.bbox_center_x),
        bbox_center_y=float(c_meta.bbox_center_y),
        is_violation=bool(c_meta.is_violation),
        needs_vlm=bool(c_meta.needs_vlm),
        needs_lpr=bool(c_meta.needs_lpr),
        first_seen_ts=float(c_meta.first_seen_ts),
        roi_entered_ts=float(c_meta.roi_entered_ts),
        frame_ts=float(c_meta.frame_ts),
    )


def _c_frame_to_info(c_meta: WasteWatchFrameMetaC) -> WasteWatchFrameInfo:
    """Convert a ctypes C frame struct into the Python dataclass."""
    return WasteWatchFrameInfo(
        camera_id=c_meta.camera_id.decode("utf-8", errors="replace").rstrip("\x00"),
        total_detections=int(c_meta.total_detections),
        objects_in_roi=int(c_meta.objects_in_roi),
        active_violations=int(c_meta.active_violations),
        active_tracks=int(c_meta.active_tracks),
        frame_ts=float(c_meta.frame_ts),
    )


def get_object_meta(
    obj_meta: pyds.NvDsObjectMeta,
) -> Optional[WasteWatchObjectInfo]:
    """Extract WasteWatchObjectInfo from an NvDsObjectMeta, if present.

    Iterates ``obj_meta.obj_user_meta_list`` looking for a NvDsUserMeta
    whose ``meta_type`` matches the WasteWatch object-level type.  When
    found the raw ``user_meta_data`` pointer is cast to
    ``WasteWatchObjectMetaC`` via ctypes and converted to a Python-friendly
    ``WasteWatchObjectInfo`` dataclass.

    Args:
        obj_meta: A DeepStream NvDsObjectMeta from the batch metadata.

    Returns:
        ``WasteWatchObjectInfo`` if the C plugin attached metadata to this
        object, otherwise ``None``.
    """
    ww_type = get_wastewatch_meta_type()
    user_meta_list = obj_meta.obj_user_meta_list
    while user_meta_list is not None:
        try:
            user_meta = pyds.NvDsUserMeta.cast(user_meta_list.data)
        except StopIteration:
            break

        if user_meta.meta_type == ww_type:
            try:
                ptr = ctypes.cast(
                    ctypes.c_void_p(user_meta.user_meta_data),
                    ctypes.POINTER(WasteWatchObjectMetaC),
                )
                c_meta = ptr.contents
                return _c_object_to_info(c_meta)
            except Exception:
                logger.exception(
                    "Failed to cast WasteWatch object user meta for track %d",
                    obj_meta.object_id,
                )
                return None

        try:
            user_meta_list = user_meta_list.next
        except StopIteration:
            break

    return None


def get_frame_meta(
    frame_meta: pyds.NvDsFrameMeta,
) -> Optional[WasteWatchFrameInfo]:
    """Extract WasteWatchFrameInfo from an NvDsFrameMeta, if present.

    Iterates ``frame_meta.frame_user_meta_list`` looking for the
    WasteWatch frame-level NvDsUserMeta.  When found the raw pointer is
    cast to ``WasteWatchFrameMetaC`` and converted to
    ``WasteWatchFrameInfo``.

    Args:
        frame_meta: A DeepStream NvDsFrameMeta from the batch metadata.

    Returns:
        ``WasteWatchFrameInfo`` if the C plugin attached frame-level
        metadata, otherwise ``None``.
    """
    ww_frame_type = _get_wastewatch_frame_meta_type()
    user_meta_list = frame_meta.frame_user_meta_list
    while user_meta_list is not None:
        try:
            user_meta = pyds.NvDsUserMeta.cast(user_meta_list.data)
        except StopIteration:
            break

        if user_meta.meta_type == ww_frame_type:
            try:
                ptr = ctypes.cast(
                    ctypes.c_void_p(user_meta.user_meta_data),
                    ctypes.POINTER(WasteWatchFrameMetaC),
                )
                c_meta = ptr.contents
                return _c_frame_to_info(c_meta)
            except Exception:
                logger.exception(
                    "Failed to cast WasteWatch frame user meta for source %d",
                    frame_meta.source_id,
                )
                return None

        try:
            user_meta_list = user_meta_list.next
        except StopIteration:
            break

    return None


def iter_enriched_objects(
    frame_meta: pyds.NvDsFrameMeta,
) -> Iterator[tuple[pyds.NvDsObjectMeta, WasteWatchObjectInfo]]:
    """Yield (obj_meta, ww_info) pairs for objects with WasteWatch metadata.

    This is a convenience generator that walks every NvDsObjectMeta in the
    given frame and yields only those that have WasteWatch enrichment
    attached by the C plugin.

    Args:
        frame_meta: A DeepStream NvDsFrameMeta from the batch metadata.

    Yields:
        Tuples of ``(NvDsObjectMeta, WasteWatchObjectInfo)`` for each
        object that carries WasteWatch metadata.
    """
    obj_meta_list = frame_meta.obj_meta_list
    while obj_meta_list is not None:
        try:
            obj_meta = pyds.NvDsObjectMeta.cast(obj_meta_list.data)
        except StopIteration:
            break

        ww_info = get_object_meta(obj_meta)
        if ww_info is not None:
            yield obj_meta, ww_info

        try:
            obj_meta_list = obj_meta_list.next
        except StopIteration:
            break
