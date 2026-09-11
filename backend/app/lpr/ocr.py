# backend/app/lpr/ocr.py
"""License plate recognition using PaddleOCR with Indian plate validation."""
import logging
import re

logger = logging.getLogger(__name__)

# Indian vehicle plate formats:
# KL-01-AB-1234, KL01AB1234, KL 01 AB 1234
INDIAN_PLATE_PATTERN = re.compile(
    r"[A-Z]{2}[\s\-]?\d{2}[\s\-]?[A-Z]{1,3}[\s\-]?\d{4}",
    re.IGNORECASE,
)


def validate_indian_plate(text: str) -> str | None:
    """Extract and normalize an Indian vehicle plate number from OCR text."""
    match = INDIAN_PLATE_PATTERN.search(text.upper().strip())
    if not match:
        return None
    # Normalize: remove spaces/dashes, reformat as XX-00-XX-0000
    raw = re.sub(r"[\s\-]", "", match.group())
    if len(raw) < 9 or len(raw) > 11:
        return None
    # Format: XX-00-XX(X)-0000
    state = raw[:2]
    district = raw[2:4]
    series = raw[4:-4]
    number = raw[-4:]
    return f"{state}-{district}-{series}-{number}"


class LPREngine:
    """License plate recognition engine using PaddleOCR."""

    def __init__(self):
        self._ocr = None
        try:
            from paddleocr import PaddleOCR
            self._ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
            logger.info("PaddleOCR initialized")
        except ImportError:
            logger.warning("PaddleOCR not available — LPR will be disabled")

    def read_plate(self, image_path: str) -> dict | None:
        """Read plate from an image. Returns {plate_number, confidence, raw_texts}."""
        if not self._ocr:
            return None

        try:
            results = self._ocr.ocr(image_path, cls=True)
            if not results or not results[0]:
                return None

            texts = []
            for line in results[0]:
                text = line[1][0]
                conf = line[1][1]
                texts.append({"text": text, "confidence": conf})

            # Try to find a valid Indian plate
            all_text = " ".join(t["text"] for t in texts)
            plate = validate_indian_plate(all_text)

            if plate:
                avg_conf = sum(t["confidence"] for t in texts) / len(texts)
                return {
                    "plate_number": plate,
                    "confidence": round(avg_conf, 3),
                    "raw_texts": texts,
                }
            return None
        except Exception:
            logger.exception("LPR failed for %s", image_path)
            return None
