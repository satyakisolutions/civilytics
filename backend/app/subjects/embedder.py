"""ArcFace alignment + embedding (ONNX, CPU)."""
from __future__ import annotations

import cv2
import numpy as np

# Standard ArcFace 112x112 five-point template.
ARCFACE_TEMPLATE = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def alignment_matrix(landmarks: np.ndarray) -> np.ndarray:
    matrix, _ = cv2.estimateAffinePartial2D(
        np.asarray(landmarks, dtype=np.float32), ARCFACE_TEMPLATE, method=cv2.LMEDS
    )
    if matrix is None:
        raise ValueError("Could not estimate face alignment")
    return matrix


def align_face(img_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
    return cv2.warpAffine(img_bgr, alignment_matrix(landmarks), (112, 112), borderValue=0.0)


class ArcFaceEmbedder:
    def __init__(self, model_path: str):
        import onnxruntime as ort

        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name

    def embed(self, aligned_bgr: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(
            aligned_bgr, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True
        )
        vector = self._session.run(None, {self._input_name: blob})[0][0].astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector
