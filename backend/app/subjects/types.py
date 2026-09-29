"""Plain data passed between the worker, analyzer and service. No cv2/onnx imports."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AnalyzedFace:
    quality: float
    embedding: np.ndarray  # (512,) float32, L2-normalised
    face_jpeg: bytes       # face thumbnail stored for operator review


@dataclass(frozen=True)
class FaceRequest:
    incident_id: uuid.UUID
    camera_id: str
    detected_at: datetime  # aware UTC
    crops: tuple[str, ...]
    person_track_id: int | None = None

    @classmethod
    def from_payload(cls, payload: dict, incoming_dir: Path) -> "FaceRequest":
        """Parse a wastewatch.faces.requests message.

        Only crop paths inside incoming_dir/<incident_id>/ are kept -- anything
        else (another incident's folder, ../ traversal, absolute paths
        elsewhere) is dropped, so a bad message can never make the worker
        read, move or delete files it does not own.
        """
        incident_id = uuid.UUID(str(payload["incident_id"]))
        own_dir = (Path(incoming_dir) / str(incident_id)).resolve()
        crops = []
        for raw in payload.get("crops") or []:
            path = Path(str(raw)).resolve()
            if path.parent == own_dir:
                crops.append(str(path))
        ts = payload.get("detected_at")
        detected_at = (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            if isinstance(ts, (int, float))
            else datetime.now(timezone.utc)
        )
        track = payload.get("person_track_id")
        return cls(
            incident_id=incident_id,
            camera_id=str(payload.get("camera_id") or "unknown"),
            detected_at=detected_at,
            crops=tuple(crops),
            person_track_id=int(track) if track is not None else None,
        )
