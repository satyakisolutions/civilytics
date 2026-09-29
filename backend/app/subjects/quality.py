"""Face quality gate and score (spec §6.2 step 3). Pure apart from cv2.Laplacian."""
from __future__ import annotations

import numpy as np

from app.subjects.config import SubjectSettings


def yaw_proxy(landmarks: np.ndarray) -> float:
    """Horizontal nose offset from the eye midpoint, in inter-eye distances.

    0 = frontal, |0.45| ~ 45 degrees. Landmark order (SCRFD): left eye,
    right eye, nose, left mouth corner, right mouth corner.
    """
    left_eye, right_eye, nose = landmarks[0], landmarks[1], landmarks[2]
    inter_eye = abs(float(right_eye[0]) - float(left_eye[0]))
    if inter_eye < 1e-6:
        return 1.0
    eye_mid_x = (float(left_eye[0]) + float(right_eye[0])) / 2.0
    return (float(nose[0]) - eye_mid_x) / inter_eye


def laplacian_sharpness(gray: np.ndarray) -> float:
    import cv2

    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def passes_gate(
    det_score: float, face_width: float, sharpness: float, yaw: float, settings: SubjectSettings
) -> bool:
    return (
        det_score >= settings.min_det
        and face_width >= settings.min_px
        and sharpness >= settings.min_sharpness
        and abs(yaw) <= settings.max_yaw
    )


def quality_score(det_score: float, face_width: float, sharpness: float, yaw: float) -> float:
    return (
        det_score
        * min(1.0, face_width / 112.0)
        * min(1.0, sharpness / 200.0)
        * max(0.0, 1.0 - abs(yaw))
    )
