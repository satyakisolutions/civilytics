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


class ROIManager:
    def __init__(self):
        self._polygons: dict[str, list[ROIPolygon]] = {}

    def set_polygons(self, camera_id: str, polygons: list[ROIPolygon]):
        self._polygons[camera_id] = polygons
        logger.info("Set %d ROI polygon(s) for camera %s", len(polygons), camera_id)

    def get_polygons(self, camera_id: str) -> list[ROIPolygon]:
        return self._polygons.get(camera_id, [])

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
