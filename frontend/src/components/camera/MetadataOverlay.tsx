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

function DetectionBox({ detection, width, height }: { detection: BBoxDetection; width: number; height: number }) {
  const [x1, y1, x2, y2] = detection.bbox;
  const color = CLASS_COLORS[detection.class_name] ?? "#ffffff";

  const left = x1 * width;
  const top = y1 * height;
  const boxWidth = (x2 - x1) * width;
  const boxHeight = (y2 - y1) * height;

  const label = detection.track_id != null
    ? `${detection.class_name} #${detection.track_id} ${(detection.confidence * 100).toFixed(0)}%`
    : `${detection.class_name} ${(detection.confidence * 100).toFixed(0)}%`;

  return (
    <>
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top}px`,
          width: `${boxWidth}px`,
          height: `${boxHeight}px`,
          border: `2px solid ${color}`,
          pointerEvents: "none",
        }}
      />
      <div
        style={{
          position: "absolute",
          left: `${left}px`,
          top: `${top - 20}px`,
          backgroundColor: color,
          color: "#000",
          fontSize: "11px",
          fontWeight: 600,
          padding: "1px 4px",
          whiteSpace: "nowrap",
          pointerEvents: "none",
          lineHeight: "16px",
        }}
      >
        {label}
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
