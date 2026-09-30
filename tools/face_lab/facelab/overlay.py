"""Draw face boxes, subject IDs and status on frames (ASCII only -- cv2.putText has no unicode)."""
from __future__ import annotations

import cv2
import numpy as np

from facelab.session import Visit

# BGR
COLORS = {
    "collecting": (0, 215, 255),  # yellow
    "new": (60, 200, 60),         # green
    "repeat": (40, 40, 230),      # red
    "review": (0, 140, 255),      # orange
    "no_face": (160, 160, 160),   # grey
}


def visit_label(visit: Visit, need_samples: int) -> str:
    r = visit.result
    if visit.status == "new":
        return f"{r.code} - New"
    if visit.status == "repeat":
        return f"{r.code} - Repeat x{r.incident_count} ({r.similarity:.2f})"
    if visit.status == "review":
        return f"{r.code} - maybe {r.candidate_code}? ({r.similarity:.2f})"
    if visit.status == "no_face":
        return "Face not captured"
    reason = visit.obs.reason if visit.obs is not None else None
    progress = f"collecting {len(visit.samples)}/{need_samples}"
    return f"{progress}: {reason}" if reason else progress


def draw(frame: np.ndarray, visits: list[Visit], need_samples: int, stats: str) -> np.ndarray:
    out = frame.copy()
    for visit in visits:
        x1, y1, x2, y2 = (int(v) for v in visit.bbox)
        color = COLORS.get(visit.status, (255, 255, 255))
        thickness = 2 if visit.obs is not None else 1  # thin = not seen in the latest processed frame
        cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)
        label = visit_label(visit, need_samples)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        top = max(0, y1 - th - 8)
        cv2.rectangle(out, (x1, top), (x1 + tw + 6, top + th + 8), color, -1)
        cv2.putText(out, label, (x1 + 3, top + th + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.rectangle(out, (0, 0), (min(out.shape[1], 12 + 9 * len(stats)), 28), (0, 0, 0), -1)
    cv2.putText(out, stats, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out
