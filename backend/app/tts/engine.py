# backend/app/tts/engine.py
"""Piper TTS engine wrapper for generating warning audio.

Tone is configurable via environment so the same engine serves both
"neutral" announcements and "aggressive" deterrent broadcasts that go
out over the SIP / IP-speaker loop at Lake Shore Gardens. Aggressive
mode uses a forceful voice model, faster cadence, and an all-caps
warning prefix so the output sounds like a command, not a notification.
"""
import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_DIR = Path("/tmp/wastewatch/tts")

# Configuration — always aggressive tone for deterrent scolding
TTS_TONE = "aggressive"
_VOICE_DIR = "/tmp/piper_voices"
TTS_VOICE = os.environ.get(
    "WW_TTS_VOICE",
    f"{_VOICE_DIR}/en_US-lessac-medium.onnx",
)
# Fast cadence — sounds commanding, not conversational
TTS_LENGTH_SCALE = float(os.environ.get("WW_TTS_LENGTH_SCALE", "0.78"))
TTS_NOISE_SCALE = float(os.environ.get("WW_TTS_NOISE_SCALE", "0.667"))
# Short pauses between sentences — keeps pressure on
TTS_SENTENCE_SILENCE = float(os.environ.get("WW_TTS_SENTENCE_SILENCE", "0.12"))
# Aggressive prefix/suffix wraps every message
TTS_TONE_PREFIX = "ATTENTION. WARNING. "
TTS_TONE_SUFFIX = " STOP IMMEDIATELY. THIS AREA IS UNDER ACTIVE SURVEILLANCE. YOU ARE BEING RECORDED."


def _apply_tone(text: str) -> str:
    """Shape text for the active tone. Aggressive mode UPPER-CASES the body
    and brackets it with a hard warning prefix + suffix so Piper renders it
    with more emphasis and the listener hears it as a command."""
    if TTS_TONE == "aggressive":
        body = text.strip().rstrip(".!?") + "."
        return f"{TTS_TONE_PREFIX}{body.upper()}{TTS_TONE_SUFFIX}".strip()
    return text


class TTSEngine:
    """Wraps Piper TTS (ONNX) for WAV generation. Falls back to placeholder if Piper unavailable."""

    def __init__(self):
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        self._available = False
        try:
            import piper  # noqa: F401
            self._available = True
            # Auto-download voice model if not present
            self._ensure_voice_model()
        except ImportError:
            logger.warning("Piper TTS not available — TTS will generate placeholder files")

    def _ensure_voice_model(self):
        """Check voice model exists. Download from HuggingFace if missing."""
        model_path = Path(TTS_VOICE)
        if model_path.exists():
            logger.info("Piper voice model found: %s", model_path)
            return
        # Try to download
        import urllib.request
        model_dir = model_path.parent
        model_dir.mkdir(parents=True, exist_ok=True)
        # Extract model name: /tmp/piper_voices/en_US-lessac-medium.onnx → en_US-lessac-medium
        model_name = model_path.name.replace(".onnx", "")
        base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium"
        for fname in [f"{model_name}.onnx", f"{model_name}.onnx.json"]:
            fpath = model_dir / fname
            if fpath.exists():
                continue
            url = f"{base_url}/{fname}"
            logger.info("Downloading Piper voice: %s", url)
            try:
                urllib.request.urlretrieve(url, str(fpath))
                logger.info("Downloaded: %s (%.1f MB)", fpath, fpath.stat().st_size / 1e6)
            except Exception:
                logger.exception("Failed to download voice model %s", url)
                self._available = False
        logger.info(
            "TTS engine initialised (tone=%s voice=%s length_scale=%.2f)",
            TTS_TONE, TTS_VOICE, TTS_LENGTH_SCALE,
        )

    def generate(self, text: str) -> dict:
        """Generate WAV audio from text. Returns {path, text_hash, duration_ms}."""
        shaped = _apply_tone(text)
        text_hash = hashlib.sha256(shaped.encode()).hexdigest()[:16]
        filename = f"tts_{text_hash}.wav"
        filepath = AUDIO_DIR / filename

        if filepath.exists():
            return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._estimate_duration(shaped)}

        if self._available:
            self._generate_piper(shaped, filepath)
        else:
            self._generate_placeholder(shaped, filepath)

        return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._estimate_duration(shaped)}

    def _generate_piper(self, text: str, filepath: Path):
        """Generate using Piper TTS with tone-shaped prosody."""
        import subprocess
        try:
            subprocess.run(
                [
                    "piper",
                    "--model", TTS_VOICE,
                    "--length_scale", str(TTS_LENGTH_SCALE),
                    "--noise_scale", str(TTS_NOISE_SCALE),
                    "--sentence_silence", str(TTS_SENTENCE_SILENCE),
                    "--output_file", str(filepath),
                ],
                input=text.encode(), check=True, capture_output=True, timeout=30,
            )
        except Exception:
            logger.exception("Piper TTS generation failed — falling back to placeholder")
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
