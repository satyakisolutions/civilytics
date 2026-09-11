import pytest
from unittest.mock import AsyncMock, patch
from app.services.kafka_producer import KafkaProducerService
from app.schemas.metadata import FrameMetadata, Detection


@pytest.mark.asyncio
async def test_send_metadata():
    with patch("app.services.kafka_producer.AIOKafkaProducer") as MockProducer:
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance
        service = KafkaProducerService(bootstrap_servers="localhost:9092")
        await service.start()
        metadata = FrameMetadata(
            camera_id="cam-01",
            frame_number=100,
            timestamp=1711900000.0,
            detections=[
                Detection(class_id=0, class_name="person", confidence=0.92, bbox=[0.1, 0.2, 0.3, 0.4], track_id=1)
            ],
        )
        await service.send_metadata(metadata)
        mock_instance.send_and_wait.assert_called_once()
        call_args = mock_instance.send_and_wait.call_args
        assert call_args.kwargs["topic"] == "wastewatch.metadata"
        assert call_args.kwargs["key"] == b"cam-01"


@pytest.mark.asyncio
async def test_producer_start_stop():
    with patch("app.services.kafka_producer.AIOKafkaProducer") as MockProducer:
        mock_instance = AsyncMock()
        MockProducer.return_value = mock_instance
        service = KafkaProducerService(bootstrap_servers="localhost:9092")
        await service.start()
        mock_instance.start.assert_called_once()
        await service.stop()
        mock_instance.stop.assert_called_once()
