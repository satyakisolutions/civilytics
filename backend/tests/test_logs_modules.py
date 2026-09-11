"""Tests for logs: collector.py and qdrant_store.py."""
import pytest
from unittest.mock import patch, MagicMock
from app.logs.collector import create_log_entry


# ── collector.py ────────────────────────────────────────────────────

def test_create_log_entry_structure():
    entry = create_log_entry("api", "info", "Server started")
    assert entry["service"] == "api"
    assert entry["level"] == "info"
    assert entry["message"] == "Server started"
    assert "id" in entry
    assert "timestamp" in entry
    assert isinstance(entry["metadata"], dict)

def test_create_log_entry_default_metadata():
    entry = create_log_entry("pipeline", "error", "crash")
    assert entry["metadata"] == {}

def test_create_log_entry_custom_metadata():
    entry = create_log_entry("vlm", "warning", "slow", metadata={"latency_ms": 5000})
    assert entry["metadata"]["latency_ms"] == 5000

def test_create_log_entry_unique_ids():
    e1 = create_log_entry("a", "info", "msg1")
    e2 = create_log_entry("a", "info", "msg2")
    assert e1["id"] != e2["id"]

def test_create_log_entry_timestamp_is_float():
    entry = create_log_entry("svc", "debug", "test")
    assert isinstance(entry["timestamp"], float)


# ── qdrant_store.py ─────────────────────────────────────────────────

def test_qdrant_store_embed_no_embedder():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._client = None
    store._embedder = None
    assert store.embed("test") is None

@pytest.mark.asyncio
async def test_qdrant_store_store_disabled():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._client = None
    store._embedder = None
    await store.store({"id": "1", "message": "test", "service": "api", "level": "info", "timestamp": 1.0})
    # Should not raise

@pytest.mark.asyncio
async def test_qdrant_store_search_disabled():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._client = None
    store._embedder = None
    results = await store.search("error logs")
    assert results == []

@pytest.mark.asyncio
async def test_qdrant_store_search_with_mock():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._client = MagicMock()
    store._embedder = MagicMock()
    store._collection = "test_logs"

    store._embedder.encode.return_value = MagicMock(tolist=MagicMock(return_value=[0.1] * 384))

    mock_result = MagicMock()
    mock_result.id = "id1"
    mock_result.score = 0.95
    mock_result.payload = {"service": "api", "level": "error", "message": "crash", "timestamp": 1.0}
    store._client.search.return_value = [mock_result]

    results = await store.search("crash")
    assert len(results) == 1
    assert results[0]["score"] == 0.95
    assert results[0]["message"] == "crash"

@pytest.mark.asyncio
async def test_qdrant_store_search_with_service_filter():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._client = MagicMock()
    store._embedder = MagicMock()
    store._collection = "test_logs"

    store._embedder.encode.return_value = MagicMock(tolist=MagicMock(return_value=[0.1] * 384))
    store._client.search.return_value = []

    # Mock qdrant_client.models at module-import level inside the method
    mock_filter = MagicMock()
    mock_fc = MagicMock()
    mock_mv = MagicMock()
    with patch.dict("sys.modules", {
        "qdrant_client.models": MagicMock(Filter=mock_filter, FieldCondition=mock_fc, MatchValue=mock_mv),
    }):
        results = await store.search("error", service="pipeline")

    store._client.search.assert_called_once()
    assert results == []

def test_qdrant_store_embed_with_mock():
    from app.logs.qdrant_store import QdrantLogStore
    store = QdrantLogStore.__new__(QdrantLogStore)
    store._embedder = MagicMock()
    store._embedder.encode.return_value = MagicMock(tolist=MagicMock(return_value=[0.5] * 384))
    result = store.embed("test text")
    assert len(result) == 384
