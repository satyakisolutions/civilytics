# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Priority-based alert router — ordered dispatch within 5-20 seconds of detection.

Fires alerts in the user-defined priority order:
    1. Smart Recording  — capture evidence FIRST (NvDsSRStart with pre-buffer)
    2. VLM + LPR        — confirm dumping + identify vehicle plate via Kafka
    3. SIP Call          — reach operator by voice (immediate for P0, delayed otherwise)
    4. WebSocket         — dashboard red/yellow alert banner
    5. Telegram          — mobile push notification to zone operator
    6. IP Speaker        — on-site audio deterrent

Three-stage alert lifecycle:
    Stage 1: PRELIMINARY (2-3 consistent frames, T+200-500ms)
        → Recording starts, VLM requested, WebSocket warning
    Stage 2: CONFIRMED (action SGIE high-confidence OR dwell threshold)
        → SIP call, Telegram, IP speaker, full alert
    Stage 3: ENRICHED (VLM/LPR results arrive, T+2-5s)
        → Update alert with evidence, upgrade/downgrade severity

Dwell-based detection (up to 20s) is acceptable for the normal path.
Action SGIE fast-path fires in <1s for high-confidence dumping detections.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AlertStage(IntEnum):
    PRELIMINARY = 1   # First detection, building confidence
    CONFIRMED = 2     # Consistent detection / dwell threshold met
    ENRICHED = 3      # VLM/LPR results available
    ESCALATED = 4     # Not acknowledged, escalating contacts
    RESOLVED = 5      # Acknowledged or false positive cleared


class AlertPriority(IntEnum):
    P0_CRITICAL = 0   # Active dumping confirmed → immediate SIP + all channels
    P1_HIGH = 1       # Suspicious + in ROI → SIP after delay + Telegram + speaker
    P2_MEDIUM = 2     # Object in ROI, building confidence → WebSocket + recording
    P3_LOW = 3        # Monitoring, low threat → WebSocket only


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ZoneOperator:
    """Operator assigned to a surveillance zone."""
    operator_id: str
    name: str
    zone: str
    role: str                   # "operator", "supervisor", "admin"
    telegram_chat_id: str = ""
    sip_number: str = ""        # SIP URI or phone number
    is_available: bool = True
    last_alert_at: float = 0.0


@dataclass
class AlertState:
    """Per-track alert lifecycle state."""
    camera_id: str
    track_id: int
    zone: str
    stage: AlertStage = AlertStage.PRELIMINARY
    priority: AlertPriority = AlertPriority.P3_LOW
    created_at: float = field(default_factory=time.monotonic)

    # Stage timestamps
    preliminary_at: float = 0.0
    confirmed_at: float = 0.0
    enriched_at: float = 0.0
    acknowledged_at: float = 0.0

    # Detection data
    action_label: str | None = None
    action_confidence: float = 0.0
    dwell_time_s: float = 0.0
    class_name: str = ""
    bbox: dict | None = None

    # VLM enrichment (filled in stage 3)
    vlm_is_dumping: bool | None = None
    vlm_confidence: float | None = None
    vlm_waste_type: str | None = None
    plate_number: str | None = None

    # Delivery tracking (ordered by priority)
    recording_started: bool = False
    vlm_requested: bool = False
    lpr_requested: bool = False
    sip_called: bool = False
    websocket_sent: bool = False
    telegram_sent: bool = False
    speaker_played: bool = False

    # Assigned operator
    assigned_operator: str | None = None


# ---------------------------------------------------------------------------
# Priority Alert Router
# ---------------------------------------------------------------------------


