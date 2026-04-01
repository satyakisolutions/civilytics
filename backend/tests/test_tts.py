"""Tests for TTS engine and cache."""
import pytest
from unittest.mock import patch, MagicMock
from app.tts.cache import TTSCache


# ── TTSCache ────────────────────────────────────────────────────────

class TestTTSCache:
    def test_initially_empty(self):
        cache = TTSCache()
        assert cache.get("hello") is None
        assert cache.has("hello") is False

    def test_put_and_get(self):
        cache = TTSCache()
        cache.put("hello world", "/tmp/tts_abc.wav", 600)
        result = cache.get("hello world")
        assert result is not None
        assert result["path"] == "/tmp/tts_abc.wav"
        assert result["duration_ms"] == 600
        assert result["text"] == "hello world"

    def test_has(self):
        cache = TTSCache()
        cache.put("test", "/tmp/x.wav", 100)
        assert cache.has("test") is True
        assert cache.has("other") is False

    def test_consistent_hashing(self):
        cache = TTSCache()
        cache.put("same text", "/path.wav", 300)
        assert cache.get("same text") is not None

    def test_different_texts_different_keys(self):
        cache = TTSCache()
        cache.put("text a", "/a.wav", 100)
        cache.put("text b", "/b.wav", 200)
        assert cache.get("text a")["path"] == "/a.wav"
        assert cache.get("text b")["path"] == "/b.wav"


# ── TTSEngine ───────────────────────────────────────────────────────

def test_tts_engine_init_no_piper():
    with patch.dict("sys.modules", {"piper": None}):
        from app.tts.engine import TTSEngine
        engine = TTSEngine()
        assert engine._available is False

def test_tts_engine_estimate_duration():
    from app.tts.engine import TTSEngine
    engine = TTSEngine()
    assert engine._estimate_duration("hello world") == 600  # 2 words * 300ms

def test_tts_engine_estimate_duration_long():
    from app.tts.engine import TTSEngine
    engine = TTSEngine()
    assert engine._estimate_duration("a b c d e") == 1500  # 5 words

def test_tts_engine_generate_placeholder(tmp_path):
    from app.tts.engine import TTSEngine
    with patch("app.tts.engine.AUDIO_DIR", tmp_path):
        engine = TTSEngine()
        engine._available = False
        result = engine.generate("test text")
    assert result["text_hash"] is not None
    assert result["duration_ms"] == 600
    # Check file was created
    from pathlib import Path
    filepath = Path(result["path"])
    assert filepath.exists()
    # Check WAV header
    data = filepath.read_bytes()
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"

def test_tts_engine_cache_hit(tmp_path):
    from app.tts.engine import TTSEngine
    with patch("app.tts.engine.AUDIO_DIR", tmp_path):
        engine = TTSEngine()
        engine._available = False
        result1 = engine.generate("hello")
        result2 = engine.generate("hello")
    assert result1["text_hash"] == result2["text_hash"]
    assert result1["path"] == result2["path"]

def test_tts_engine_hash_consistency():
    import hashlib
    text = "consistency test"
    h1 = hashlib.sha256(text.encode()).hexdigest()[:16]
    h2 = hashlib.sha256(text.encode()).hexdigest()[:16]
    assert len(h1) == 16
    assert h1 == h2
