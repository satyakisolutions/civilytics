"""Settings for face-based subject IDs (spec §12). Read from WW_* env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from typing import Mapping

ENV_VARS: dict[str, str] = {
    "min_det": "WW_FACE_MIN_DET",
    "min_px": "WW_FACE_MIN_PX",
    "min_sharpness": "WW_FACE_MIN_SHARPNESS",
    "max_yaw": "WW_FACE_MAX_YAW",
    "match_auto": "WW_FACE_MATCH_AUTO",
    "match_review": "WW_FACE_MATCH_REVIEW",
    "max_faces": "WW_SUBJECT_MAX_FACES",
    "keep_per_incident": "WW_FACE_KEEP_PER_INCIDENT",
    "retention_days": "WW_SUBJECT_RETENTION_DAYS",
    "incoming_retention_days": "WW_FACE_INCOMING_RETENTION_DAYS",
    "det_model": "WW_FACE_DET_MODEL",
    "rec_model": "WW_FACE_REC_MODEL",
    "faces_dir": "WW_FACES_PRIVATE_DIR",
    "qdrant_url": "WW_QDRANT_URL",
    "collection": "WW_FACE_COLLECTION",
}


@dataclass(frozen=True)
class SubjectSettings:
    min_det: float = 0.6
    min_px: float = 40.0
    min_sharpness: float = 60.0
    max_yaw: float = 0.45
    match_auto: float = 0.55
    match_review: float = 0.40
    max_faces: int = 10
    keep_per_incident: int = 3
    retention_days: int = 90
    incoming_retention_days: int = 7
    det_model: str = "/app/models/face/det_10g.onnx"
    rec_model: str = "/app/models/face/w600k_r50.onnx"
    faces_dir: str = "/app/faces_private"
    qdrant_url: str = "http://qdrant:6333"
    collection: str = "offender_faces"

    def __post_init__(self) -> None:
        if not (0.0 < self.match_review <= self.match_auto <= 1.0):
            raise ValueError(
                f"Need 0 < WW_FACE_MATCH_REVIEW ({self.match_review}) <= "
                f"WW_FACE_MATCH_AUTO ({self.match_auto}) <= 1"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "SubjectSettings":
        env = os.environ if env is None else env
        kwargs = {}
        for f in fields(cls):
            raw = env.get(ENV_VARS[f.name])
            if raw is not None:
                kwargs[f.name] = type(f.default)(raw)
        return cls(**kwargs)
