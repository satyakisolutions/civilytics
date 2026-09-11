"""YOLO-World zero-shot detector wrapper, implementing the Detector
protocol from bag_trigger.py.

Spec: docs/superpowers/specs/2026-08-31-bag-trigger-gated-dumping-design.md
section 10 (Phase 1). Zero-shot, no training data required to start.

LICENSING NOTE (spec section 10): YOLO-World's official repository is
GPL-3.0. Ultralytics publishes separate open-source and enterprise
licensing terms. Review before any commercial distribution or hosted
use -- this has NOT been cleared as of this writing.

Requires `ultralytics` to be installed -- not yet done in any repo venv
as of this writing (see docs/superpowers/specs/2026-08-31-... section 20
Phase 1, not yet started). This module is written ahead of that install
so Phase 1's feasibility test has real code to run once the dependency
is in place.
"""
from __future__ import annotations

import io

from app.object_lifecycle import BoundingBox

DEFAULT_BAG_CLASSES = [
    "garbage bag", "plastic bag", "sack", "cardboard box", "waste bundle", "bottle", "container",
]


class YOLOWorldDetector:
    """Thin adapter around ultralytics YOLO-World so bag_trigger.py's
    Detector protocol doesn't need to know about ultralytics directly."""

    def __init__(self, model_name: str = "yolov8s-worldv2.pt", confidence_threshold: float = 0.25) -> None:
        from ultralytics import YOLO  # deferred import: only required once this class is used

        self._model = YOLO(model_name)
        self._confidence_threshold = confidence_threshold

    def set_classes(self, classes: list[str]) -> None:
        self._model.set_classes(classes)

    def detect(self, jpeg_bytes: bytes, classes: list[str]):
        from PIL import Image

        from app.bag_trigger import Detection  # local import to avoid a circular import at module load

        self.set_classes(classes)
        image = Image.open(io.BytesIO(jpeg_bytes))
        results = self._model.predict(image, conf=self._confidence_threshold, verbose=False)

        detections = []
        for result in results:
            for box, conf, cls_idx in zip(result.boxes.xywh, result.boxes.conf, result.boxes.cls):
                x_center, y_center, w, h = (float(v) for v in box)
                label = result.names[int(cls_idx)]
                detections.append(Detection(
                    label=label,
                    bbox=BoundingBox(x=x_center - w / 2, y=y_center - h / 2, w=w, h=h),
                    confidence=float(conf),
                ))
        return detections
