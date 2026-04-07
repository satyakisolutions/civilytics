# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Kafka output for VLM summaries."""
from __future__ import annotations

import logging
import time

import orjson
from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class KafkaOutput:
    """Publishes VLM summaries to a Kafka topic.

    Uses confluent_kafka.Producer for low-latency, non-blocking writes.
    Each message is keyed by camera_id so that all summaries for a given
    camera land on the same partition (preserving order per camera).
    """

    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        self._topic = topic
        self._producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers,
                "linger.ms": 5,
                "batch.num.messages": 50,
            }
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_summary(self, camera_id: str, summary: dict) -> None:
        """Publish a JSON-encoded VLM summary to Kafka.

        Args:
            camera_id: Camera identifier (used as the message key).
            summary: VLM summary dict to serialize.
        """
        try:
            # Add a publish timestamp if not already present
            if "published_at" not in summary:
                summary["published_at"] = time.time()

            self._producer.produce(
                topic=self._topic,
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(summary),
                on_delivery=self._delivery_callback,
            )
            # Trigger any queued delivery callbacks without blocking
            self._producer.poll(0)
        except Exception:
            logger.exception("Failed to send VLM summary for %s", camera_id)

    def flush(self, timeout: float = 5.0) -> None:
        """Flush pending messages (blocks up to *timeout* seconds)."""
        self._producer.flush(timeout=timeout)

    def close(self) -> None:
        """Flush and release resources."""
        self.flush()
        logger.info("Kafka output closed (topic=%s)", self._topic)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _delivery_callback(err, msg) -> None:
        if err is not None:
            logger.error(
                "Kafka delivery failed (topic=%s, key=%s): %s",
                msg.topic(),
                msg.key(),
                err,
            )
