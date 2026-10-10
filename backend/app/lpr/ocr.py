# backend/app/lpr/ocr.py
"""License plate recognition using fast-plate-ocr with Indian plate validation."""
import logging
import re

logger = logging.getLogger(__name__)

INDIAN_PLATE_PATTERN = re.compile(
    r"[A-Z]{2}[\s\-]?\d{2}[\s\-]?[A-Z]{1,3}[\s\-]?\d{4}",
    re.IGNORECASE,
)


def validate_indian_plate(text: str) -> str | None:
    """Extract and normalize an Indian vehicle plate number from OCR text."""
    match = INDIAN_PLATE_PATTERN.search(text.upper().strip())
    if not match:
        return None
    raw = re.sub(r"[\s\-]", "", match.group())
    if len(raw) < 9 or len(raw) > 11:
        return None
    state = raw[:2]
    district = raw[2:4]
    series = raw[4:-4]
    number = raw[-4:]
    return f"{state}-{district}-{series}-{number}"


_recognizer = None


def _get_recognizer():
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    try:
        from fast_plate_ocr import LicensePlateRecognizer
        _recognizer = LicensePlateRecognizer("cct-s-v2-global-model")
        logger.info("fast-plate-ocr initialized (cct-s-v2-global-model)")
        return _recognizer
    except Exception as e:
        logger.warning("fast-plate-ocr unavailable: %s", e)
        return None


class LPREngine:
    """License plate recognition engine using fast-plate-ocr."""

    def __init__(self):
        self._recognizer = _get_recognizer()

    def read_plate(self, image_path: str) -> dict | None:
        """Read plate from a cropped plate image. Returns {plate_number, confidence, raw_texts}."""
        if self._recognizer is None:
            self._recognizer = _get_recognizer()
        if self._recognizer is None:
            return None

        try:
            predictions = self._recognizer.run(image_path)
            if not predictions:
                return None

            raw_texts = []
            for pred in predictions:
                if isinstance(pred, tuple):
                    text, conf = pred[0], pred[1] if len(pred) > 1 else 0.0
                else:
                    text, conf = str(pred), 0.0
                raw_texts.append({"text": text, "confidence": float(conf)})

            all_text = " ".join(t["text"] for t in raw_texts)
            plate = validate_indian_plate(all_text)

            if plate:
                avg_conf = sum(t["confidence"] for t in raw_texts) / len(raw_texts) if raw_texts else 0.0
                return {
                    "plate_number": plate,
                    "confidence": round(avg_conf, 3),
                    "raw_texts": raw_texts,
                }
            return None
        except Exception:
            logger.exception("LPR failed for %s", image_path)
            return None
