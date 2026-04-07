// Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
import type { FrameMetadata, BBoxDetection, EventFSMState, CameraAlertFlag } from "@/types/monitoring";

interface MetadataOverlayProps {
  metadata: FrameMetadata | null;
  width: number;
  height: number;
}

// ---------------------------------------------------------------------------
// FSM state → bbox color (constant per state, not gradient)
// ---------------------------------------------------------------------------

const FSM_COLORS: Record<EventFSMState, string> = {
  idle:      "#101010", // black  — detected, not in ROI
  enter:     "#3b82f6", // blue   — just entered ROI
  observe:   "#f59e0b", // amber  — in ROI, building dwell
  violation: "#ef4444", // red    — violation confirmed
  alerting:  "#ef4444", // red    — alert dispatched
  escalated: "#dc2626", // dark red — escalated to supervisor
  exited:    "#101010", // black  — left ROI
};

const FSM_LABELS: Record<EventFSMState, string> = {
  idle:      "",
  enter:     "ENTER",
  observe:   "OBSERVE",
  violation: "VIOLATION",
  alerting:  "ALERTING",
  escalated: "ESCALATED",
  exited:    "EXITED",
};

// Pulsing states — violation/alerting/escalated pulse to draw attention
const FSM_PULSE: Record<EventFSMState, string | undefined> = {
  idle:      undefined,
  enter:     undefined,
  observe:   undefined,
  violation: "violationPulse 1s ease-in-out infinite alternate",
  alerting:  "violationPulse 0.8s ease-in-out infinite alternate",
  escalated: "violationPulse 0.5s ease-in-out infinite alternate",
  exited:    "exitFadeOut 1.5s ease-out forwards",
};

// Class name → icon color for the label background (subtle, secondary to FSM)
const CLASS_LABEL_COLORS: Record<string, string> = {
  person:     "#22c55e",
  bicycle:    "#3b82f6",
  car:        "#eab308",
  motorcycle: "#f97316",
  bus:        "#a855f7",
  truck:      "#ef4444",
  // Carried items
  backpack:   "#06b6d4",
  handbag:    "#06b6d4",
  suitcase:   "#06b6d4",
  // Waste objects
  bottle:     "#f43f5e",
  chair:      "#f43f5e",
  couch:      "#f43f5e",
  refrigerator: "#f43f5e",
  tv:         "#f43f5e",
  toilet:     "#f43f5e",
};

function getBboxColor(detection: BBoxDetection): string {
  const fsmState = detection.fsm_state ?? "idle";
  return FSM_COLORS[fsmState] ?? "#101010";
}

function getAnimation(detection: BBoxDetection): string | undefined {
  const fsmState = detection.fsm_state ?? "idle";
  return FSM_PULSE[fsmState];
}

// ---------------------------------------------------------------------------
// Detection Box
// ---------------------------------------------------------------------------

