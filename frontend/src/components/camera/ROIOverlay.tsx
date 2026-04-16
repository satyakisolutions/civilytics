import { useState } from "react";
import type { ROIPolygon, ROIPoint } from "@/types/monitoring";

interface ROIOverlayProps {
  polygons: ROIPolygon[];
  /**
   * Optional explicit pixel dimensions. If 0 or omitted the overlay
   * fills its parent and resolves coordinates against its own client
   * rect at click-time, so it stays usable even before the
   * ResizeObserver in CameraFeed has fired (which used to hide the
   * editor entirely on first paint).
   */
  width?: number;
  height?: number;
  isEditing?: boolean;
  currentPoints?: ROIPoint[];
  onCanvasClick?: (point: ROIPoint) => void;
  onFinish?: (name: string) => void;
  onCancel?: () => void;
  onUndoPoint?: () => void;
  onRemovePolygon?: (index: number) => void;
}

export function ROIOverlay({
  polygons,
  isEditing = false,
  currentPoints = [],
  onCanvasClick,
  onFinish,
  onCancel,
  onUndoPoint,
  onRemovePolygon,
}: ROIOverlayProps) {
  const [pendingName, setPendingName] = useState("Restricted Zone");
  const [hoveredPolygon, setHoveredPolygon] = useState<number | null>(null);

  const handleClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!isEditing || !onCanvasClick) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    onCanvasClick({
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    });
  };

  // Percentage-based coordinates — works without explicit width/height
  // so the editor never fails to render before the ResizeObserver fires.
  const pct = (v: number) => `${v * 100}%`;
  const canFinish = currentPoints.length >= 3;

  return (
    <>
      <svg
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        width="100%"
        height="100%"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          pointerEvents: isEditing ? "auto" : "none",
          cursor: isEditing ? "crosshair" : "default",
          zIndex: isEditing ? 25 : 5,
        }}
        onClick={handleClick}
      >
        {polygons.map((polygon, i) => {
          const isHovered = hoveredPolygon === i;
          const cx = polygon.points.reduce((s, p) => s + p.x * 100, 0) / (polygon.points.length || 1);
          const cy = polygon.points.reduce((s, p) => s + p.y * 100, 0) / (polygon.points.length || 1);
          return (
            <g
              key={`roi-${i}`}
              onMouseEnter={() => isEditing && setHoveredPolygon(i)}
              onMouseLeave={() => setHoveredPolygon(null)}
              style={{ cursor: isEditing ? "pointer" : "default", pointerEvents: isEditing ? "auto" : "none" }}
            >
              <polygon
                points={polygon.points.map(p => `${p.x * 100},${p.y * 100}`).join(" ")}
                fill={isHovered ? "rgba(239, 68, 68, 0.25)" : "rgba(59, 130, 246, 0.15)"}
                stroke={isHovered ? "#ef4444" : "#3b82f6"}
                strokeWidth={isHovered ? 0.6 : 0.4}
                vectorEffect="non-scaling-stroke"
              />
              {polygon.points.length > 0 && (
                <text
                  x={polygon.points[0].x * 100 + 0.5}
                  y={polygon.points[0].y * 100 - 0.5}
                  fill={isHovered ? "#ef4444" : "#3b82f6"}
                  fontSize="2.5"
                  fontWeight={600}
                >
                  {polygon.name}
                </text>
              )}
              {isEditing && isHovered && onRemovePolygon && (
                <g
                  onClick={(e) => { e.stopPropagation(); onRemovePolygon(i); setHoveredPolygon(null); }}
                  style={{ cursor: "pointer" }}
                >
                  <circle cx={cx} cy={cy} r={3} fill="rgba(239, 68, 68, 0.9)" stroke="#fff" strokeWidth={0.3} />
                  <text x={cx} y={cy + 1} fill="#fff" fontSize="3" fontWeight={700} textAnchor="middle">×</text>
                </g>
              )}
            </g>
          );
        })}
        {isEditing && currentPoints.length > 0 && (
          <g>
            <polyline
              points={currentPoints.map(p => `${p.x * 100},${p.y * 100}`).join(" ")}
              fill="none"
              stroke="#f97316"
              strokeWidth={0.5}
              strokeDasharray="1,0.6"
              vectorEffect="non-scaling-stroke"
            />
            {currentPoints.map((p, i) => (
              <circle
                key={`pt-${i}`}
                cx={p.x * 100}
                cy={p.y * 100}
                r={0.9}
                fill="#f97316"
                stroke="#fff"
                strokeWidth={0.3}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </g>
        )}
      </svg>

      {/* Editing mode border indicator */}
      {isEditing && (
        <div
          className="absolute inset-0 border-2 border-orange-500 rounded pointer-events-none z-30"
          style={{ boxShadow: 'inset 0 0 20px rgba(249, 115, 22, 0.15)' }}
        />
      )}

      {/* Floating control strip — top of video when editing */}
      {isEditing && (
        <div
          className="absolute left-2 right-2 top-2 flex flex-wrap items-center gap-2 px-2 py-1.5 rounded bg-background/90 backdrop-blur-sm border border-orange-500/60 z-40 text-[11px]"
          onClick={e => e.stopPropagation()}
        >
          <span className="text-orange-400 font-semibold">ROI</span>
          {polygons.length > 0 && (
            <span className="text-blue-400">{polygons.length} zone{polygons.length === 1 ? "" : "s"} — hover to delete</span>
          )}
          <span className="text-foreground/70">
            {currentPoints.length} point{currentPoints.length === 1 ? "" : "s"} — click on the video to add
          </span>
          <input
            value={pendingName}
            onChange={e => setPendingName(e.target.value)}
            placeholder="Zone name"
            className="ml-auto h-6 px-1.5 rounded bg-background/60 border border-border text-[11px] w-32"
          />
          <button
            onClick={() => onUndoPoint?.()}
            disabled={currentPoints.length === 0}
            className="h-6 px-2 rounded bg-secondary text-secondary-foreground disabled:opacity-40"
          >
            Undo
          </button>
          <button
            onClick={() => canFinish && onFinish?.(pendingName)}
            disabled={!canFinish}
            className="h-6 px-2 rounded bg-orange-500/90 text-white font-semibold disabled:opacity-40"
          >
            Save ROI
          </button>
          <button
            onClick={() => onCancel?.()}
            className="h-6 px-2 rounded bg-secondary text-secondary-foreground"
          >
            Cancel
          </button>
        </div>
      )}
    </>
  );
}
