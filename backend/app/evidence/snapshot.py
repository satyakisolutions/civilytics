# backend/app/evidence/snapshot.py
"""Smart snapshot selection — pick best frame from a buffer."""
import logging

logger = logging.getLogger(__name__)


def compute_sharpness(frame_bytes: bytes) -> float:
    """Estimate frame sharpness using byte variance as proxy. Returns 0-1."""
    if not frame_bytes:
        return 0.0
    data = frame_bytes[:10000]  # sample first 10KB
    mean = sum(data) / len(data)
    variance = sum((b - mean) ** 2 for b in data) / len(data)
    return min(variance / 5000.0, 1.0)  # normalize


def score_frame(
    confidence: float,
    sharpness: float,
    framing_score: float = 0.5,
) -> float:
    """Score a frame for snapshot quality.

    score = confidence * 0.4 + sharpness * 0.3 + framing * 0.3
    """
    return confidence * 0.4 + sharpness * 0.3 + framing_score * 0.3


def select_best_frame(frames: list[dict]) -> dict | None:
    """Select the best frame from a list of {data, confidence, metadata}.

    Each frame dict should have: data (bytes), confidence (float), metadata (dict).
    Returns the highest-scoring frame or None.
    """
    if not frames:
        return None

    best = None
    best_score = -1.0

    for frame in frames:
        sharpness = compute_sharpness(frame.get("data", b""))
        score = score_frame(
            confidence=frame.get("confidence", 0.5),
            sharpness=sharpness,
        )
        if score > best_score:
            best_score = score
            best = frame

    return best
