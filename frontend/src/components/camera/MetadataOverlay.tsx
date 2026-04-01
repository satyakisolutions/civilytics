import type { FrameMetadata, BBoxDetection } from "@/types/monitoring";

interface MetadataOverlayProps {
  metadata: FrameMetadata | null;
  width: number;
  height: number;
}

const CLASS_COLORS: Record<string, string> = {
  person: "#22c55e",    // green
  bicycle: "#3b82f6",   // blue
  car: "#eab308",       // yellow
  motorcycle: "#f97316", // orange
  bus: "#a855f7",       // purple
  truck: "#ef4444",     // red
};

function getDwellColor(dwellTime: number | undefined): string {
  if (!dwellTime || dwellTime <= 5) return "#22c55e"; // green
  if (dwellTime <= 15) return "#eab308"; // yellow
  return "#ef4444"; // red
}

function DetectionBox({ detection, width, height }: { detection: BBoxDetection; width: number; height: number }) {
  const [x1, y1, x2, y2] = detection.bbox;

  const classColor = CLASS_COLORS[detection.class_name] ?? "#ffffff";
  const borderColor = detection.dwell_time_s != null
    ? getDwellColor(detection.dwell_time_s)
    : classColor;

  const left = x1 * width;
  const top = y1 * height;
  const boxWidth = (x2 - x1) * width;
  const boxHeight = (y2 - y1) * height;

  const isViolation = detection.fsm_state === "violation";

  const label = [
    detection.class_name,
    detection.track_id != null ? `#${detection.track_id}` : null,
    `${(detection.confidence * 100).toFixed(0)}%`,
    detection.dwell_time_s != null ? `${detection.dwell_time_s.toFixed(1)}s` : null,
  ].filter(Boolean).join(' | ');

  return (
    <>
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top}px`,
          width: `${boxWidth}px`,
          height: `${boxHeight}px`,
          border: `2px solid ${borderColor}`,
          pointerEvents: "none",
          animation: isViolation ? "violationPulse 0.8s ease-in-out infinite alternate" : undefined,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top - 20}px`,
          backgroundColor: borderColor,
          color: "#000",
          fontSize: "11px",
          fontWeight: 600,
          padding: "1px 4px",
          whiteSpace: "nowrap",
          pointerEvents: "none",
          lineHeight: "16px",
          display: "flex",
          alignItems: "center",
          gap: "4px",
        }}
      >
        {label}
        {detection.in_roi && (
          <span
            style={{
              backgroundColor: "#3b82f6",
              color: "#fff",
              fontSize: "9px",
              fontWeight: 700,
              padding: "0 3px",
              borderRadius: "2px",
              letterSpacing: "0.03em",
            }}
          >
            IN ROI
          </span>
        )}
      </div>
    </>
  );
}

export function MetadataOverlay({ metadata, width, height }: MetadataOverlayProps) {
  if (!metadata || width === 0 || height === 0) return null;

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
      `}</style>
      {metadata.detections.map((det, i) => (
        <DetectionBox key={`${det.class_id}-${det.track_id ?? i}`} detection={det} width={width} height={height} />
      ))}
      <div
        style={{
          position: "absolute",
          bottom: 4,
          right: 4,
          backgroundColor: "rgba(0,0,0,0.7)",
          color: "#fff",
          fontSize: "11px",
          padding: "2px 6px",
          borderRadius: "4px",
        }}
      >
        {metadata.detections.length} detections
      </div>
    </div>
  );
}
