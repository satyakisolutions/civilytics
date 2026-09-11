# pipeline/app/preprocessor.py
"""ROI-based frame preprocessor — crops to bounding rect of polygon ROIs.

Instead of running nvinfer on the full 1920x1080 frame, this preprocessor
computes the axis-aligned bounding rectangle of all polygon ROIs for each
camera and generates an nvdspreprocess config that crops to only those regions.

Users edit polygon ROIs in the frontend; the bounding rect is auto-derived.
This saves significant GPU by avoiding full-frame inference when the ROI
covers only a portion of the frame.
"""
import logging
from pathlib import Path

from app.roi import ROIManager

logger = logging.getLogger(__name__)

PREPROCESS_CONFIG_PATH = Path("/tmp/wastewatch/nvdspreprocess.txt")


def generate_preprocess_config(
    roi_manager: ROIManager,
    camera_ids: list[str],
    frame_width: int = 1920,
    frame_height: int = 1080,
    network_width: int = 640,
    network_height: int = 640,
) -> str | None:
    """Generate nvdspreprocess config for ROI-based cropping.

    For each camera with defined polygon ROIs, the merged bounding rect
    is used as the crop region for inference. Cameras without ROIs fall
    back to full-frame inference.

    Returns the config file path, or None if no ROIs are defined.
    """
    rois_found = False
    sections: list[str] = []

    sections.append("[property]")
    sections.append("enable=1")
    sections.append(f"target-unique-ids=0")
    sections.append(f"network-input-shape=3;{network_height};{network_width}")
    sections.append("network-input-order=1")
    sections.append("maintain-aspect-ratio=1")
    sections.append("symmetric-padding=1")
    sections.append("processing-width=640")
    sections.append("processing-height=640")
    sections.append("")

    for camera_id in camera_ids:
        crop_rect = roi_manager.get_merged_crop_rect(camera_id)
        if crop_rect is None:
            continue

        rois_found = True
        px, py, pw, ph = crop_rect.to_pixel(frame_width, frame_height)

        # Ensure minimum crop size for nvinfer
        pw = max(pw, 64)
        ph = max(ph, 64)

        source_id = int(camera_id.replace("cam-", "")) if camera_id.startswith("cam-") else 0

        sections.append(f"[source-{source_id}]")
        sections.append(f"enable=1")
        sections.append(f"roi-params-src-{source_id}={px};{py};{pw};{ph}")
        sections.append("")

        coverage_pct = round(crop_rect.w * crop_rect.h * 100, 1)
        logger.info(
            "Camera %s: ROI crop rect (%d,%d %dx%d) covers %.1f%% of frame — "
            "inference GPU load reduced by ~%.0f%%",
            camera_id, px, py, pw, ph, coverage_pct, 100 - coverage_pct,
        )

    if not rois_found:
        logger.info("No ROI polygons defined — using full-frame inference for all cameras")
        return None

    config_text = "\n".join(sections)
    PREPROCESS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREPROCESS_CONFIG_PATH.write_text(config_text)
    logger.info("Wrote nvdspreprocess config to %s", PREPROCESS_CONFIG_PATH)
    return str(PREPROCESS_CONFIG_PATH)


class ROIPreprocessor:
    """Manages ROI-based preprocessing for the DeepStream pipeline.

    When polygon ROIs are defined, this preprocessor computes the bounding
    rect crop regions. The poly→rect relationship is automatic:
    - Users edit ONLY polygon ROIs (via frontend editor or REST API)
    - Bounding rects are auto-derived with padding
    - nvinfer processes only the cropped region
    """

    def __init__(self, roi_manager: ROIManager, frame_width: int = 1920, frame_height: int = 1080):
        self._roi_manager = roi_manager
        self._frame_width = frame_width
        self._frame_height = frame_height
        self._active_crops: dict[str, tuple[int, int, int, int]] = {}

    def update_crops(self, camera_ids: list[str]) -> dict[str, tuple[int, int, int, int]]:
        """Recompute crop regions for all cameras. Call after ROI polygon changes.

        Returns dict of camera_id → (x, y, w, h) pixel crop rects.
        Cameras without ROIs are omitted (full-frame fallback).
        """
        self._active_crops.clear()
        for camera_id in camera_ids:
            crop_rect = self._roi_manager.get_merged_crop_rect(camera_id)
            if crop_rect is not None:
                self._active_crops[camera_id] = crop_rect.to_pixel(
                    self._frame_width, self._frame_height,
                )
        return dict(self._active_crops)

    def get_crop(self, camera_id: str) -> tuple[int, int, int, int] | None:
        """Get the crop rect for a camera, or None for full-frame."""
        return self._active_crops.get(camera_id)

    def should_crop(self, camera_id: str) -> bool:
        return camera_id in self._active_crops

    def get_gpu_savings_estimate(self) -> dict[str, float]:
        """Estimate GPU savings per camera from ROI cropping."""
        savings = {}
        full_area = self._frame_width * self._frame_height
        for camera_id, (_, _, w, h) in self._active_crops.items():
            crop_area = w * h
            savings[camera_id] = round((1 - crop_area / full_area) * 100, 1)
        return savings
