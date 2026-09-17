# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Integrates action classification with the WasteWatch FSM for faster alerting.

The standard FSM relies on dwell time alone::

    IDLE -> ENTER (in ROI) -> OBSERVE (5s) -> VIOLATION (15s)

With action classification, the FSM gains a fast path::

    IDLE -> ENTER (in ROI) -> FAST_VIOLATION (dumping detected, <1s)

And a suspicious path with reduced thresholds::

    IDLE -> ENTER (in ROI) -> OBSERVE (carrying_object, 3s) -> VIOLATION (5s)

This module wraps the existing ``EventFSMManager`` and
``ActionClassifierExtractor`` to provide unified state transitions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.action_classifier import (
    ActionClassification,
    ActionClassifierExtractor,
)
from app.event_fsm import EventFSMManager, FSMEvent, FSMState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extended FSM event with action data
# ---------------------------------------------------------------------------


@dataclass
class ActionFSMEvent(FSMEvent):
    """FSM event enriched with action classification data.

    Extends the base ``FSMEvent`` so that downstream consumers (Kafka,
    alert service) receive action information alongside the state
    transition.
    """

    action_label: str | None = None
    action_confidence: float | None = None
    action_threat: str | None = None
    fast_alert: bool = False


# ---------------------------------------------------------------------------
# ActionAwareFSM
# ---------------------------------------------------------------------------


