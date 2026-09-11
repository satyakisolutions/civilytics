# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Unified violation event dispatcher -- fans out to all subsystems simultaneously.

When the FSM transitions to VIOLATION (either via dwell threshold or action SGIE
fast-alert), this dispatcher triggers ALL downstream actions in parallel:

1. Smart Recording  -- NvDsSRStart() with 15s pre-buffer
2. VLM Analysis     -- Kafka request to capture frames and run Qwen2.5-VL-7B
3. TTS Warning      -- Kafka request to generate audio alert via Piper TTS
4. Alert Dispatch   -- Kafka event for WebSocket + Telegram fan-out
5. LPR Request      -- Kafka request for vehicle plate recognition (vehicles only)
6. Evidence Start   -- Kafka event to track evidence collection lifecycle

This replaces the scattered trigger logic where each subsystem was triggered
independently at different points in the pipeline, causing:
- TTS delays (waited for full alert round-trip through backend)
- VLM delays (waited for event consumer -> VLM worker chain)
- Recording delays (started after backend processing)

Now: ALL fire within the same GStreamer buffer callback (~1ms fan-out).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Vehicle classes that qualify for LPR requests.
_VEHICLE_CLASSES = frozenset({"car", "motorcycle", "bus", "truck"})

# FSM states considered active-violation for exit handling.
_ACTIVE_VIOLATION_STATES = frozenset({"violation", "alerting", "escalated"})


@dataclass
class ViolationContext:
    """All data needed to dispatch a violation event."""

    camera_id: str
    track_id: int
    class_name: str
    dwell_time_s: float
    bbox: dict | None
    fsm_state: str            # "violation"
    fsm_prev_state: str       # what it transitioned from
    in_roi: bool
    zone_name: str            # derived from camera_id or ROI name

    # Action classification (from SGIE, may be None if SGIE unavailable)
    action_label: str | None = None
    action_confidence: float | None = None
    action_threat: str | None = None
    fast_alert: bool = False

    # Timestamps
    timestamp: float = field(default_factory=time.time)
    frame_num: int = 0


