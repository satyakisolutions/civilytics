import numpy as np
import pytest

from app.subjects.config import SubjectSettings
from app.subjects.quality import laplacian_sharpness, passes_gate, quality_score, yaw_proxy

S = SubjectSettings()


def _lm(nose_x: float) -> np.ndarray:
    # left eye, right eye, nose, left mouth, right mouth
    return np.array([[30, 40], [70, 40], [nose_x, 60], [35, 80], [65, 80]], dtype=np.float32)


def test_yaw_is_zero_for_frontal_face():
    assert yaw_proxy(_lm(50)) == pytest.approx(0.0)


def test_yaw_grows_as_nose_moves_off_centre():
    assert yaw_proxy(_lm(68)) == pytest.approx(0.45)
    assert yaw_proxy(_lm(32)) == pytest.approx(-0.45)


def test_yaw_degenerate_eyes_is_worst_case():
    lm = _lm(50)
    lm[1] = lm[0]
    assert yaw_proxy(lm) == 1.0


@pytest.mark.parametrize("det,width,sharp,yaw,ok", [
    (0.60, 40.0, 60.0, 0.45, True),
    (0.59, 40.0, 60.0, 0.00, False),
    (0.90, 39.9, 60.0, 0.00, False),
    (0.90, 80.0, 59.9, 0.00, False),
    (0.90, 80.0, 90.0, -0.46, False),
])
def test_gate_boundaries(det, width, sharp, yaw, ok):
    assert passes_gate(det, width, sharp, yaw, S) is ok


def test_quality_score_saturates_and_penalises():
    assert quality_score(0.9, 112, 200, 0.0) == pytest.approx(0.9)
    assert quality_score(0.9, 300, 900, 0.0) == pytest.approx(0.9)
    assert quality_score(0.9, 56, 100, 0.5) == pytest.approx(0.9 * 0.5 * 0.5 * 0.5)


def test_sharpness_flat_vs_textured():
    flat = np.full((112, 112), 128, dtype=np.uint8)
    checker = (np.indices((112, 112)).sum(axis=0) % 2 * 255).astype(np.uint8)
    assert laplacian_sharpness(flat) == 0.0
    assert laplacian_sharpness(checker) > 1000.0
