# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Action classification metadata extractor for WasteWatch SGIE.

The action recognition SGIE (nvinfer, process-mode=2) runs on person/vehicle
crops from the primary detector and classifies the observed action. This module
extracts the classification results from NvDsClassifierMeta and integrates
them with the WasteWatch pipeline for faster alerting.

Alert Speed Comparison:
    Without SGIE: detect -> dwell 15s -> flag -> VLM 2-10s -> alert  (~25s)
    With SGIE:    detect -> action classify 50ms -> dumping + ROI -> alert (<1s)
                  (VLM still runs for confirmation and evidence enrichment)

The SGIE attaches NvDsClassifierMeta to each NvDsObjectMeta. This module:
1. Reads the classifier label from obj_meta.classifier_meta_list
2. Maps action labels to threat levels
3. Provides fast violation detection (action-based, not just dwell-based)
4. Enriches the metadata published to Kafka with action classification
5. Triggers fast-path alerts for high-confidence dumping detections
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import IntEnum
from statistics import mode

import pyds

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action label taxonomy
# ---------------------------------------------------------------------------


class ActionLabel(IntEnum):
    """Action classes produced by the SGIE action classifier.

    Order matches configs/action_labels.txt exactly.
    """

    DUMPING_WASTE = 0
    CARRYING_OBJECT = 1
    LOADING_UNLOADING = 2
    STANDING_IDLE = 3
    WALKING = 4
    RUNNING = 5
    DRIVING = 6
    NORMAL_ACTIVITY = 7


# ---------------------------------------------------------------------------
# Threat mapping
# ---------------------------------------------------------------------------

ACTION_THREAT_MAP: dict[ActionLabel, str] = {
    ActionLabel.DUMPING_WASTE: "critical",
    ActionLabel.CARRYING_OBJECT: "warning",
    ActionLabel.LOADING_UNLOADING: "warning",
    ActionLabel.STANDING_IDLE: "low",
    ActionLabel.WALKING: "none",
    ActionLabel.RUNNING: "low",
    ActionLabel.DRIVING: "none",
    ActionLabel.NORMAL_ACTIVITY: "none",
}

FAST_ALERT_ACTIONS: set[ActionLabel] = {ActionLabel.DUMPING_WASTE}

SUSPICIOUS_ACTIONS: set[ActionLabel] = {
    ActionLabel.CARRYING_OBJECT,
    ActionLabel.LOADING_UNLOADING,
}

# Reduced dwell thresholds (seconds) when a suspicious action is detected.
# Default thresholds are enter=5s, observe=15s.
_SUSPICIOUS_DWELL_THRESHOLDS: dict[ActionLabel, float] = {
    ActionLabel.CARRYING_OBJECT: 5.0,
    ActionLabel.LOADING_UNLOADING: 3.0,
}

# SGIE unique component ID — must match gie-unique-id in nvinfer_action_sgie.txt
_SGIE_UNIQUE_ID = 2

# Temporal smoothing window size
_SMOOTHING_WINDOW = 10

