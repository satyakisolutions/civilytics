"""Qdrant index of subject face embeddings (collection offender_faces).

An index only: every vector also lives in subject_faces.embedding, so the
collection can be rebuilt from Postgres (SubjectService.resync_index).
"""
from __future__ import annotations

import uuid
from typing import Sequence

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.subjects.matcher import Hit


class FaceVectorStore:
    def __init__(self, client: QdrantClient, collection: str = "offender_faces", dim: int = 512):
        self._client = client
        self._collection = collection
        self._dim = dim

    def ensure_collection(self) -> None:
        if not self._client.collection_exists(self._collection):
            self._client.create_collection(
                self._collection,
                vectors_config=qm.VectorParams(size=self._dim, distance=qm.Distance.COSINE),
            )

    def upsert(self, face_id: uuid.UUID, vector: Sequence[float], subject_id: uuid.UUID, incident_id: uuid.UUID) -> None:
        self._client.upsert(
            self._collection,
            points=[
                qm.PointStruct(
                    id=str(face_id),
                    vector=[float(x) for x in vector],
                    payload={"subject_id": str(subject_id), "incident_id": str(incident_id)},
                )
            ],
        )

    def search(self, vector: Sequence[float], limit: int = 20) -> list[Hit]:
        result = self._client.query_points(
            self._collection, query=[float(x) for x in vector], limit=limit, with_payload=True
        )
        return [
            Hit(
                subject_id=uuid.UUID(point.payload["subject_id"]),
                face_id=uuid.UUID(str(point.id)),
                score=float(point.score),
            )
            for point in result.points
        ]

    def delete(self, face_ids: list[uuid.UUID]) -> None:
        if not face_ids:
            return
        self._client.delete(
            self._collection,
            points_selector=qm.PointIdsList(points=[str(f) for f in face_ids]),
        )

    def all_point_ids(self, page_size: int = 1000) -> list[uuid.UUID]:
        """Every point id in the collection (ids only, paginated scroll)."""
        ids: list[uuid.UUID] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                self._collection, limit=page_size, offset=offset, with_payload=False, with_vectors=False,
            )
            ids.extend(uuid.UUID(str(point.id)) for point in points)
            if offset is None:
                return ids
