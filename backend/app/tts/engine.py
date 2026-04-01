# backend/app/tts/engine.py
"""Piper TTS engine wrapper for generating warning audio."""
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("/tmp/wastewatch/tts")


class TTSEngine:
    """Wraps Piper TTS (ONNX) for WAV generation. Falls back to placeholder if Piper unavailable."""

    def __init__(self):
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self._available = False
        try:
            import piper
            self._available = True
        except ImportError:
            logger.warning("Piper TTS not available — TTS will generate placeholder files")

    def generate(self, text: str) -> dict:
        """Generate WAV audio from text. Returns {path, text_hash, duration_ms}."""
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        filename = f"tts_{text_hash}.wav"
        filepath = AUDIO_DIR / filename

        if filepath.exists():
            return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._estimate_duration(text)}

        if self._available:
            self._generate_piper(text, filepath)
        else:
            self._generate_placeholder(text, filepath)

        return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._estimate_duration(text)}

    def _generate_piper(self, text: str, filepath: Path):
        """Generate using Piper TTS."""
        import subprocess
        try:
            subprocess.run(
                ["piper", "--model", "en_US-lessac-medium", "--output_file", str(filepath)],
                input=text.encode(), check=True, capture_output=True, timeout=30,
            )
        except Exception:
            logger.exception("Piper TTS generation failed")
            self._generate_placeholder(text, filepath)

    def _generate_placeholder(self, text: str, filepath: Path):
        """Generate a minimal WAV placeholder (silence)."""
        import struct
        sample_rate = 22050
        duration_s = max(1, len(text.split()) * 0.3)
        num_samples = int(sample_rate * duration_s)
        with open(filepath, "wb") as f:
            data_size = num_samples * 2
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + data_size))
            f.write(b"WAVEfmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
            f.write(b"data")
            f.write(struct.pack("<I", data_size))
            f.write(b"\x00" * data_size)

    def _estimate_duration(self, text: str) -> int:
        words = len(text.split())
        return int(words * 300)  # ~300ms per word
