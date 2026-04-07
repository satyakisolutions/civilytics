# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Smart Record trigger -- starts/stops recording based on FSM events.

Integrates with the wastewatchmeta C plugin or Python FSM to trigger
DeepStream Smart Recording on violation events.

Flow:
    1. The metadata probe (extract_metadata or extract_metadata_v2) calls
       ``integrate_with_probe()`` for each tracked object whose FSM state
       changed.
    2. ``on_fsm_event()`` maps FSM transitions to SmartRecordManager
       start/stop calls and publishes Kafka evidence lifecycle events.
    3. ``on_frame_batch()`` is invoked once per batch to garbage-collect
       expired recordings.

Kafka events produced:
    wastewatch.evidence  (key = camera_id)
        { "type": "evidence.start",    ... }   — recording initiated
        { "type": "evidence.complete",  ... }   — recording finalised
        { "type": "evidence.expired",   ... }   — recording force-stopped
"""

from __future__ import annotations

import logging
import time
from typing import Any

import orjson

logger = logging.getLogger(__name__)

# FSM states that trigger recording start / stop.
_RECORDING_START_STATES = frozenset({"violation"})
_RECORDING_STOP_STATES = frozenset({"exited"})

# FSM states from which a transition to EXITED means the recording should
# be stopped (i.e. the track was in an active violation phase).
_ACTIVE_VIOLATION_STATES = frozenset({"violation", "alerting", "escalated"})


class SmartRecordTrigger:
    """Bridges FSM state transitions to DeepStream Smart Recording.

    Usage in the pipeline::

        from app.smart_record import SmartRecordManager
        from app.smart_record_trigger import SmartRecordTrigger

        sr_mgr = SmartRecordManager(config)
        trigger = SmartRecordTrigger(sr_mgr, kafka_producer)

        # In the metadata probe:
        trigger.integrate_with_probe(ww_object_info)

        # In the health-check timer:
        trigger.on_frame_batch([])
    """

    def __init__(
        self,
        smart_record_mgr: Any,  # SmartRecordManager (Any to avoid circular import issues)
        kafka_producer: Any | None = None,
    ) -> None:
        self._sr_mgr = smart_record_mgr
        self._kafka = kafka_producer

        # Track which (camera_id, track_id) pairs transitioned into a
        # violation state so we know to stop recording when they exit.
        self._violation_tracks: set[tuple[str, int]] = set()

        logger.info("SmartRecordTrigger initialised")

    # ------------------------------------------------------------------
    # FSM event handler
    # ------------------------------------------------------------------

    def on_fsm_event(
        self,
        camera_id: str,
        track_id: int,
        fsm_state: str,
        prev_state: str,
        class_name: str,
        dwell_time_s: float,
        bbox: dict | None = None,
    ) -> None:
        """Handle an FSM state transition for a tracked object.

        Called when the FSM for (camera_id, track_id) changes state.
        Decides whether to start or stop a smart recording and publishes
        the corresponding Kafka evidence event.

        Args:
            camera_id: Camera identifier (e.g. "cam-01").
            track_id: Object tracker ID.
            fsm_state: New FSM state (e.g. "violation", "exited").
            prev_state: Previous FSM state.
            class_name: Detection class label (e.g. "car", "person").
            dwell_time_s: How long the object has been in the ROI.
            bbox: Normalised bounding box dict {x, y, w, h} or None.
        """
        key = (camera_id, track_id)

        # --- Transition INTO violation: start recording --------------------
        if fsm_state in _RECORDING_START_STATES and prev_state not in _RECORDING_START_STATES:
            self._violation_tracks.add(key)

            output_path = self._sr_mgr.start_recording(camera_id, track_id)

            if output_path:
                logger.info(
                    "Evidence recording STARTED: %s/track-%d (%s, dwell=%.1fs) -> %s",
                    camera_id,
                    track_id,
                    class_name,
                    dwell_time_s,
                    output_path,
                )
                self._publish_evidence_event("evidence.start", {
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "class_name": class_name,
                    "dwell_time_s": round(dwell_time_s, 1),
                    "bbox": bbox,
                    "output_path": output_path,
                    "timestamp": time.time(),
                })
            else:
                logger.warning(
                    "Failed to start evidence recording for %s/track-%d",
                    camera_id,
                    track_id,
                )

        # --- Transition to EXITED from an active violation: stop recording --
        elif fsm_state in _RECORDING_STOP_STATES and prev_state in _ACTIVE_VIOLATION_STATES:
            self._violation_tracks.discard(key)

            output_path = self._sr_mgr.stop_recording(camera_id, track_id)

            if output_path:
                logger.info(
                    "Evidence recording STOPPED: %s/track-%d (%s -> %s, dwell=%.1fs) -> %s",
                    camera_id,
                    track_id,
                    prev_state,
                    fsm_state,
                    dwell_time_s,
                    output_path,
                )
                self._publish_evidence_event("evidence.complete", {
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "class_name": class_name,
                    "prev_state": prev_state,
                    "dwell_time_s": round(dwell_time_s, 1),
                    "bbox": bbox,
                    "output_path": output_path,
                    "timestamp": time.time(),
                })
            else:
                logger.debug(
                    "No active recording found for %s/track-%d on exit",
                    camera_id,
                    track_id,
                )

        # --- Transition to EXITED from a non-violation state: just clean up -
        elif fsm_state in _RECORDING_STOP_STATES:
            self._violation_tracks.discard(key)

        # --- Any other transition: log for debugging -----------------------
        else:
            logger.debug(
                "FSM transition %s/track-%d: %s -> %s (no recording action)",
                camera_id,
                track_id,
                prev_state,
                fsm_state,
            )

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    def on_frame_batch(self, frame_infos: list[Any] | None = None) -> None:
        """Called once per batch to perform periodic maintenance.

        Checks for recordings that exceeded max_duration_s and force-stops
        them.  The *frame_infos* parameter is accepted for API symmetry
        but is not currently used.

        Args:
            frame_infos: Optional list of per-frame info dicts (unused).
        """
        expired_paths = self._sr_mgr.cleanup_expired()

        for path in expired_paths:
            logger.warning("Evidence recording EXPIRED (max duration): %s", path)
            self._publish_evidence_event("evidence.expired", {
                "output_path": path,
                "reason": "max_duration_exceeded",
                "timestamp": time.time(),
            })

    # ------------------------------------------------------------------
    # Metadata probe integration
    # ------------------------------------------------------------------

    def integrate_with_probe(self, ww_object_info: Any) -> None:
        """Called from the metadata probe for each tracked object.

        Checks whether the object's FSM state changed and, if so,
        delegates to ``on_fsm_event()``.

        This supports both the native C wastewatchmeta plugin (which
        attaches WasteWatchObjectMeta to each NvDsObjectMeta) and the
        Python fallback (where ww_object_info is a dict with the same
        keys).

        Expected attributes / keys on ``ww_object_info``:
            - fsm_state_changed (bool)
            - camera_id (str)
            - track_id (int)
            - fsm_state (str)
            - prev_state (str)
            - class_name (str)
            - dwell_time_s (float)
            - bbox (dict | None)
        """
        # Support both attribute access (C plugin struct) and dict access.
        _get = (
            (lambda k, d=None: getattr(ww_object_info, k, d))
            if hasattr(ww_object_info, "__dict__") or hasattr(ww_object_info, "fsm_state_changed")
            else (lambda k, d=None: ww_object_info.get(k, d))
        )

        if not _get("fsm_state_changed", False):
            return

        camera_id = _get("camera_id")
        track_id = _get("track_id")
        fsm_state = _get("fsm_state")
        prev_state = _get("prev_state")
        class_name = _get("class_name", "unknown")
        dwell_time_s = _get("dwell_time_s", 0.0)
        bbox = _get("bbox")

        if camera_id is None or track_id is None or fsm_state is None:
            logger.warning(
                "Incomplete ww_object_info for smart record trigger: "
                "camera_id=%r, track_id=%r, fsm_state=%r",
                camera_id,
                track_id,
                fsm_state,
            )
            return

        self.on_fsm_event(
            camera_id=camera_id,
            track_id=track_id,
            fsm_state=fsm_state,
            prev_state=prev_state or "unknown",
            class_name=class_name,
            dwell_time_s=float(dwell_time_s),
            bbox=bbox,
        )

    # ------------------------------------------------------------------
    # Kafka evidence events
    # ------------------------------------------------------------------

    def _publish_evidence_event(self, event_type: str, payload: dict) -> None:
        """Publish an evidence lifecycle event to the wastewatch.evidence topic.

        Events:
            evidence.start    — a recording was initiated for a violation
            evidence.complete — the recording was finalised (MP4 ready)
            evidence.expired  — the recording was force-stopped (max duration)
        """
        if self._kafka is None:
            logger.debug("No Kafka producer — skipping evidence event: %s", event_type)
            return

        camera_id = payload.get("camera_id", "unknown")
        message = {"type": event_type, **payload}

        try:
            self._kafka._producer.produce(
                topic="wastewatch.evidence",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(message),
            )
            self._kafka._producer.poll(0)
        except Exception:
            logger.exception(
                "Failed to publish evidence event %s for %s",
                event_type,
                camera_id,
            )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def violation_track_count(self) -> int:
        """Number of tracks currently in a violation recording state."""
        return len(self._violation_tracks)

    def get_status(self) -> dict:
        """Return a summary of trigger and recording state."""
        return {
            "violation_tracks": len(self._violation_tracks),
            "active_recordings": self._sr_mgr.get_active_recordings(),
            "has_native_api": self._sr_mgr.has_native_api,
        }
