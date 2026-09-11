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
# Fixed deterrent phrase spoken on every aggressive-tone alert -- short,
# direct command aimed at whoever is dumping right now, not a description
# of the event (that's what the dashboard/Telegram text alert is for).
#
# Kannada, not synthesized live: Piper has no Kannada voice at all (checked
# the official voice list -- kn/kn_IN doesn't exist, unlike hi_IN/ml_IN/
# ne_NP which do). Since this phrase is always the same fixed string, a
# one-time pre-generated static audio file is used instead of live TTS --
# see _FIXED_DETERRENT_AUDIO below.
TTS_FIXED_DETERRENT = "ಎಚ್ಚರಿಕೆ! ಕಸ ಎಸೆಯಬೇಡಿ. ನಿಮ್ಮ ಚಟುವಟಿಕೆ ಕ್ಯಾಮೆರಾದಲ್ಲಿ ದಾಖಲಾಗುತ್ತಿದೆ."
_FIXED_DETERRENT_AUDIO = Path(__file__).parent / "assets" / "kannada_deterrent.wav"


def _apply_tone(text: str) -> str:
    """Shape text for the active tone. Aggressive mode ignores the passed-in
    alert text entirely and always speaks the same short, direct command --
    a fixed deterrent phrase reads more like an authoritative warning than
    reading out per-event details (track IDs, dwell seconds) aloud."""
    if TTS_TONE == "aggressive":
        return TTS_FIXED_DETERRENT
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
        # Extract model name: /tmp/piper_voices/en_US-ryan-high.onnx → en_US-ryan-high
        model_name = model_path.name.replace(".onnx", "")
        # Parse Piper's own naming convention ({lang}_{REGION}-{voice}-{quality})
        # to build the HuggingFace path instead of a hardcoded "lessac/medium"
        # directory -- that was hardcoded for the lessac voice specifically,
        # so any other WW_TTS_VOICE (this deployment uses ryan-high, which
        # lives under a completely different en/en_US/ryan/high/ path) 404'd
        # on every download attempt and silently fell back to the
        # placeholder generator (pure silence, no speech at all) forever.
        try:
            _lang_region, _rest = model_name.split("-", 1)
            _voice, _quality = _rest.rsplit("-", 1)
            _lang = _lang_region.split("_")[0]
        except ValueError:
            logger.error("Could not parse voice name %r — expected {lang}_{REGION}-{voice}-{quality}", model_name)
            self._available = False
            return
        base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{_lang}/{_lang_region}/{_voice}/{_quality}"
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

        if shaped == TTS_FIXED_DETERRENT and _FIXED_DETERRENT_AUDIO.exists():
            # Static pre-generated Kannada audio -- see TTS_FIXED_DETERRENT
            # comment. Never invoke Piper (no Kannada voice) or the silent
            # placeholder for this phrase.
            import shutil
            shutil.copyfile(_FIXED_DETERRENT_AUDIO, filepath)
            return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._audio_duration_ms(filepath)}

        if self._available:
            self._generate_piper(shaped, filepath)
        else:
            self._generate_placeholder(shaped, filepath)

        return {"path": str(filepath), "text_hash": text_hash, "duration_ms": self._estimate_duration(shaped)}

    def _audio_duration_ms(self, filepath: Path) -> int:
        """Read the real duration from a WAV file's header instead of the
        word-count estimate (that heuristic assumes space-separated Latin
        words -- meaningless for Kannada script)."""
        import wave
        try:
            with wave.open(str(filepath), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return int(frames / float(rate) * 1000)
        except Exception:
            logger.exception("Failed to read WAV duration for %s", filepath)
            return 7000

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
