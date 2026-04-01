# backend/app/logs/collector.py
"""Structured log collector for all services."""
import logging
import time
import uuid

logger = logging.getLogger(__name__)


def create_log_entry(
    service: str,
    level: str,
    message: str,
    metadata: dict | None = None,
) -> dict:
    """Create a structured log entry dict for Kafka/DB."""
    return {
        "id": str(uuid.uuid4()),
        "service": service,
        "level": level,
        "message": message,
        "metadata": metadata or {},
        "timestamp": time.time(),
    }