# Minimum consistent frames (out of last 5) required for a fast alert
_FAST_ALERT_MIN_CONSISTENT = 3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ActionClassification:
    """Result of action classification for a single detection."""

    label: ActionLabel
    label_name: str
    confidence: float
    threat_level: str  # "critical", "warning", "low", "none"
    is_fast_alert: bool  # True if this action triggers immediate alert
    timestamp: float = field(default_factory=time.monotonic)


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class ActionClassifierExtractor:
    """Extracts and manages action classification results from the SGIE.

    Parameters
    ----------
    fast_alert_confidence:
        Minimum confidence for a ``DUMPING_WASTE`` classification to trigger
        a fast-path alert (bypassing dwell-based detection).
    suspicious_confidence:
        Minimum confidence for a suspicious action (carrying_object,
        loading_unloading) to reduce the dwell threshold.
    """

    def __init__(
        self,
        fast_alert_confidence: float = 0.6,
        suspicious_confidence: float = 0.5,
    ) -> None:
        self._fast_alert_conf = fast_alert_confidence
        self._suspicious_conf = suspicious_confidence

        # Per-track action history: (camera_id, track_id) -> deque of ActionLabel
        self._track_history: dict[tuple[str, int], deque[ActionLabel]] = defaultdict(
            lambda: deque(maxlen=_SMOOTHING_WINDOW)
        )
        # Latest smoothed result cache
        self._track_latest: dict[tuple[str, int], ActionClassification] = {}

    # ------------------------------------------------------------------
    # Core extraction
    # ------------------------------------------------------------------

    def extract_action(self, obj_meta) -> ActionClassification | None:
        """Read NvDsClassifierMeta from *obj_meta* and return an ActionClassification.

        Returns ``None`` if no classifier result from our SGIE is attached.
        """
        classifier_meta_list = obj_meta.classifier_meta_list
        while classifier_meta_list is not None:
            try:
                classifier_meta = pyds.NvDsClassifierMeta.cast(classifier_meta_list.data)
            except StopIteration:
                break

            # Only process results from our SGIE
            if classifier_meta.unique_component_id == _SGIE_UNIQUE_ID:
                label_info_list = classifier_meta.label_info_list
                while label_info_list is not None:
                    try:
                        label_info = pyds.NvDsLabelInfo.cast(label_info_list.data)
                    except StopIteration:
                        break

                    label_id = label_info.result_class_id
                    confidence = float(label_info.result_prob)
                    label_name = label_info.result_label

                    try:
                        action = ActionLabel(label_id)
                    except ValueError:
                        logger.warning(
                            "Unknown action label_id=%d from SGIE, skipping",
                            label_id,
                        )
                        try:
                            label_info_list = label_info_list.next
                        except StopIteration:
                            break
                        continue

                    return ActionClassification(
                        label=action,
                        label_name=label_name if label_name else action.name.lower(),
                        confidence=confidence,
                        threat_level=ACTION_THREAT_MAP.get(action, "none"),
                        is_fast_alert=(
                            action in FAST_ALERT_ACTIONS
                            and confidence >= self._fast_alert_conf
                        ),
                    )

                    # Note: we return on the first valid label_info.
                    # DeepStream classifiers typically attach one top-1 result.

            try:
                classifier_meta_list = classifier_meta_list.next
            except StopIteration:
                break

        return None

    # ------------------------------------------------------------------
    # Temporal smoothing
    # ------------------------------------------------------------------

    def update_track_action(
        self,
        camera_id: str,
        track_id: int,
        action: ActionClassification,
    ) -> ActionClassification:
        """Apply temporal smoothing to an action classification.

        Maintains a sliding window of the last ``_SMOOTHING_WINDOW``
        classifications per track and returns the classification with
        the most frequent (mode) action label. This prevents single-frame
        glitches from triggering false alerts.

        Returns the smoothed ``ActionClassification``.
        """
        key = (camera_id, track_id)
        history = self._track_history[key]
        history.append(action.label)

        # Compute the mode (most frequent label in the window)
        try:
            smoothed_label = mode(history)
        except Exception:
            # statistics.mode raises on empty or multi-mode in older Python;
            # fall back to the latest observation.
            smoothed_label = action.label

        if smoothed_label == action.label:
            # Mode matches latest observation — use its confidence directly.
            smoothed = action
        else:
            # Mode differs — build a synthetic classification with reduced
            # confidence (we don't have the original confidence for old frames,
            # so we discount by the ratio of mode count to window size).
            mode_count = sum(1 for lbl in history if lbl == smoothed_label)
            smoothed_conf = action.confidence * (mode_count / len(history))
            smoothed = ActionClassification(
                label=smoothed_label,
                label_name=smoothed_label.name.lower(),
                confidence=round(smoothed_conf, 4),
                threat_level=ACTION_THREAT_MAP.get(smoothed_label, "none"),
                is_fast_alert=(
                    smoothed_label in FAST_ALERT_ACTIONS
                    and smoothed_conf >= self._fast_alert_conf
                ),
            )

        self._track_latest[key] = smoothed
        return smoothed

    # ------------------------------------------------------------------
    # Fast alerting logic
    # ------------------------------------------------------------------

    def should_fast_alert(
        self,
        camera_id: str,
        track_id: int,
        action: ActionClassification,
        in_roi: bool,
    ) -> bool:
        """Determine if a fast-path alert should be issued.

        A fast alert fires when ALL of the following are true:

        1. The action is in ``FAST_ALERT_ACTIONS`` (i.e., dumping_waste).
        2. The confidence meets the ``fast_alert_confidence`` threshold.
        3. The object is currently inside an ROI.
        4. The smoothed action over the last 5 frames is consistent
           (at least ``_FAST_ALERT_MIN_CONSISTENT`` out of 5 are the
           same action) to filter out single-frame glitches.
        """
        if action.label not in FAST_ALERT_ACTIONS:
            return False
        if action.confidence < self._fast_alert_conf:
            return False
        if not in_roi:
            return False

        # Check temporal consistency over the last 5 frames
        key = (camera_id, track_id)
        history = self._track_history.get(key)
        if history is None or len(history) < _FAST_ALERT_MIN_CONSISTENT:
            return False

        recent = list(history)[-5:]
        consistent_count = sum(1 for lbl in recent if lbl == action.label)
        return consistent_count >= _FAST_ALERT_MIN_CONSISTENT

    # ------------------------------------------------------------------
    # Dwell threshold reduction for suspicious actions
    # ------------------------------------------------------------------

    def should_reduce_dwell_threshold(
        self,
        action: ActionClassification,
    ) -> tuple[bool, float]:
        """Check if a suspicious action should reduce the dwell threshold.

        Returns
        -------
        (should_reduce, new_threshold_s)
            ``should_reduce`` is True if the action is suspicious with
            sufficient confidence. ``new_threshold_s`` is the reduced
            observe threshold (replacing the default 15s).
        """
        if action.label not in SUSPICIOUS_ACTIONS:
            return False, 0.0
        if action.confidence < self._suspicious_conf:
            return False, 0.0

        reduced = _SUSPICIOUS_DWELL_THRESHOLDS.get(action.label, 15.0)
        return True, reduced

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def get_osd_label(self, action: ActionClassification) -> str:
        """Return a display label for the On-Screen Display (nvosd).

        Fast-alert actions are highlighted with an uppercase warning prefix.
        """
        if action.is_fast_alert:
            return f"\u26a0 {action.label_name.upper()} [{action.confidence:.2f}]"
        return f"{action.label_name} [{action.confidence:.2f}]"

    # ------------------------------------------------------------------
    # Kafka metadata enrichment
    # ------------------------------------------------------------------

    def enrich_detection_dict(
        self,
        detection: dict,
        action: ActionClassification,
    ) -> dict:
        """Add action classification fields to the detection metadata dict.

        The enriched dict is published to Kafka for downstream consumers
        (alert service, VLM service, dashboard).
        """
        detection["action_label"] = action.label_name
        detection["action_confidence"] = round(action.confidence, 4)
        detection["action_threat"] = action.threat_level
        detection["action_fast_alert"] = action.is_fast_alert
        return detection

    # ------------------------------------------------------------------
    # Track management
    # ------------------------------------------------------------------

    def get_latest_action(
        self,
        camera_id: str,
        track_id: int,
    ) -> ActionClassification | None:
        """Return the latest smoothed action for a track, or None."""
        return self._track_latest.get((camera_id, track_id))

    def prune_track(self, camera_id: str, track_id: int) -> None:
        """Remove tracking history for a departed object."""
        key = (camera_id, track_id)
        self._track_history.pop(key, None)
        self._track_latest.pop(key, None)

    def prune_stale_tracks(self, active_keys: set[tuple[str, int]]) -> None:
        """Remove history for tracks no longer active."""
        stale = set(self._track_history.keys()) - active_keys
        for key in stale:
            self._track_history.pop(key, None)
            self._track_latest.pop(key, None)
