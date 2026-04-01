# pipeline/app/roi.py
"""ROI polygon manager for nvdsanalytics and point-in-polygon checks."""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ROIPolygon:
    camera_id: str
    name: str
    points: list[tuple[float, float]]  # normalized (0-1) coordinates
    source_id: int


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


@dataclass
class BoundingRect:
    """Axis-aligned bounding rectangle derived from a polygon (normalized 0-1)."""
    x: float
    y: float
    w: float
    h: float
    camera_id: str

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def to_pixel(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """Return (x, y, w, h) in pixel coordinates."""
        return (
            int(self.x * frame_w),
            int(self.y * frame_h),
            int(self.w * frame_w),
            int(self.h * frame_h),
        )


def polygon_to_bounding_rect(camera_id: str, polygon: ROIPolygon, padding: float = 0.05) -> BoundingRect:
    """Compute the axis-aligned bounding rectangle of a polygon with padding.

    The padding expands the rect by a fraction of its dimensions to ensure
    objects near polygon edges are still captured by inference.
    """
    xs = [p[0] for p in polygon.points]
    ys = [p[1] for p in polygon.points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    w = max_x - min_x
    h = max_y - min_y
    pad_x = w * padding
    pad_y = h * padding

    return BoundingRect(
        x=max(0.0, min_x - pad_x),
        y=max(0.0, min_y - pad_y),
        w=min(1.0 - max(0.0, min_x - pad_x), w + 2 * pad_x),
        h=min(1.0 - max(0.0, min_y - pad_y), h + 2 * pad_y),
        camera_id=camera_id,
    )


class ROIManager:
    def __init__(self):
        self._polygons: dict[str, list[ROIPolygon]] = {}
        self._bounding_rects: dict[str, list[BoundingRect]] = {}

    def set_polygons(self, camera_id: str, polygons: list[ROIPolygon]):
        self._polygons[camera_id] = polygons
        self._bounding_rects[camera_id] = [
            polygon_to_bounding_rect(camera_id, p) for p in polygons
        ]
        logger.info(
            "Set %d ROI polygon(s) for camera %s (bounding rects auto-derived)",
            len(polygons), camera_id,
        )

    def get_polygons(self, camera_id: str) -> list[ROIPolygon]:
        return self._polygons.get(camera_id, [])

    def get_bounding_rects(self, camera_id: str) -> list[BoundingRect]:
        """Get auto-derived bounding rectangles for a camera's polygon ROIs."""
        return self._bounding_rects.get(camera_id, [])

    def get_merged_crop_rect(self, camera_id: str) -> BoundingRect | None:
        """Get a single merged bounding rect covering all ROI polygons for a camera.

        Returns None if no polygons are defined (full-frame inference fallback).
        """
        rects = self._bounding_rects.get(camera_id, [])
        if not rects:
            return None
        min_x = min(r.x for r in rects)
        min_y = min(r.y for r in rects)
        max_x2 = max(r.x2 for r in rects)
        max_y2 = max(r.y2 for r in rects)
        return BoundingRect(
            x=min_x, y=min_y,
            w=max_x2 - min_x, h=max_y2 - min_y,
            camera_id=camera_id,
        )

    def is_in_any_roi(self, camera_id: str, x: float, y: float) -> bool:
        for polygon in self._polygons.get(camera_id, []):
            if point_in_polygon(x, y, polygon.points):
                return True
        return False

    def generate_nvdsanalytics_config(self, config_width: int = 1920, config_height: int = 1080) -> str:
        lines = ["[property]", "enable=1", f"config-width={config_width}", f"config-height={config_height}", ""]
        for camera_id, polygons in self._polygons.items():
            for polygon in polygons:
                section = f"roi-filtering-stream-{polygon.source_id}"
                lines.append(f"[{section}]")
                lines.append("enable=1")
                points_str = ";".join(f"{int(x * config_width)};{int(y * config_height)}" for x, y in polygon.points)
                lines.append(f"roi-RF={points_str}")
                lines.append("inverse-roi=0")
                lines.append("")
        return "\n".join(lines)
