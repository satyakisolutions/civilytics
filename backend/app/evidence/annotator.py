# backend/app/evidence/annotator.py
"""Draw bounding boxes, labels, and dwell time on snapshot frames."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def annotate_frame(
    image_path: str,
    detections: list[dict],
    output_path: str,
    width: int = 1920,
    height: int = 1080,
) -> str | None:
    """Draw bboxes and labels on an image. Returns output path or None on failure.

    Each detection: {class_name, confidence, bbox: {x,y,w,h}, track_id, dwell_time_s}
    Coordinates are normalized (0-1).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not available — skipping annotation")
        # Copy original as fallback
        import shutil
        shutil.copy2(image_path, output_path)
        return output_path

    try:
        img = Image.open(image_path)
        draw = ImageDraw.Draw(img)
        w, h = img.size

        colors = {
            "person": "#22c55e", "bicycle": "#3b82f6", "car": "#eab308",
            "motorcycle": "#f97316", "bus": "#a855f7", "truck": "#ef4444",
        }

        for det in detections:
            bbox = det.get("bbox", {})
            x1 = int(bbox.get("x", 0) * w)
            y1 = int(bbox.get("y", 0) * h)
            x2 = int((bbox.get("x", 0) + bbox.get("w", 0)) * w)
            y2 = int((bbox.get("y", 0) + bbox.get("h", 0)) * h)

            color = colors.get(det.get("class_name", ""), "#ffffff")
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)

            label_parts = [det.get("class_name", "")]
            if det.get("track_id") is not None:
                label_parts.append(f"#{det['track_id']}")
            label_parts.append(f"{det.get('confidence', 0):.0%}")
            if det.get("dwell_time_s") is not None:
                label_parts.append(f"{det['dwell_time_s']:.1f}s")
            label = " | ".join(label_parts)

            draw.rectangle([x1, max(0, y1 - 18), x1 + len(label) * 7, y1], fill=color)
            draw.text((x1 + 2, max(0, y1 - 16)), label, fill="black")

        img.save(output_path)
        return output_path
    except Exception:
        logger.exception("Failed to annotate frame")
        return None
