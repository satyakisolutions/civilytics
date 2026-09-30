"""Visits (one person's continuous presence in view) and when to match them.

Mirrors the feature's rule "one violation episode per person = one incident":
each visit is matched once, after collecting a few good faces; leaving view
for longer than `gone_after_s` and coming back starts a new visit, which is
what shows up as a repeat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from facelab.faces import FaceObs
from facelab.gallery import EnrollResult, Gallery


def iou(a: np.ndarray, b: np.ndarray) -> float:
    w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = w * h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / union) if union > 0 else 0.0


def _close(a: np.ndarray, b: np.ndarray) -> bool:
    """Centres within half a face width -- keeps fast-moving faces on the same visit."""
    ca = np.array([(a[0] + a[2]) / 2, (a[1] + a[3]) / 2])
    cb = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
    size = max(a[2] - a[0], b[2] - b[0])
    return float(np.linalg.norm(ca - cb)) <= 0.5 * size


@dataclass
class Visit:
    visit_id: int
    bbox: np.ndarray
    first_seen: float
    last_seen: float
    obs: FaceObs | None = None         # face in the latest processed frame (None if not seen)
    samples: list[tuple[float, np.ndarray, np.ndarray]] = field(default_factory=list)  # (quality, embedding, aligned)
    first_good_at: float | None = None
    status: str = "collecting"         # collecting | new | repeat | review | same | no_face
    result: EnrollResult | None = None

    @property
    def decided(self) -> bool:
        return self.status != "collecting"

    def add(self, obs: FaceObs, now: float, keep: int = 5) -> None:
        self.bbox, self.last_seen, self.obs = obs.bbox, now, obs
        if not obs.passed:
            return
        if self.first_good_at is None:
            self.first_good_at = now
        self.samples.append((obs.quality, obs.embedding, obs.aligned))
        self.samples.sort(key=lambda s: -s[0])
        del self.samples[keep:]


class Session:
    def __init__(
        self,
        gallery: Gallery,
        need_samples: int = 3,
        collect_s: float = 2.0,
        gone_after_s: float = 3.0,
        iou_threshold: float = 0.25,
        min_no_face_s: float = 1.0,
        on_result: Callable[[Visit], None] | None = None,
    ) -> None:
        self.gallery = gallery
        self.need_samples = need_samples
        self.collect_s = collect_s
        self.gone_after_s = gone_after_s
        self.iou_threshold = iou_threshold
        self.min_no_face_s = min_no_face_s
        self.on_result = on_result
        self.active: dict[int, Visit] = {}
        self._next_id = 1

    def step(self, faces: list[FaceObs], now: float) -> list[Visit]:
        """Feed one processed frame's faces; returns the currently active visits."""
        self._associate(faces, now)
        for visit in self.active.values():
            if not visit.decided and self._ready(visit, now):
                self._decide(visit, now)
            if visit.result is not None and visit.obs is not None:
                self.gallery.touch(visit.result.code_num, now)
        for visit_id in [vid for vid, v in self.active.items() if now - v.last_seen > self.gone_after_s]:
            self._close(self.active.pop(visit_id), now)
        return list(self.active.values())

    def finish(self, now: float) -> None:
        for visit in list(self.active.values()):
            self._close(visit, now)
        self.active.clear()

    # ── internals ──

    def _associate(self, faces: list[FaceObs], now: float) -> None:
        pairs = []
        for vid, visit in self.active.items():
            for fi, face in enumerate(faces):
                score = iou(visit.bbox, face.bbox)
                if score < self.iou_threshold and _close(visit.bbox, face.bbox):
                    score = self.iou_threshold
                if score >= self.iou_threshold:
                    pairs.append((score, vid, fi))
        pairs.sort(reverse=True)
        used_v, used_f = set(), set()
        for _, vid, fi in pairs:
            if vid in used_v or fi in used_f:
                continue
            used_v.add(vid)
            used_f.add(fi)
            self.active[vid].add(faces[fi], now)
        for vid, visit in self.active.items():
            if vid not in used_v:
                visit.obs = None
        for fi, face in enumerate(faces):
            if fi not in used_f:
                visit = Visit(visit_id=self._next_id, bbox=face.bbox, first_seen=now, last_seen=now)
                self._next_id += 1
                visit.add(face, now)
                self.active[visit.visit_id] = visit

    def _ready(self, visit: Visit, now: float) -> bool:
        if not visit.samples:
            return False
        return len(visit.samples) >= self.need_samples or now - visit.first_good_at >= self.collect_s

    def _decide(self, visit: Visit, now: float) -> None:
        result = self.gallery.enroll(
            [(q, e) for q, e, _ in visit.samples], now,
            visit_start=visit.first_seen, continuation_gap_s=self.gone_after_s,
        )
        visit.result = result
        if result.kind == "same":
            visit.status = "same"
        elif result.kind == "auto":
            visit.status = "repeat"
        elif result.kind == "review":
            visit.status = "review"
        else:
            visit.status = "new"
        if self.on_result:
            self.on_result(visit)

    def _close(self, visit: Visit, now: float) -> None:
        if visit.decided:
            return
        if visit.samples:
            self._decide(visit, now)
        elif visit.last_seen - visit.first_seen >= self.min_no_face_s:
            visit.status = "no_face"
            if self.on_result:
                self.on_result(visit)
