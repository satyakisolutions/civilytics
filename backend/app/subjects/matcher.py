"""Per-subject scoring and the auto / review / new decision (spec §6.2 steps 6-7).

Pure: no numpy/cv2, so the API process can import it cheaply.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable, Literal


@dataclass(frozen=True)
class Hit:
    subject_id: uuid.UUID
    face_id: uuid.UUID
    score: float


@dataclass(frozen=True)
class Decision:
    kind: Literal["auto", "review", "new"]
    subject_id: uuid.UUID | None
    score: float | None


def best_per_subject(hits: Iterable[Hit]) -> dict[uuid.UUID, float]:
    best: dict[uuid.UUID, float] = {}
    for hit in hits:
        if hit.score > best.get(hit.subject_id, float("-inf")):
            best[hit.subject_id] = hit.score
    return best


def decide(per_subject: dict[uuid.UUID, float], auto: float, review: float) -> Decision:
    if not per_subject:
        return Decision("new", None, None)
    subject_id, score = max(per_subject.items(), key=lambda kv: (kv[1], str(kv[0])))
    if score >= auto:
        return Decision("auto", subject_id, score)
    if score >= review:
        return Decision("review", subject_id, score)
    return Decision("new", None, score)


def faces_over_cap(faces: list[tuple[uuid.UUID, float]], cap: int) -> list[uuid.UUID]:
    ranked = sorted(faces, key=lambda f: (-f[1], str(f[0])))
    return [face_id for face_id, _ in ranked[cap:]]
