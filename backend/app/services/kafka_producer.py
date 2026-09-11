import orjson
from aiokafka import AIOKafkaProducer
from app.config.kafka import TOPIC_METADATA
from app.schemas.metadata import FrameMetadata


def _encode_key(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


class KafkaProducerService:
    def __init__(self, bootstrap_servers: str):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: orjson.dumps(v),
        )

    async def start(self):
        await self._producer.start()

    async def stop(self):
        await self._producer.stop()

    async def send_metadata(self, metadata: FrameMetadata):
        await self._producer.send_and_wait(
            topic=TOPIC_METADATA,
            key=_encode_key(metadata.camera_id),
            value=metadata.model_dump(),
        )

    async def send(self, topic: str, key: str, value: dict):
        await self._producer.send_and_wait(topic=topic, key=_encode_key(key), value=value)