class ActionAwareFSM:
    """Wraps ``EventFSMManager`` and ``ActionClassifierExtractor`` for unified
    state transitions with action-based fast alerting.

    Parameters
    ----------
    fsm_manager:
        The existing dwell-based FSM manager.
    action_extractor:
        The SGIE action classification extractor.
    """

    def __init__(
        self,
        fsm_manager: EventFSMManager,
        action_extractor: ActionClassifierExtractor,
    ) -> None:
        self._fsm = fsm_manager
        self._action = action_extractor
        # Track which (camera_id, track_id) have already emitted a fast alert
        # to avoid duplicate alerts for the same track.
        self._fast_alerted: set[tuple[str, int]] = set()

    # ------------------------------------------------------------------
    # Main update
    # ------------------------------------------------------------------

    def update(
        self,
        camera_id: str,
        track_id: int,
        class_name: str,
        obj_meta,
        in_roi: bool,
        dwell_time_s: float,
        bbox: dict | None = None,
        has_carried_item: bool = False,
        violation_threshold_s: float | None = None,
        enter_threshold_s: float | None = None,
        speed_px_s: float | None = None,
    ) -> FSMEvent | None:
        """Process a detection through the action-aware FSM.

        Steps:
        1. Extract action classification from SGIE metadata.
        2. Apply temporal smoothing.
        3. Check for fast alert (dumping_waste in ROI with consistency).
        4. Check for reduced dwell threshold (suspicious actions).
        5. Delegate to the underlying ``EventFSMManager.update()``.
        6. Enrich the returned event with action data.
        """
        key = (camera_id, track_id)
        action: ActionClassification | None = None
        smoothed: ActionClassification | None = None

        # Step 1: Extract action classification from obj_meta
        if obj_meta is not None:
            action = self._action.extract_action(obj_meta)

        # Step 2: Temporal smoothing
        if action is not None:
            smoothed = self._action.update_track_action(camera_id, track_id, action)

            # Step 3: Fast alert path
            if (
                key not in self._fast_alerted
                and self._action.should_fast_alert(camera_id, track_id, smoothed, in_roi)
            ):
                self._fast_alerted.add(key)
                logger.warning(
                    "FAST ALERT: %s track=%d action=%s conf=%.3f",
                    camera_id,
                    track_id,
                    smoothed.label_name,
                    smoothed.confidence,
                )

                # Force the FSM into VIOLATION state so downstream processing
                # (VLM request, Kafka event) is consistent.
                # We call the FSM update with an artificially high dwell time
                # to ensure the transition fires, then wrap the event.
                forced_event = self._fsm.update(
                    camera_id,
                    track_id,
                    class_name,
                    in_roi=in_roi,
                    dwell_time_s=max(dwell_time_s, self._fsm._observe_threshold + 1),
                    bbox=bbox,
                    has_carried_item=has_carried_item,
                    violation_threshold_s=violation_threshold_s,
                    enter_threshold_s=enter_threshold_s,
                )

                # If the FSM didn't produce an event (e.g., already in VIOLATION),
                # synthesize one.
                if forced_event is None:
                    current_state = self._fsm.get_fsm_state(camera_id, track_id)
                    prev_state_str = current_state if current_state else "idle"
                    forced_event = ActionFSMEvent(
                        camera_id=camera_id,
                        track_id=track_id,
                        state=FSMState.VIOLATION,
                        prev_state=FSMState(prev_state_str),
                        dwell_time_s=dwell_time_s,
                        class_name=class_name,
                        bbox=bbox,
                        action_label=smoothed.label_name,
                        action_confidence=smoothed.confidence,
                        action_threat=smoothed.threat_level,
                        fast_alert=True,
                    )
                else:
                    forced_event = self._enrich_event(forced_event, smoothed, fast_alert=True)

                return forced_event

        # Step 4: Check for reduced dwell threshold
        effective_observe_threshold = self._fsm._observe_threshold
        if smoothed is not None:
            should_reduce, reduced_threshold = self._action.should_reduce_dwell_threshold(smoothed)
            if should_reduce:
                # Temporarily adjust the FSM's observe threshold for this update.
                # We restore it afterward to avoid permanently altering the manager.
                effective_observe_threshold = reduced_threshold
                original_threshold = self._fsm._observe_threshold
                self._fsm._observe_threshold = reduced_threshold

        # Step 5: Delegate to the underlying FSM
        event = self._fsm.update(
            camera_id,
            track_id,
            class_name,
            in_roi=in_roi,
            dwell_time_s=dwell_time_s,
            bbox=bbox,
            has_carried_item=has_carried_item,
            violation_threshold_s=violation_threshold_s,
            enter_threshold_s=enter_threshold_s,
            speed_px_s=speed_px_s,
        )

        # Restore threshold if we reduced it
        if smoothed is not None:
            should_reduce, _ = self._action.should_reduce_dwell_threshold(smoothed)
            if should_reduce:
                self._fsm._observe_threshold = original_threshold  # noqa: F821 — set above

        # Step 6: Enrich event with action data
        if event is not None and smoothed is not None:
            event = self._enrich_event(event, smoothed, fast_alert=False)

        # Clean up fast-alert tracking when a track exits
        if event is not None and event.state == FSMState.EXITED:
            self._fast_alerted.discard(key)
            self._action.prune_track(camera_id, track_id)

        return event

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enrich_event(
        self,
        event: FSMEvent,
        action: ActionClassification,
        *,
        fast_alert: bool,
    ) -> ActionFSMEvent:
        """Wrap a base FSMEvent into an ActionFSMEvent with classification data."""
        return ActionFSMEvent(
            camera_id=event.camera_id,
            track_id=event.track_id,
            state=event.state,
            prev_state=event.prev_state,
            dwell_time_s=event.dwell_time_s,
            class_name=event.class_name,
            bbox=event.bbox,
            timestamp=event.timestamp,
            action_label=action.label_name,
            action_confidence=action.confidence,
            action_threat=action.threat_level,
            fast_alert=fast_alert,
        )

    def get_action_for_track(
        self,
        camera_id: str,
        track_id: int,
    ) -> ActionClassification | None:
        """Return the latest smoothed action for a track."""
        return self._action.get_latest_action(camera_id, track_id)

    def prune_stale(self) -> list[FSMEvent]:
        """Delegate stale pruning to the underlying FSM and clean up action state."""
        events = self._fsm.prune_stale()
        for evt in events:
            key = (evt.camera_id, evt.track_id)
            self._fast_alerted.discard(key)
            self._action.prune_track(evt.camera_id, evt.track_id)
        return events

    def get_fsm_state(self, camera_id: str, track_id: int) -> str | None:
        """Proxy to the underlying FSM's state query."""
        return self._fsm.get_fsm_state(camera_id, track_id)
