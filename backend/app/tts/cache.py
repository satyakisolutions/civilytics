# backend/app/tts/cache.py
"""TTS audio cache backed by database."""
import hashlib
import logging

logger = logging.getLogger(__name__)


class TTSCache:
    """In-memory + DB-backed TTS cache. Hash text → audio path."""

    def __init__(self):
        self._memory: dict[str, dict] = {}  # text_hash -> {path, duration_ms, text}

    def get(self, text: str) -> dict | None:
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        return self._memory.get(text_hash)

    def put(self, text: str, path: str, duration_ms: int):
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        self._memory[text_hash] = {"path": path, "duration_ms": duration_ms, "text": text, "text_hash": text_hash}

    def has(self, text: str) -> bool:
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        return text_hash in self._memory
