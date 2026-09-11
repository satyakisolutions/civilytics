"""Tests for Kafka worker lifecycle and message handling (mocked Kafka)."""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ── MetadataConsumer ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metadata_consumer_start_stop():
    from app.services.kafka_consumer import MetadataConsumer
    with patch("app.services.kafka_consumer.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        mc = MetadataConsumer("localhost:9092")
        await mc.start()
        assert mc._consumer is not None
        await mc.stop()
        mock_consumer.stop.assert_called_once()


@pytest.mark.asyncio
async def test_event_consumer_start_stop():
    from app.services.event_consumer import EventConsumer
    with patch("app.services.event_consumer.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        ec = EventConsumer("localhost:9092")
        await ec.start()
        assert ec._consumer is not None
        await ec.stop()
        mock_consumer.stop.assert_called_once()


# ── VLM Worker ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vlm_worker_start_stop():
    from app.vlm.worker import VLMWorker
    with patch("app.vlm.worker.AIOKafkaConsumer") as MockKafkaC, \
         patch("app.vlm.worker.AIOKafkaProducer") as MockKafkaP, \
         patch("app.vlm.worker.VLMClient") as MockClient:
        MockClient.return_value = AsyncMock()
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafkaC.return_value = mock_consumer
        mock_producer = AsyncMock()
        MockKafkaP.return_value = mock_producer

        w = VLMWorker("localhost:9092")
        await w.start()
        await w.stop()
        mock_consumer.stop.assert_called_once()


# ── TTS Worker ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tts_worker_start_stop():
    from app.tts.worker import TTSWorker
    with patch("app.tts.worker.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        w = TTSWorker("localhost:9092")
        await w.start()
        await w.stop()
        mock_consumer.stop.assert_called_once()


# ── LPR Worker ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lpr_worker_start_stop():
    from app.lpr.worker import LPRWorker
    with patch("app.lpr.worker.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        w = LPRWorker("localhost:9092")
        await w.start()
        await w.stop()
        mock_consumer.stop.assert_called_once()


# ── Log Worker ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_worker_start_stop():
    from app.logs.worker import LogWorker
    with patch("app.logs.worker.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        w = LogWorker("localhost:9092")
        await w.start()
        await w.stop()
        mock_consumer.stop.assert_called_once()


# ── Alert Dispatcher ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_dispatcher_start_stop():
    from app.alerts.dispatcher import AlertDispatcher
    with patch("app.alerts.dispatcher.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        d = AlertDispatcher("localhost:9092")
        await d.start()
        await d.stop()
        mock_consumer.stop.assert_called_once()


# ── Evidence Worker ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evidence_worker_start_stop():
    from app.evidence.worker import EvidenceWorker
    with patch("app.evidence.worker.AIOKafkaConsumer") as MockKafka:
        mock_consumer = AsyncMock()
        mock_consumer.__aiter__ = MagicMock(return_value=iter([]))
        MockKafka.return_value = mock_consumer

        w = EvidenceWorker("localhost:9092")
        await w.start()
        await w.stop()