class ViolationDispatcher:
    """Unified violation event dispatcher.

    On a VIOLATION FSM transition, fans out Smart Recording start, VLM
    analysis request, TTS audio generation, alert dispatch, LPR request
    (vehicles only), and evidence lifecycle tracking -- all within a single
    GStreamer buffer callback invocation.

    Usage::

        from app.violation_dispatcher import ViolationDispatcher, ViolationContext

        dispatcher = ViolationDispatcher(kafka_producer, smart_record_mgr)

        # In the metadata probe, after FSM transitions to VIOLATION:
        ctx = ViolationContext(
            camera_id="cam-01", track_id=42, class_name="person",
            dwell_time_s=18.3, bbox={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
            fsm_state="violation", fsm_prev_state="observe",
            in_roi=True, zone_name="zone-A",
        )
        result = dispatcher.on_violation(ctx)
    """

    # Cooldown period: won't re-dispatch for the same (camera_id, track_id)
    # within this many seconds.
    _COOLDOWN_S = 30.0

    # Stale entry threshold: entries older than this are cleaned up.
    _STALE_THRESHOLD_S = 600.0  # 10 minutes

    def __init__(
        self,
        kafka_producer: Any,  # PipelineKafkaProducer
        smart_record_mgr: Any | None = None,  # SmartRecordManager
    ) -> None:
        self._kafka = kafka_producer
        self._sr_mgr = smart_record_mgr
        self._cooldown_s = self._COOLDOWN_S

        # Tracks dispatched violations to prevent duplicate dispatches.
        # Key: (camera_id, track_id), Value: time.monotonic() of last dispatch.
        self._dispatched: dict[tuple[str, int], float] = {}

        logger.info("ViolationDispatcher initialised (cooldown=%.0fs)", self._cooldown_s)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def on_violation(self, ctx: ViolationContext) -> dict:
        """Dispatch violation to all subsystems simultaneously.

        Returns a summary dict of what was dispatched.
        """
        key = (ctx.camera_id, ctx.track_id)

        # Dedup check -- don't re-dispatch for same track within cooldown
        if key in self._dispatched:
            elapsed = time.monotonic() - self._dispatched[key]
            if elapsed < self._cooldown_s:
                logger.debug(
                    "Violation dispatch suppressed for %s/track-%d (%.1fs cooldown)",
                    ctx.camera_id, ctx.track_id, elapsed,
                )
                return {"dispatched": False, "reason": "cooldown"}

        self._dispatched[key] = time.monotonic()
        results: dict[str, str] = {}

        # 1. SMART RECORDING -- start immediately with pre-buffer
        results["recording"] = self._start_recording(ctx)

        # 2. VLM ANALYSIS -- request frame capture + Qwen2.5-VL-7B analysis
        results["vlm"] = self._request_vlm_analysis(ctx)

        # 3. TTS WARNING -- generate audio alert immediately
        results["tts"] = self._request_tts_warning(ctx)

        # 4. ALERT EVENT -- publish for WebSocket + Telegram dispatch
        results["alert"] = self._publish_alert_event(ctx)

        # 5. LPR REQUEST -- for vehicles only
        results["lpr"] = self._request_lpr(ctx)

        # 6. EVIDENCE LIFECYCLE -- mark evidence collection started
        results["evidence"] = self._publish_evidence_start(ctx)

        # Flush Kafka to ensure all messages are sent
        self._kafka.poll()

        logger.warning(
            "VIOLATION DISPATCHED: %s track=%d class=%s dwell=%.1fs action=%s fast=%s | "
            "recording=%s vlm=%s tts=%s alert=%s lpr=%s",
            ctx.camera_id, ctx.track_id, ctx.class_name, ctx.dwell_time_s,
            ctx.action_label or "none", ctx.fast_alert,
            results["recording"], results["vlm"], results["tts"],
            results["alert"], results["lpr"],
        )

        return {"dispatched": True, "results": results}

    # ------------------------------------------------------------------
    # Exit handler
    # ------------------------------------------------------------------

    def on_exit(self, camera_id: str, track_id: int, prev_state: str) -> dict:
        """Called when a track exits from a violation state.

        Stops recording and publishes evidence.complete. Only acts if
        *prev_state* was an active violation state (violation/alerting/escalated).

        Args:
            camera_id: Camera identifier.
            track_id: Object tracker ID.
            prev_state: The FSM state the track was in before exiting.

        Returns:
            Summary dict of actions taken.
        """
        if prev_state not in _ACTIVE_VIOLATION_STATES:
            return {"acted": False, "reason": "prev_state_not_violation"}

        results: dict[str, str] = {}

        # Stop smart recording
        if self._sr_mgr is not None:
            try:
                output_path = self._sr_mgr.stop_recording(camera_id, track_id)
                if output_path:
                    results["recording"] = "stopped"
                    logger.info(
                        "Evidence recording STOPPED via dispatcher: %s/track-%d -> %s",
                        camera_id, track_id, output_path,
                    )
                else:
                    results["recording"] = "no_active_recording"
            except Exception:
                logger.exception(
                    "Failed to stop recording for %s/track-%d", camera_id, track_id,
                )
                results["recording"] = "error"
        else:
            results["recording"] = "unavailable"

        # Publish evidence.complete
        try:
            self._kafka.produce(
                topic="wastewatch.evidence",
                key=camera_id,
                value={
                    "action": "complete",
                    "camera_id": camera_id,
                    "track_id": track_id,
                    "prev_state": prev_state,
                    "timestamp": time.time(),
                },
            )
            results["evidence"] = "sent"
        except Exception:
            logger.exception(
                "Failed to publish evidence.complete for %s/track-%d",
                camera_id, track_id,
            )
            results["evidence"] = "error"

        # Clean up dispatch tracking
        self._dispatched.pop((camera_id, track_id), None)

        self._kafka.poll()

        logger.info(
            "VIOLATION EXIT: %s track=%d prev_state=%s | recording=%s evidence=%s",
            camera_id, track_id, prev_state,
            results.get("recording", "n/a"), results.get("evidence", "n/a"),
        )

        return {"acted": True, "results": results}

    # ------------------------------------------------------------------
    # Stale entry cleanup
    # ------------------------------------------------------------------

    def cleanup_stale(self) -> None:
        """Remove old entries from the dispatched dict (older than 10 minutes)."""
        now = time.monotonic()
        stale_keys = [
            key for key, ts in self._dispatched.items()
            if (now - ts) > self._STALE_THRESHOLD_S
        ]
        for key in stale_keys:
            del self._dispatched[key]

        if stale_keys:
            logger.debug(
                "Cleaned up %d stale dispatch entries", len(stale_keys),
            )

    # ------------------------------------------------------------------
    # Internal: Smart Recording
    # ------------------------------------------------------------------

    def _start_recording(self, ctx: ViolationContext) -> str:
        """Start smart recording with pre-buffer for the violation.

        Returns:
            "started", "unavailable", "already_recording", or "error".
        """
        if self._sr_mgr is None:
            return "unavailable"

        try:
            output_path = self._sr_mgr.start_recording(ctx.camera_id, ctx.track_id)
            if output_path:
                logger.info(
                    "Smart recording STARTED: %s/track-%d -> %s",
                    ctx.camera_id, ctx.track_id, output_path,
                )
                return "started"
            else:
                # start_recording returned None/empty -- likely already recording
                return "already_recording"
        except Exception:
            logger.exception(
                "Failed to start smart recording for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: VLM Analysis
    # ------------------------------------------------------------------

    def _request_vlm_analysis(self, ctx: ViolationContext) -> str:
        """Request frame capture and Qwen2.5-VL-7B analysis via Kafka.

        Returns:
            "sent" or "error".
        """
        try:
            self._kafka.send_vlm_request(
                camera_id=ctx.camera_id,
                track_id=ctx.track_id,
                zone_name=ctx.zone_name,
                dwell_time_s=ctx.dwell_time_s,
                class_name=ctx.class_name,
                path_summary=self._build_path_summary(ctx),
            )
            return "sent"
        except Exception:
            logger.exception(
                "Failed to send VLM request for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: TTS Warning
    # ------------------------------------------------------------------

    def _request_tts_warning(self, ctx: ViolationContext) -> str:
        """Generate an audio alert via Piper TTS immediately.

        Publishes a pre-built contextual warning message to the
        ``wastewatch.tts`` Kafka topic.

        Returns:
            "sent" or "error".
        """
        # Build contextual TTS message
        if ctx.fast_alert:
            message = (
                f"Alert. Active waste dumping detected at camera {ctx.camera_id}. "
                f"A {ctx.class_name} has been observed dumping waste in the monitored zone. "
                f"Immediate attention required."
            )
        else:
            message = (
                f"Warning. Suspicious activity detected at camera {ctx.camera_id}. "
                f"A {ctx.class_name} has been in the restricted zone for "
                f"{int(ctx.dwell_time_s)} seconds. Please investigate."
            )

        # Add vehicle info if applicable
        if ctx.class_name in _VEHICLE_CLASSES:
            message += f" Vehicle type: {ctx.class_name}."

        try:
            self._kafka.send_tts_request(
                camera_id=ctx.camera_id,
                track_id=ctx.track_id,
                text=message,
                priority="high" if ctx.fast_alert else "normal",
                timestamp=ctx.timestamp,
            )
            return "sent"
        except Exception:
            logger.exception(
                "Failed to send TTS request for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: Alert Event
    # ------------------------------------------------------------------

    def _publish_alert_event(self, ctx: ViolationContext) -> str:
        """Publish alert for WebSocket + Telegram dispatch.

        Returns:
            "sent" or "error".
        """
        try:
            self._kafka.send_alert(
                camera_id=ctx.camera_id,
                track_id=ctx.track_id,
                severity=ctx.action_threat or ("critical" if ctx.dwell_time_s > 30 else "warning"),
                message=self._build_alert_message(ctx),
                is_dumping=ctx.fast_alert or ctx.dwell_time_s > 15,
                dwell_time_s=round(ctx.dwell_time_s, 1),
                class_name=ctx.class_name,
                action_label=ctx.action_label,
                action_confidence=ctx.action_confidence,
                fast_alert=ctx.fast_alert,
                bbox=ctx.bbox,
                timestamp=ctx.timestamp,
            )
            return "sent"
        except Exception:
            logger.exception(
                "Failed to publish alert for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: LPR Request
    # ------------------------------------------------------------------

    def _request_lpr(self, ctx: ViolationContext) -> str:
        """Request license plate recognition for vehicle violations.

        Only sends for vehicle classes (car, motorcycle, bus, truck).

        Returns:
            "sent", "skipped" (non-vehicle), or "error".
        """
        if ctx.class_name not in _VEHICLE_CLASSES:
            return "skipped"

        try:
            self._kafka.send_lpr_request(
                camera_id=ctx.camera_id,
                track_id=ctx.track_id,
                vehicle_bbox=ctx.bbox or {},
                class_name=ctx.class_name,
            )
            return "sent"
        except Exception:
            logger.exception(
                "Failed to send LPR request for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: Evidence Lifecycle
    # ------------------------------------------------------------------

    def _publish_evidence_start(self, ctx: ViolationContext) -> str:
        """Publish evidence collection start event.

        Returns:
            "sent" or "error".
        """
        try:
            self._kafka.produce(
                topic="wastewatch.evidence",
                key=ctx.camera_id,
                value={
                    "action": "start",
                    "camera_id": ctx.camera_id,
                    "track_id": ctx.track_id,
                    "violation_type": ctx.action_label or "dwell_threshold",
                    "timestamp": ctx.timestamp,
                },
            )
            return "sent"
        except Exception:
            logger.exception(
                "Failed to publish evidence start for %s/track-%d",
                ctx.camera_id, ctx.track_id,
            )
            return "error"

    # ------------------------------------------------------------------
    # Internal: Message builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_path_summary(ctx: ViolationContext) -> str:
        """Build a human-readable path summary for VLM context.

        Returns a short string describing the object's behaviour pattern
        that helps the VLM model contextualise the violation.
        """
        if ctx.fast_alert:
            conf = ctx.action_confidence if ctx.action_confidence is not None else 0.0
            return f"dumping_detected (action SGIE, conf={conf:.2f})"
        elif ctx.dwell_time_s > 30:
            return "stationary_prolonged"
        elif ctx.dwell_time_s > 10:
            return "stationary"
        else:
            return "moving"

    @staticmethod
    def _build_alert_message(ctx: ViolationContext) -> str:
        """Build a contextual alert message for human consumption.

        Generates a descriptive message suitable for WebSocket notifications,
        Telegram messages, and dashboard display.
        """
        if ctx.fast_alert:
            base = (
                f"Active waste dumping detected at {ctx.camera_id} "
                f"(zone: {ctx.zone_name}). "
                f"A {ctx.class_name} (track {ctx.track_id}) was observed "
                f"dumping waste."
            )
            if ctx.action_label and ctx.action_confidence is not None:
                base += (
                    f" Action: {ctx.action_label} "
                    f"(confidence: {ctx.action_confidence:.0%})."
                )
            return base

        severity_word = "prolonged" if ctx.dwell_time_s > 30 else "sustained"
        base = (
            f"Suspicious {severity_word} presence detected at {ctx.camera_id} "
            f"(zone: {ctx.zone_name}). "
            f"A {ctx.class_name} (track {ctx.track_id}) has been in the "
            f"restricted zone for {int(ctx.dwell_time_s)} seconds."
        )

        if ctx.class_name in _VEHICLE_CLASSES:
            base += " License plate recognition has been requested."

        return base

    # ------------------------------------------------------------------
    # Status / Diagnostics
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return a summary of dispatcher state for health checks."""
        return {
            "active_dispatches": len(self._dispatched),
            "cooldown_s": self._cooldown_s,
            "smart_record_available": self._sr_mgr is not None,
        }
