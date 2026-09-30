"""In-memory subject gallery using the feature's auto / review / new decision."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import facelab  # noqa: F401
from app.subjects.matcher import decide


def subject_code(code_num: int) -> str:
    return f"S-{code_num:03d}"


@dataclass
class Subject:
    code_num: int
    embeddings: list[np.ndarray] = field(default_factory=list)
    qualities: list[float] = field(default_factory=list)
    incident_count: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0

    @property
    def code(self) -> str:
        return subject_code(self.code_num)


@dataclass(frozen=True)
class EnrollResult:
    kind: str                  # "new" | "auto" | "review"
    code: str
    incident_count: int
    similarity: float | None   # best match score against existing subjects
    candidate_code: str | None  # for "review": the subject it may be


class Gallery:
    def __init__(self, match_auto: float = 0.55, match_review: float = 0.40, max_faces: int = 10):
        self.match_auto = match_auto
        self.match_review = match_review
        self.max_faces = max_faces
        self.subjects: dict[int, Subject] = {}

    def best_scores(self, embeddings: list[np.ndarray]) -> dict[int, float]:
        """Per subject: best cosine similarity over (new face x stored face) pairs."""
        scores: dict[int, float] = {}
        for code_num, subject in self.subjects.items():
            if not subject.embeddings:
                continue
            stored = np.stack(subject.embeddings)
            best = max(float(np.max(stored @ np.asarray(e, dtype=np.float32))) for e in embeddings)
            scores[code_num] = best
        return scores

    def enroll(self, samples: list[tuple[float, np.ndarray]], when: float) -> EnrollResult:
        """Match one visit's faces (quality, embedding) and record the incident."""
        if not samples:
            raise ValueError("enroll needs at least one face sample")
        embeddings = [e for _, e in samples]
        decision = decide(self.best_scores(embeddings), self.match_auto, self.match_review)
        candidate_code = None
        if decision.kind == "auto":
            subject = self.subjects[decision.subject_id]
        else:
            if decision.kind == "review":
                candidate_code = self.subjects[decision.subject_id].code
            subject = Subject(code_num=max(self.subjects, default=0) + 1, first_seen=when)
            self.subjects[subject.code_num] = subject
        subject.incident_count += 1
        subject.last_seen = when
        for quality, embedding in samples:
            subject.embeddings.append(np.asarray(embedding, dtype=np.float32))
            subject.qualities.append(float(quality))
        self._cap(subject)
        return EnrollResult(decision.kind, subject.code, subject.incident_count, decision.score, candidate_code)

    def _cap(self, subject: Subject) -> None:
        order = sorted(range(len(subject.qualities)), key=lambda i: -subject.qualities[i])[: self.max_faces]
        subject.embeddings = [subject.embeddings[i] for i in order]
        subject.qualities = [subject.qualities[i] for i in order]

    # ── persistence (optional --gallery DIR) ──

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        meta = []
        arrays = {}
        for s in self.subjects.values():
            meta.append({
                "code_num": s.code_num, "qualities": s.qualities, "incident_count": s.incident_count,
                "first_seen": s.first_seen, "last_seen": s.last_seen,
            })
            if s.embeddings:
                arrays[f"s{s.code_num}"] = np.stack(s.embeddings)
        (directory / "gallery.json").write_text(json.dumps(meta, indent=2))
        np.savez(directory / "gallery.npz", **arrays)

    @classmethod
    def load(cls, directory: Path, **kwargs) -> "Gallery":
        gallery = cls(**kwargs)
        meta_path, arr_path = directory / "gallery.json", directory / "gallery.npz"
        if not meta_path.exists():
            return gallery
        arrays = np.load(arr_path) if arr_path.exists() else {}
        for m in json.loads(meta_path.read_text()):
            key = f"s{m['code_num']}"
            embeddings = list(arrays[key]) if key in arrays else []
            gallery.subjects[m["code_num"]] = Subject(
                code_num=m["code_num"], embeddings=embeddings, qualities=list(m["qualities"]),
                incident_count=m["incident_count"], first_seen=m["first_seen"], last_seen=m["last_seen"],
            )
        return gallery
