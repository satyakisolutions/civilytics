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

    def flush(self) -> None:
        self._producer.flush(timeout=5)

    def poll(self) -> None:
        self._producer.poll(0)
