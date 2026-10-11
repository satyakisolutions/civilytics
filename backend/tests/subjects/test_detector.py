import numpy as np
import pytest

from app.subjects.detector import decode_stride, letterbox


def test_letterbox_scales_and_pads():
    img = np.full((100, 200, 3), 255, dtype=np.uint8)
    out, scale = letterbox(img, (320, 320))
    assert out.shape == (320, 320, 3)
    assert scale == 1.6
    assert out[:160].min() == 255
    assert out[160:].max() == 0


def test_decode_stride_reconstructs_box_and_landmarks():
    # 2x2 grid, stride 8, 2 anchors -> 8 anchors; anchor 3 sits at centre (8, 0).
    scores = np.zeros((8, 1), dtype=np.float32)
    scores[3] = 0.9
    bbox = np.ones((8, 4), dtype=np.float32)          # distance 1 * stride 8 on every side
    kps = np.zeros((8, 10), dtype=np.float32)         # all landmarks at the anchor centre
    s, b, k = decode_stride(scores, bbox, kps, stride=8, height=2, width=2, num_anchors=2, threshold=0.5)
    assert s.shape == (1,) and float(s[0]) == pytest.approx(0.9)
    assert b[0].tolist() == [0.0, -8.0, 16.0, 8.0]
    assert k.shape == (1, 5, 2)
    assert np.allclose(k[0], [[8.0, 0.0]] * 5)


def test_decode_stride_below_threshold_is_empty():
    s, b, k = decode_stride(
        np.full((8, 1), 0.1, np.float32), np.ones((8, 4), np.float32), np.zeros((8, 10), np.float32),
        stride=8, height=2, width=2, num_anchors=2, threshold=0.5,
    )
    assert len(s) == 0 and b.shape == (0, 4) and k.shape == (0, 5, 2)
