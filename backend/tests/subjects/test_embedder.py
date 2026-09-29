import cv2
import numpy as np

from app.subjects.embedder import ARCFACE_TEMPLATE, align_face, alignment_matrix


def test_alignment_maps_landmarks_onto_template():
    landmarks = ARCFACE_TEMPLATE * 2.0 + np.array([10.0, 20.0], dtype=np.float32)
    m = alignment_matrix(landmarks)
    mapped = cv2.transform(landmarks.reshape(-1, 1, 2), m).reshape(-1, 2)
    assert np.allclose(mapped, ARCFACE_TEMPLATE, atol=1e-2)


def test_align_face_output_is_112_square():
    img = np.random.default_rng(0).integers(0, 255, (300, 300, 3), dtype=np.uint8)
    out = align_face(img, ARCFACE_TEMPLATE * 2.0)
    assert out.shape == (112, 112, 3)
