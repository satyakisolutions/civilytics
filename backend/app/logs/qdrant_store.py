# backend/app/logs/qdrant_store.py
"""Qdrant vector store for semantic log search."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


class QdrantLogStore:
    """Interface to Qdrant for storing and searching log embeddings.

    Falls back gracefully if Qdrant/sentence-transformers aren't available.
    """

    def __init__(self, url: str = "http://localhost:6333", collection: str = "wastewatch_logs"):
        self._url = url
        self._collection = collection
        self._client = None
        self._embedder = None

        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=url, timeout=10)
            logger.info("Qdrant client connected: %s", url)
        except ImportError:
            logger.warning("qdrant-client not installed — log vector search disabled")
        except Exception:
            logger.warning("Qdrant connection failed — log vector search disabled")

        try:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer("all-MiniLM-L6-v2")
            logger.info("Sentence transformer loaded for log embedding")
        except ImportError:
            logger.warning("sentence-transformers not installed — log embedding disabled")

    def embed(self, text: str) -> list[float] | None:
        if not self._embedder:
            return None
        return self._embedder.encode(text).tolist()

    async def store(self, log_entry: dict):
        """Store a log entry with its embedding in Qdrant."""
        if not self._client or not self._embedder:
            return
        try:
            embedding = self.embed(log_entry["message"])
            if not embedding:
                return
            from qdrant_client.models import PointStruct
            self._client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=log_entry["id"],
                        vector=embedding,
                        payload={
                            "service": log_entry["service"],
                            "level": log_entry["level"],
                            "message": log_entry["message"],
                            "timestamp": log_entry["timestamp"],
                            "metadata": log_entry.get("metadata", {}),
                        },
                    )
                ],
            )
        except Exception:
            logger.exception("Failed to store log in Qdrant")

    async def search(self, query: str, limit: int = 20, service: str | None = None) -> list[dict]:
        """Semantic search for logs. Returns matching log entries."""
        if not self._client or not self._embedder:
            return []
        try:
            embedding = self.embed(query)
            if not embedding:
                return []

            filter_conditions = None
            if service:
                from qdrant_client.models import Filter, FieldCondition, MatchValue
                filter_conditions = Filter(must=[
                    FieldCondition(key="service", match=MatchValue(value=service))
                ])

            results = self._client.search(
                collection_name=self._collection,
                query_vector=embedding,
                limit=limit,
                query_filter=filter_conditions,
            )
            return [
                {
                    "id": str(r.id),
                    "score": r.score,
                    "service": r.payload.get("service"),
                    "level": r.payload.get("level"),
                    "message": r.payload.get("message"),
                    "timestamp": r.payload.get("timestamp"),
                }
                for r in results
            ]
        except Exception:
            logger.exception("Qdrant search failed")
            return []
