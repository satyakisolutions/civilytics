import type { ROIPolygon, ROIPoint } from "@/types/monitoring";

interface ROIOverlayProps {
  polygons: ROIPolygon[];
  width: number;
  height: number;
  isEditing?: boolean;
  currentPoints?: ROIPoint[];
  onCanvasClick?: (point: ROIPoint) => void;
}

export function ROIOverlay({
  polygons, width, height,
  isEditing = false, currentPoints = [], onCanvasClick,
}: ROIOverlayProps) {
  if (width === 0 || height === 0) return null;

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isEditing || !onCanvasClick) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onCanvasClick({
      x: (e.clientX - rect.left) / width,
      y: (e.clientY - rect.top) / height,
    });
  };

  return (
    <svg
      width={width} height={height}
      style={{ position: "absolute", top: 0, left: 0, pointerEvents: isEditing ? "auto" : "none", cursor: isEditing ? "crosshair" : "default" }}
      onClick={handleClick}
    >
      {polygons.map((polygon, i) => (
        <g key={`roi-${i}`}>
          <polygon
            points={polygon.points.map(p => `${p.x * width},${p.y * height}`).join(" ")}
            fill="rgba(59, 130, 246, 0.15)"
            stroke="#3b82f6" strokeWidth={2}
            strokeDasharray={isEditing ? "6,3" : "none"}
          />
          {polygon.points.length > 0 && (
            <text x={polygon.points[0].x * width + 4} y={polygon.points[0].y * height - 4}
              fill="#3b82f6" fontSize={11} fontWeight={600}>
              {polygon.name}
            </text>
          )}
        </g>
      ))}
      {isEditing && currentPoints.length > 0 && (
        <g>
          <polyline
            points={currentPoints.map(p => `${p.x * width},${p.y * height}`).join(" ")}
            fill="none" stroke="#f97316" strokeWidth={2} strokeDasharray="4,2"
          />
          {currentPoints.map((p, i) => (
            <circle key={`pt-${i}`} cx={p.x * width} cy={p.y * height}
              r={4} fill="#f97316" stroke="#fff" strokeWidth={1} />
          ))}
        </g>
      )}
    </svg>
  );
}
