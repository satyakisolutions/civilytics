"""Per-model health and latency tracking."""
import logging
import time

logger = logging.getLogger(__name__)


class ModelHealthTracker:
    """Track model inference latency and health."""

    def __init__(self):
        self._models: dict[str, dict] = {
            "rfdetr": {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0},
            "bytetrack": {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0},
            "qwen2.5-vl": {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0},
            "piper-tts": {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0},
            "paddleocr": {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0},
        }

    def record_latency(self, model_id: str, latency_ms: float):
        if model_id not in self._models:
            self._models[model_id] = {"status": "unknown", "last_latency_ms": 0, "avg_latency_ms": 0, "call_count": 0}
        m = self._models[model_id]
        m["last_latency_ms"] = latency_ms
        m["call_count"] += 1
        m["avg_latency_ms"] = round(
            (m["avg_latency_ms"] * (m["call_count"] - 1) + latency_ms) / m["call_count"], 1
        )
        m["status"] = "healthy"

    def get_all(self) -> dict[str, dict]:
        return dict(self._models)

    def set_status(self, model_id: str, status: str):
        if model_id in self._models:
            self._models[model_id]["status"] = status


# Module-level singleton
model_health = ModelHealthTracker()