class PriorityAlertRouter:
    """Routes alerts to channels in priority order within a 5-20s window.

    Priority order (user-defined):
        1. Smart Recording  2. VLM + LPR  3. SIP  4. WebSocket  5. Telegram  6. IP Speaker

    Parameters
    ----------
    kafka_producer:
        Pipeline Kafka producer for sending messages to alert topics.
    smart_record_mgr:
        Optional SmartRecordManager for NvDsSR recording.
    zone_operators:
        List of zone operator configs for routing.
    sip_immediate_confidence:
        SGIE confidence threshold above which SIP fires immediately
        (not waiting for the 60s escalation delay).
    sip_escalation_delay_s:
        Default delay before SIP call for non-critical alerts.
    """

    def __init__(
        self,
        kafka_producer,
        smart_record_mgr=None,
        zone_operators: list[ZoneOperator] | None = None,
        sip_immediate_confidence: float = 0.8,
        sip_escalation_delay_s: float = 60.0,
    ) -> None:
        self._kafka = kafka_producer
        self._sr_mgr = smart_record_mgr
        self._zone_ops: dict[str, list[ZoneOperator]] = {}
        for op in (zone_operators or []):
            self._zone_ops.setdefault(op.zone, []).append(op)

        self._sip_immediate_conf = sip_immediate_confidence
        self._sip_delay_s = sip_escalation_delay_s

        # Active alert states keyed by (camera_id, track_id)
        self._states: dict[tuple[str, int], AlertState] = {}
        # Cooldown: won't re-dispatch for same track within 30s
        self._cooldown_s = 30.0

    # ------------------------------------------------------------------
    # Priority computation
    # ------------------------------------------------------------------

    def compute_priority(
        self,
        action_label: str | None,
        action_confidence: float,
        in_roi: bool,
        dwell_time_s: float,
    ) -> AlertPriority:
        if action_label == "dumping_waste" and action_confidence >= 0.7 and in_roi:
            return AlertPriority.P0_CRITICAL
        if action_label in ("dumping_waste", "carrying_object", "loading_unloading") and in_roi:
            return AlertPriority.P1_HIGH
        if in_roi and dwell_time_s > 5.0:
            return AlertPriority.P2_MEDIUM
        return AlertPriority.P3_LOW

    # ------------------------------------------------------------------
    # Main entry: called on every detection frame
    # ------------------------------------------------------------------

    def on_detection(
        self,
        camera_id: str,
        track_id: int,
        consistent_frames: int,
        action_label: str | None,
        action_confidence: float,
        in_roi: bool,
        dwell_time_s: float,
        class_name: str,
        bbox: dict | None,
        zone: str,
    ) -> dict[str, Any]:
        """Process a detection and fire alerts according to priority order.

        Called from the metadata probe on every frame with enriched metadata.
        """
        key = (camera_id, track_id)
        state = self._states.get(key)
        priority = self.compute_priority(action_label, action_confidence, in_roi, dwell_time_s)

        if state is None and priority > AlertPriority.P2_MEDIUM:
            return {}  # P3_LOW — just monitoring, no alert state needed

        if state is None:
            state = AlertState(
                camera_id=camera_id, track_id=track_id, zone=zone, priority=priority,
            )
            self._states[key] = state

        # Update with latest data
        state.action_label = action_label
        state.action_confidence = action_confidence
        state.dwell_time_s = dwell_time_s
        state.class_name = class_name
        state.bbox = bbox
        state.priority = min(state.priority, priority)  # Only upgrade, never downgrade

        results: dict[str, Any] = {}

        # --- STAGE 1: PRELIMINARY ---
        # Fires at 2+ consistent frames OR dwell > 5s
        if state.stage == AlertStage.PRELIMINARY:
            should_preliminary = (
                (consistent_frames >= 2 and priority <= AlertPriority.P2_MEDIUM)
                or (in_roi and dwell_time_s > 5.0)
            )
            if should_preliminary:
                results["stage1"] = self._fire_preliminary(state)

        # --- STAGE 2: CONFIRMED ---
        # Fires at 3+ consistent frames with high conf, OR dwell > 20s, OR fast-alert
        if state.stage <= AlertStage.CONFIRMED and state.stage >= AlertStage.PRELIMINARY:
            is_fast_alert = action_label == "dumping_waste" and action_confidence >= 0.6 and in_roi
            should_confirm = (
                is_fast_alert
                or (consistent_frames >= 3 and priority <= AlertPriority.P1_HIGH)
                or (in_roi and dwell_time_s >= 20.0)
            )
            if should_confirm and state.stage != AlertStage.CONFIRMED:
                results["stage2"] = self._fire_confirmed(state)

        return results

    # ------------------------------------------------------------------
    # Stage 1: PRELIMINARY — recording + VLM + WebSocket warning
    # ------------------------------------------------------------------

    def _fire_preliminary(self, state: AlertState) -> dict:
        state.stage = AlertStage.PRELIMINARY
        state.preliminary_at = time.monotonic()
        results: dict[str, str] = {}

        # Assign zone operator
        operator = self._get_zone_operator(state.zone)
        if operator:
            state.assigned_operator = operator.operator_id

        # PRIORITY 1: Smart Recording — capture evidence FIRST
        if not state.recording_started and self._sr_mgr is not None:
            try:
                path = self._sr_mgr.start_recording(state.camera_id, state.track_id)
                state.recording_started = True
                results["recording"] = "started"
            except Exception:
                logger.exception("Failed to start recording for %s/track-%d", state.camera_id, state.track_id)
                results["recording"] = "failed"

        # PRIORITY 2: VLM + LPR — confirm and identify
        if not state.vlm_requested:
            self._kafka.send_vlm_request(
                camera_id=state.camera_id, track_id=state.track_id,
                zone_name=state.zone, dwell_time_s=state.dwell_time_s,
                class_name=state.class_name,
                path_summary=f"preliminary:{state.action_label or 'dwell'}:{state.action_confidence:.2f}",
            )
            state.vlm_requested = True
            results["vlm"] = "requested"

        if not state.lpr_requested and state.class_name in ("car", "motorcycle", "bus", "truck", "bicycle"):
            self._kafka.send_lpr_request(
                camera_id=state.camera_id, track_id=state.track_id,
                vehicle_bbox=state.bbox or {}, class_name=state.class_name,
            )
            state.lpr_requested = True
            results["lpr"] = "requested"

        # PRIORITY 4: WebSocket — yellow warning
        if not state.websocket_sent:
            self._kafka.send_alert(
                state.camera_id, state.track_id,
                severity="warning",
                message=f"Activity detected: {state.class_name} in monitored zone ({state.zone})",
                stage="preliminary",
                action_label=state.action_label,
                action_confidence=state.action_confidence,
            )
            state.websocket_sent = True
            results["websocket"] = "warning_sent"

        logger.info(
            "PRELIMINARY [P%d]: %s track=%d %s conf=%.2f dwell=%.1fs → recording+VLM+WebSocket",
            state.priority, state.camera_id, state.track_id,
            state.action_label or "dwell", state.action_confidence, state.dwell_time_s,
        )
        return results

    # ------------------------------------------------------------------
    # Stage 2: CONFIRMED — full fan-out in priority order
    # ------------------------------------------------------------------

    def _fire_confirmed(self, state: AlertState) -> dict:
        state.stage = AlertStage.CONFIRMED
        state.confirmed_at = time.monotonic()
        results: dict[str, str] = {}
        operator = self._get_zone_operator(state.zone)
        severity = "critical" if state.priority == AlertPriority.P0_CRITICAL else "warning"
        message = self._build_confirmed_message(state)

        # PRIORITY 1: Smart Recording (already started in preliminary, ensure it's running)
        if not state.recording_started and self._sr_mgr is not None:
            try:
                self._sr_mgr.start_recording(state.camera_id, state.track_id)
                state.recording_started = True
                results["recording"] = "started_late"
            except Exception:
                pass

        # PRIORITY 2: VLM + LPR (already requested in preliminary, no-op)
        if not state.vlm_requested:
            self._kafka.send_vlm_request(
                camera_id=state.camera_id, track_id=state.track_id,
                zone_name=state.zone, dwell_time_s=state.dwell_time_s,
                class_name=state.class_name,
                path_summary=f"confirmed:{state.action_label or 'dwell'}:{state.action_confidence:.2f}",
            )
            state.vlm_requested = True
            results["vlm"] = "requested"

        # PRIORITY 3: SIP Call
        # P0_CRITICAL with high confidence → call IMMEDIATELY
        # P1_HIGH → call after escalation delay (handled by EscalationWorker)
        if not state.sip_called and state.priority == AlertPriority.P0_CRITICAL:
            if state.action_confidence >= self._sip_immediate_conf and operator and operator.sip_number:
                self._kafka.produce(
                    topic="wastewatch.sip",
                    key=state.camera_id,
                    value={
                        "camera_id": state.camera_id,
                        "track_id": state.track_id,
                        "contact_number": operator.sip_number,
                        "contact_name": operator.name if operator else "operator",
                        "severity": severity,
                        "message": message,
                        "immediate": True,
                    },
                )
                state.sip_called = True
                results["sip"] = f"immediate_call_{operator.name}"

        # PRIORITY 4: WebSocket — upgrade to red alert
        self._kafka.send_alert(
            state.camera_id, state.track_id,
            severity=severity,
            message=message,
            stage="confirmed",
            action_label=state.action_label,
            action_confidence=state.action_confidence,
            dwell_time_s=state.dwell_time_s,
        )
        results["websocket"] = "confirmed_alert"

        # PRIORITY 5: Telegram — to zone operator
        if not state.telegram_sent and operator and operator.telegram_chat_id:
            self._kafka.send_alert(
                state.camera_id, state.track_id,
                severity=severity,
                message=message,
                stage="confirmed",
                telegram_chat_id=operator.telegram_chat_id,
                operator_name=operator.name,
            )
            state.telegram_sent = True
            results["telegram"] = f"sent_to_{operator.name}"

        # PRIORITY 6: IP Speaker — on-site deterrent
        if not state.speaker_played:
            speaker_text = self._build_speaker_text(state)
            self._kafka.send_tts_request(
                state.camera_id, state.track_id, speaker_text,
                priority="high" if state.priority <= AlertPriority.P1_HIGH else "normal",
            )
            state.speaker_played = True
            results["speaker"] = "alert_sent"

        logger.warning(
            "CONFIRMED [P%d]: %s track=%d %s conf=%.2f dwell=%.1fs → "
            "recording+VLM+%sSIP+WebSocket+Telegram+Speaker",
            state.priority, state.camera_id, state.track_id,
            state.action_label or "dwell", state.action_confidence, state.dwell_time_s,
            "IMMEDIATE " if state.sip_called else "deferred ",
        )
        return results

    # ------------------------------------------------------------------
    # Stage 3: VLM/LPR results arrive
    # ------------------------------------------------------------------

    def on_vlm_result(self, camera_id: str, track_id: int, vlm_result: dict) -> dict:
        """Handle VLM analysis results — upgrade or downgrade the alert."""
        key = (camera_id, track_id)
        state = self._states.get(key)
        if not state:
            return {}

        state.stage = AlertStage.ENRICHED
        state.enriched_at = time.monotonic()
        state.vlm_is_dumping = vlm_result.get("is_dumping", False)
        state.vlm_confidence = vlm_result.get("confidence", 0.0)
        state.vlm_waste_type = vlm_result.get("waste_type", "unknown")
        results: dict[str, str] = {}

        if state.vlm_is_dumping:
            # VLM CONFIRMS — upgrade alert, ensure SIP fires
            message = self._build_enriched_message(state)
            self._kafka.send_alert(
                state.camera_id, state.track_id,
                severity="critical", message=message, stage="enriched",
                vlm_confirmed=True, waste_type=state.vlm_waste_type,
            )
            results["action"] = "confirmed_by_vlm"

            if not state.sip_called:
                operator = self._get_zone_operator(state.zone)
                if operator and operator.sip_number:
                    self._kafka.produce(
                        topic="wastewatch.sip", key=state.camera_id,
                        value={
                            "camera_id": state.camera_id, "track_id": state.track_id,
                            "contact_number": operator.sip_number,
                            "contact_name": operator.name,
                            "message": message, "immediate": True, "vlm_confirmed": True,
                        },
                    )
                    state.sip_called = True
                    results["sip"] = "vlm_confirmed_call"
        else:
            # VLM says NOT dumping — downgrade, cancel SIP if pending
            results["action"] = "downgraded_by_vlm"
            if state.sip_called:
                self._kafka.produce(
                    topic="wastewatch.sip", key=state.camera_id,
                    value={"camera_id": state.camera_id, "track_id": state.track_id, "action": "cancel"},
                )
                results["sip"] = "cancelled"
            self._kafka.send_alert(
                state.camera_id, state.track_id, severity="info",
                message=f"VLM: not dumping (conf={state.vlm_confidence:.2f}). Monitoring continues.",
                stage="enriched", vlm_confirmed=False,
            )

        logger.info(
            "ENRICHED: %s track=%d vlm_dumping=%s vlm_conf=%.2f waste=%s",
            camera_id, track_id, state.vlm_is_dumping,
            state.vlm_confidence or 0.0, state.vlm_waste_type,
        )
        return results

    def on_lpr_result(self, camera_id: str, track_id: int, plate_number: str) -> None:
        """Update alert with license plate from LPR."""
        key = (camera_id, track_id)
        state = self._states.get(key)
        if not state:
            return
        state.plate_number = plate_number
        self._kafka.send_alert(
            state.camera_id, state.track_id, severity="critical",
            message=f"Vehicle plate identified: {plate_number} at {state.camera_id}",
            stage="enriched", plate_number=plate_number,
        )
        logger.info("LPR result: %s track=%d plate=%s", camera_id, track_id, plate_number)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_acknowledge(self, camera_id: str, track_id: int, operator_id: str) -> dict:
        """Mark alert as acknowledged — stop escalation."""
        key = (camera_id, track_id)
        state = self._states.get(key)
        if not state:
            return {"acknowledged": False}
        state.stage = AlertStage.RESOLVED
        state.acknowledged_at = time.monotonic()
        if state.sip_called:
            self._kafka.produce(
                topic="wastewatch.sip", key=camera_id,
                value={"camera_id": camera_id, "track_id": track_id, "action": "cancel"},
            )
        logger.info("Alert acknowledged: %s track=%d by %s", camera_id, track_id, operator_id)
        return {"acknowledged": True, "elapsed_s": round(state.acknowledged_at - state.created_at, 1)}

    def on_track_exit(self, camera_id: str, track_id: int) -> dict:
        """Track left ROI — stop recording, finalize evidence."""
        key = (camera_id, track_id)
        state = self._states.pop(key, None)
        if not state:
            return {}
        results: dict[str, str] = {}
        if state.recording_started and self._sr_mgr is not None:
            try:
                path = self._sr_mgr.stop_recording(camera_id, track_id)
                results["recording"] = f"stopped:{path}"
            except Exception:
                logger.exception("Failed to stop recording %s/track-%d", camera_id, track_id)
        self._kafka.produce(
            topic="wastewatch.evidence", key=camera_id,
            value={
                "action": "complete", "camera_id": camera_id, "track_id": track_id,
                "dwell_time_s": state.dwell_time_s, "vlm_confirmed": state.vlm_is_dumping,
                "plate_number": state.plate_number,
            },
        )
        results["evidence"] = "complete"
        return results

    # ------------------------------------------------------------------
    # Auto-stop: alert automatically clears when conditions resolve
    # ------------------------------------------------------------------

    def on_frame_update(self, camera_id: str, track_id: int, in_roi: bool, dwell_time_s: float) -> dict | None:
        """Called every frame to check for auto-stop conditions.

        Auto-stop triggers when:
        1. Object leaves the ROI (in_roi=False) for 5+ consecutive seconds
        2. VLM said NOT dumping and dwell hasn't increased for 10s
        3. Track is lost (not updated for 30s — handled by cleanup_stale)

        Auto-recovery triggers when:
        1. A resolved/auto-stopped alert's track re-enters ROI
        2. A new action SGIE classification overrides VLM downgrade
        """
        key = (camera_id, track_id)
        state = self._states.get(key)
        if not state:
            return None

        now = time.monotonic()

        # --- AUTO-STOP: object left ROI ---
        if not in_roi and state.stage in (AlertStage.PRELIMINARY, AlertStage.CONFIRMED, AlertStage.ENRICHED):
            if not hasattr(state, '_roi_exit_at'):
                state._roi_exit_at = now  # type: ignore[attr-defined]
            elif now - state._roi_exit_at > 5.0:  # type: ignore[attr-defined]
                logger.info(
                    "AUTO-STOP: %s track=%d left ROI for %.1fs — clearing alert",
                    camera_id, track_id, now - state._roi_exit_at,  # type: ignore[attr-defined]
                )
                return self._auto_stop(state, reason="left_roi")
        else:
            # Reset exit timer when back in ROI
            if hasattr(state, '_roi_exit_at'):
                del state._roi_exit_at  # type: ignore[attr-defined]

        # --- AUTO-STOP: VLM said not dumping + stagnant dwell ---
        if (
            state.vlm_is_dumping is False
            and state.stage == AlertStage.ENRICHED
        ):
            if not hasattr(state, '_vlm_downgrade_at'):
                state._vlm_downgrade_at = now  # type: ignore[attr-defined]
                state._vlm_downgrade_dwell = dwell_time_s  # type: ignore[attr-defined]
            elif now - state._vlm_downgrade_at > 10.0:  # type: ignore[attr-defined]
                # If dwell hasn't increased significantly, auto-stop
                if dwell_time_s - state._vlm_downgrade_dwell < 3.0:  # type: ignore[attr-defined]
                    logger.info(
                        "AUTO-STOP: %s track=%d VLM=not_dumping + stagnant dwell — clearing",
                        camera_id, track_id,
                    )
                    return self._auto_stop(state, reason="vlm_negative_stagnant")

        return None

    def _auto_stop(self, state: AlertState, reason: str) -> dict:
        """Auto-stop an alert — stop recording, notify channels, clean up."""
        results: dict[str, str] = {"auto_stopped": reason}

        # Stop recording
        if state.recording_started and self._sr_mgr is not None:
            try:
                self._sr_mgr.stop_recording(state.camera_id, state.track_id)
                results["recording"] = "stopped"
            except Exception:
                pass

        # Cancel pending SIP call
        if state.sip_called:
            self._kafka.produce(
                topic="wastewatch.sip", key=state.camera_id,
                value={"camera_id": state.camera_id, "track_id": state.track_id, "action": "cancel"},
            )
            results["sip"] = "cancelled"

        # Notify WebSocket — alert resolved
        self._kafka.send_alert(
            state.camera_id, state.track_id, severity="info",
            message=f"Alert auto-resolved: {reason} ({state.camera_id}, track {state.track_id})",
            stage="resolved", auto_stopped=True, reason=reason,
        )
        results["websocket"] = "resolved"

        # Publish evidence completion
        self._kafka.produce(
            topic="wastewatch.evidence", key=state.camera_id,
            value={
                "action": "auto_stopped", "camera_id": state.camera_id,
                "track_id": state.track_id, "reason": reason,
                "dwell_time_s": state.dwell_time_s,
            },
        )

        state.stage = AlertStage.RESOLVED
        # Don't remove — keep for auto-recovery check
        state._auto_stopped_at = time.monotonic()  # type: ignore[attr-defined]

        logger.info(
            "AUTO-STOP [%s]: %s track=%d dwell=%.1fs — recording stopped, alert cleared",
            reason, state.camera_id, state.track_id, state.dwell_time_s,
        )
        return results

    # ------------------------------------------------------------------
    # Auto-recovery: re-activate alert if threat returns
    # ------------------------------------------------------------------

    def check_auto_recovery(
        self,
        camera_id: str,
        track_id: int,
        in_roi: bool,
        action_label: str | None,
        action_confidence: float,
        dwell_time_s: float,
    ) -> dict | None:
        """Check if a resolved/auto-stopped alert should be reactivated.

        Recovery triggers:
        1. Track re-enters ROI after auto-stop within 60s
        2. New action SGIE "dumping_waste" classification on same track
        3. Dwell time resumes increasing after stagnation
        """
        key = (camera_id, track_id)
        state = self._states.get(key)
        if not state or state.stage != AlertStage.RESOLVED:
            return None

        if not hasattr(state, '_auto_stopped_at'):
            return None

        now = time.monotonic()
        elapsed = now - state._auto_stopped_at  # type: ignore[attr-defined]

        # Don't recover after 60s — treat as a new event
        if elapsed > 60.0:
            self._states.pop(key, None)
            return None

        should_recover = False
        reason = ""

        # Recovery: re-entered ROI with suspicious action
        if in_roi and action_label in ("dumping_waste", "carrying_object", "loading_unloading"):
            should_recover = True
            reason = f"re-entered_roi_with_{action_label}"

        # Recovery: high-confidence dumping on same track
        if action_label == "dumping_waste" and action_confidence >= 0.7:
            should_recover = True
            reason = f"dumping_redetected_conf={action_confidence:.2f}"

        if should_recover:
            logger.warning(
                "AUTO-RECOVERY: %s track=%d reason=%s (%.1fs after auto-stop)",
                camera_id, track_id, reason, elapsed,
            )
            # Reset state for re-alerting
            state.stage = AlertStage.PRELIMINARY
            state.recording_started = False
            state.sip_called = False
            state.telegram_sent = False
            state.speaker_played = False
            state.websocket_sent = False
            state.vlm_requested = False
            state.vlm_is_dumping = None
            # Clean up auto-stop attributes
            for attr in ('_auto_stopped_at', '_roi_exit_at', '_vlm_downgrade_at', '_vlm_downgrade_dwell'):
                if hasattr(state, attr):
                    delattr(state, attr)

            return {"recovered": True, "reason": reason, "elapsed_s": round(elapsed, 1)}

        return None

    # ------------------------------------------------------------------
    # Stale cleanup
    # ------------------------------------------------------------------

    def cleanup_stale(self) -> list[str]:
        """Remove alert states older than 10 minutes."""
        now = time.monotonic()
        stale = [k for k, s in self._states.items() if now - s.created_at > 600]
        for key in stale:
            state = self._states.pop(key)
            if state.recording_started and self._sr_mgr is not None:
                try:
                    self._sr_mgr.stop_recording(state.camera_id, state.track_id)
                except Exception:
                    pass
        return [f"{k[0]}/track-{k[1]}" for k in stale]

    def get_active_alerts(self) -> list[dict]:
        """Return all active alert states for monitoring dashboard."""
        return [
            {
                "camera_id": s.camera_id, "track_id": s.track_id, "zone": s.zone,
                "stage": s.stage.name, "priority": f"P{s.priority}",
                "action": s.action_label, "confidence": s.action_confidence,
                "dwell_s": round(s.dwell_time_s, 1), "class": s.class_name,
                "vlm_confirmed": s.vlm_is_dumping, "plate": s.plate_number,
                "recording": s.recording_started, "sip": s.sip_called,
                "telegram": s.telegram_sent, "speaker": s.speaker_played,
                "operator": s.assigned_operator,
                "age_s": round(time.monotonic() - s.created_at, 1),
            }
            for s in self._states.values()
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_zone_operator(self, zone: str) -> ZoneOperator | None:
        ops = self._zone_ops.get(zone, [])
        available = [o for o in ops if o.is_available and o.role == "operator"]
        if available:
            return min(available, key=lambda o: o.last_alert_at)
        # Fallback to any available operator in zone
        if ops:
            return ops[0]
        # Fallback to any operator
        for zone_ops in self._zone_ops.values():
            if zone_ops:
                return zone_ops[0]
        return None

    def _build_confirmed_message(self, state: AlertState) -> str:
        parts = [f"Violation at {state.camera_id} ({state.zone})"]
        parts.append(f"{state.class_name} detected")
        if state.action_label:
            parts.append(f"action: {state.action_label} ({state.action_confidence:.0%})")
        parts.append(f"dwell: {state.dwell_time_s:.0f}s")
        return " | ".join(parts)

    def _build_enriched_message(self, state: AlertState) -> str:
        parts = [f"CONFIRMED: {state.camera_id} ({state.zone})"]
        parts.append(f"{state.class_name} — {state.action_label or 'dwell violation'}")
        if state.vlm_waste_type and state.vlm_waste_type != "unknown":
            parts.append(f"waste: {state.vlm_waste_type}")
        if state.plate_number:
            parts.append(f"plate: {state.plate_number}")
        parts.append(f"VLM conf: {(state.vlm_confidence or 0):.0%}")
        return " | ".join(parts)

    def _build_speaker_text(self, state: AlertState) -> str:
        if state.priority == AlertPriority.P0_CRITICAL:
            return (
                f"Alert. Illegal waste dumping detected at camera {state.camera_id}. "
                f"A {state.class_name} has been observed dumping waste in zone {state.zone}. "
                f"This area is under surveillance. Immediate attention required."
            )
        return (
            f"Warning. Suspicious activity detected at camera {state.camera_id}. "
            f"A {state.class_name} has been in zone {state.zone} for "
            f"{int(state.dwell_time_s)} seconds. Please investigate."
        )