function DetectionBox({ detection, width, height }: { detection: BBoxDetection; width: number; height: number }) {
  const [x1, y1, x2, y2] = detection.bbox;
  const borderColor = getBboxColor(detection);
  const animation = getAnimation(detection);
  const fsmState = detection.fsm_state ?? "idle";
  const fsmLabel = FSM_LABELS[fsmState];

  const left = x1 * width;
  const top = y1 * height;
  const boxWidth = (x2 - x1) * width;
  const boxHeight = (y2 - y1) * height;

  const isViolation = fsmState === "violation" || fsmState === "alerting" || fsmState === "escalated";

  // --- Primary label: action or class + track ---
  const actionText = detection.action_label
    ? (detection.action_fast_alert
        ? `\u26a0 ${detection.action_label.toUpperCase()}`
        : detection.action_label)
    : null;

  const primaryLabel = [
    actionText ?? detection.class_name,
    detection.track_id != null ? `#${detection.track_id}` : null,
  ].filter(Boolean).join(" ");

  // --- Secondary label: dwell time only ---
  const secondaryParts = [
    detection.dwell_time_s != null ? `${detection.dwell_time_s.toFixed(1)}s` : null,
  ].filter(Boolean);

  return (
    <>
      {/* Bbox rectangle — color driven by FSM state */}
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top}px`,
          width: `${boxWidth}px`,
          height: `${boxHeight}px`,
          border: `2px solid ${borderColor}`,
          borderRadius: "2px",
          pointerEvents: "none",
          animation,
        }}
      />

      {/* Top label bar */}
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${Math.max(0, top - 32)}px`,
          display: "flex",
          flexDirection: "column",
          gap: "1px",
          pointerEvents: "none",
        }}
      >
        {/* Row 1: Action/class + track ID */}
        <div
          style={{
            backgroundColor: borderColor,
            color: isViolation ? "#fff" : "#000",
            fontSize: "11px",
            fontFamily: "'Poppins', sans-serif",
            fontWeight: 600,
            padding: "1px 6px",
            whiteSpace: "nowrap",
            lineHeight: "15px",
            display: "flex",
            alignItems: "center",
            gap: "4px",
            borderRadius: "2px 2px 0 0",
          }}
        >
          {primaryLabel}

          {/* FSM state badge */}
          {fsmLabel && (
            <span
              style={{
                backgroundColor: "rgba(0,0,0,0.3)",
                color: "#fff",
                fontSize: "9px",
                fontWeight: 700,
                padding: "0 4px",
                borderRadius: "2px",
                letterSpacing: "0.05em",
              }}
            >
              {fsmLabel}
            </span>
          )}

          {/* IN ROI badge */}
          {detection.in_roi && (
            <span
              style={{
                backgroundColor: "#3b82f6",
                color: "#fff",
                fontSize: "9px",
                fontWeight: 700,
                padding: "0 4px",
                borderRadius: "2px",
              }}
            >
              ROI
            </span>
          )}

          {/* Recording indicator */}
          {detection.recording && (
            <span
              style={{
                backgroundColor: "#ef4444",
                color: "#fff",
                fontSize: "9px",
                fontWeight: 700,
                padding: "0 4px",
                borderRadius: "2px",
                display: "flex",
                alignItems: "center",
                gap: "2px",
              }}
            >
              <span style={{ display: "inline-block", width: "6px", height: "6px", borderRadius: "50%", backgroundColor: "#fff", animation: "violationPulse 1s ease-in-out infinite alternate" }} />
              REC
            </span>
          )}
        </div>

        {/* Row 2: Secondary info (confidence, dwell, action confidence) */}
        <div
          style={{
            backgroundColor: "rgba(0,0,0,0.75)",
            color: "#e5e7eb",
            fontSize: "10px",
            fontFamily: "'Poppins', sans-serif",
            fontWeight: 400,
            padding: "1px 5px",
            whiteSpace: "nowrap",
            lineHeight: "14px",
            borderRadius: "0 0 2px 2px",
          }}
        >
          {secondaryParts.join(" | ")}

          {/* Plate number (appears after LPR) */}
          {detection.plate_number && (
            <span style={{ marginLeft: "6px", color: "#fbbf24", fontWeight: 600 }}>
              {detection.plate_number}
            </span>
          )}
        </div>
      </div>

      {/* Bottom-left: VLM result (appears after enrichment) */}
      {detection.vlm_result && (
        <div
          style={{
            position: "absolute",
            left: `${left}px`,
            top: `${top + boxHeight + 2}px`,
            backgroundColor: detection.vlm_result.is_dumping
              ? "rgba(239, 68, 68, 0.9)"
              : "rgba(34, 197, 94, 0.85)",
            color: "#fff",
            fontSize: "11px",
            fontFamily: "'Poppins', sans-serif",
            fontWeight: 500,
            padding: "1px 6px",
            borderRadius: "2px",
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          VLM: {detection.vlm_result.is_dumping ? "DUMPING" : "clear"}
          {detection.vlm_result.waste_type && detection.vlm_result.waste_type !== "none"
            ? ` | ${detection.vlm_result.waste_type.replace("_", " ")}`
            : ""}
          {` | ${(detection.vlm_result.confidence * 100).toFixed(0)}%`}
        </div>
      )}

      {/* Category indicator (waste_object gets a small icon) */}
      {detection.category === "waste_object" && (
        <div
          style={{
            position: "absolute",
            left: `${left + boxWidth - 20}px`,
            top: `${top + 2}px`,
            backgroundColor: "#f43f5e",
            color: "#fff",
            fontSize: "9px",
            fontWeight: 700,
            width: "18px",
            height: "18px",
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}
        >
          W
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Per-camera alert flag banner
// ---------------------------------------------------------------------------

const ALERT_LEVEL_STYLES: Record<string, { bg: string; pulse: boolean; icon: string }> = {
  monitoring:  { bg: "#3b82f6", pulse: false, icon: "MON" },
  suspicious:  { bg: "#f59e0b", pulse: false, icon: "SUS" },
  violation:   { bg: "#ef4444", pulse: true,  icon: "VIO" },
  critical:    { bg: "#dc2626", pulse: true,  icon: "CRT" },
};

function CameraAlertBanner({ alert, width }: { alert: CameraAlertFlag; width: number }) {
  const style = ALERT_LEVEL_STYLES[alert.level] ?? ALERT_LEVEL_STYLES.monitoring;
  const elapsed = alert.since ? Math.floor((Date.now() - alert.since) / 1000) : null;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: `${width}px`,
        backgroundColor: alert.color || style.bg,
        color: "#fff",
        fontSize: "11px",
        fontFamily: "'Poppins', sans-serif",
        fontWeight: 600,
        padding: "3px 8px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        pointerEvents: "none",
        animation: style.pulse ? "violationPulse 1s ease-in-out infinite alternate" : undefined,
        zIndex: 10,
      }}
    >
      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <span
          style={{
            backgroundColor: "rgba(255,255,255,0.25)",
            padding: "0 4px",
            borderRadius: "2px",
            fontSize: "9px",
            fontWeight: 700,
            letterSpacing: "0.05em",
          }}
        >
          {style.icon}
        </span>
        {alert.text}
      </span>
      {elapsed !== null && (
        <span style={{ fontSize: "10px", opacity: 0.8 }}>
          {elapsed}s
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Frame Overlay
// ---------------------------------------------------------------------------

export function MetadataOverlay({ metadata, width, height }: MetadataOverlayProps) {
  if (!metadata || width === 0 || height === 0) return null;

  const violations = metadata.detections.filter(
    d => d.fsm_state === "violation" || d.fsm_state === "alerting" || d.fsm_state === "escalated"
  );
  const inRoi = metadata.detections.filter(d => d.in_roi);

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: `${width}px`,
        height: `${height}px`,
        pointerEvents: "none",
      }}
    >
      <style>{`
        @keyframes violationPulse {
          from { opacity: 1; }
          to   { opacity: 0.35; }
        }
        @keyframes exitFadeOut {
          0%   { opacity: 0.8; border-color: #101010; }
          100% { opacity: 0; border-color: #101010; }
        }
      `}</style>

      {/* Per-camera alert flag banner (top of frame) */}
      {metadata.camera_alert && metadata.camera_alert.level !== "none" && (
        <CameraAlertBanner alert={metadata.camera_alert} width={width} />
      )}

      {/* Detection boxes */}
      {metadata.detections.map((det, i) => (
        <DetectionBox key={`${det.class_id}-${det.track_id ?? i}`} detection={det} width={width} height={height} />
      ))}

      {/* Frame status bar (bottom) */}
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          display: "flex",
          justifyContent: "space-between",
          padding: "3px 6px",
          backgroundColor: "rgba(0,0,0,0.7)",
          color: "#fff",
          fontSize: "11px",
          fontFamily: "'Poppins', sans-serif",
          fontWeight: 500,
        }}
      >
        <span>{metadata.detections.length} detections | {inRoi.length} in ROI</span>
        {violations.length > 0 && (
          <span style={{ color: "#ef4444", fontWeight: 700, animation: "violationPulse 1s ease-in-out infinite alternate" }}>
            {violations.length} VIOLATION{violations.length > 1 ? "S" : ""}
          </span>
        )}
        {metadata.fps != null && (
          <span style={{ color: "#9ca3af" }}>{metadata.fps} FPS</span>
        )}
      </div>
    </div>
  );
}
