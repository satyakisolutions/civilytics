import logging

import orjson
from confluent_kafka import Producer

logger = logging.getLogger(__name__)


class PipelineKafkaProducer:
    def __init__(self) -> None:
        from app.config import pipeline_settings

        self._producer = Producer(
            {
                "bootstrap.servers": pipeline_settings.kafka_bootstrap_servers,
                "linger.ms": 5,
                "batch.num.messages": 100,
            }
        )

    def send_metadata(self, camera_id: str, metadata: dict) -> None:
        try:
            self._producer.produce(
                topic="wastewatch.metadata",
                key=camera_id.encode("utf-8"),
                value=orjson.dumps(metadata),
            )
        except Exception:
            logger.exception("Failed to send metadata for %s", camera_id)

    def send_event(self, event) -> None:
        """Publish an FSM state change event to wastewatch.events."""
        try:
            self._producer.produce(
                topic="wastewatch.events",
                key=event.camera_id.encode("utf-8"),
                value=orjson.dumps({
                    "camera_id": event.camera_id,
                    "track_id": event.track_id,
                    "state": event.state.value,
                    "prev_state": event.prev_state.value,
                    "dwell_time_s": round(event.dwell_time_s, 1),
                    "class_name": event.class_name,
                    "bbox": event.bbox,
                    "timestamp": event.timestamp,
                }),
            )
        except Exception:
            logger.exception("Failed to send event for track %d", event.track_id)

    def flush(self) -> None:
        self._producer.flush(timeout=5)

    def poll(self) -> None:
        self._producer.poll(0)
