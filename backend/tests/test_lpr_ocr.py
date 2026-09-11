"""Tests for LPR OCR module."""
import pytest
from app.lpr.ocr import validate_indian_plate, LPREngine


# ── validate_indian_plate ───────────────────────────────────────────

class TestValidateIndianPlate:
    def test_standard_format(self):
        assert validate_indian_plate("KL-01-AB-1234") == "KL-01-AB-1234"

    def test_no_separators(self):
        assert validate_indian_plate("KL01AB1234") == "KL-01-AB-1234"

    def test_spaces(self):
        assert validate_indian_plate("KL 01 AB 1234") == "KL-01-AB-1234"

    def test_three_letter_series(self):
        result = validate_indian_plate("KA51ABC1234")
        assert result == "KA-51-ABC-1234"

    def test_case_insensitive(self):
        result = validate_indian_plate("kl01ab1234")
        assert result is not None
        assert result.startswith("KL")

    def test_invalid_no_match(self):
        assert validate_indian_plate("HELLO WORLD") is None
        assert validate_indian_plate("12345") is None
        assert validate_indian_plate("") is None

    def test_embedded_in_text(self):
        result = validate_indian_plate("some text KL01AB1234 more text")
        assert result == "KL-01-AB-1234"

    def test_too_short_after_normalize(self):
        assert validate_indian_plate("KL01A1") is None

    def test_mixed_separators(self):
        result = validate_indian_plate("KL-01 AB-1234")
        assert result == "KL-01-AB-1234"


# ── LPREngine ───────────────────────────────────────────────────────

def test_lpr_engine_no_paddleocr():
    """LPREngine should gracefully handle missing PaddleOCR."""
    engine = LPREngine()
    # PaddleOCR likely not installed in test env
    if engine._ocr is None:
        result = engine.read_plate("/fake/path.jpg")
        assert result is None
