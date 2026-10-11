"""SCRFD face detector (ONNX, CPU).

Post-processing follows InsightFace's SCRFD reference implementation
(MIT-licensed code). The det_10g.onnx weights are research-only -- see
the spec's licensing section before commercial deployment.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class FaceDetection:
    score: float
    bbox: np.ndarray  # (4,) x1, y1, x2, y2 in source-image pixels
    kps: np.ndarray   # (5, 2) landmarks in source-image pixels


def letterbox(img: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, float]:
    """Resize keeping aspect ratio into the top-left of a black (w, h) canvas."""
    w_in, h_in = size
    h, w = img.shape[:2]
    scale = min(w_in / w, h_in / h)
    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
    canvas = np.zeros((h_in, w_in, 3), dtype=np.uint8)
    canvas[:new_h, :new_w] = cv2.resize(img, (new_w, new_h))
    return canvas, scale


def decode_stride(
    scores: np.ndarray,
    bbox_preds: np.ndarray,
    kps_preds: np.ndarray,
    stride: int,
    height: int,
    width: int,
    num_anchors: int,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Scores, boxes (N,4) and landmarks (N,5,2) above threshold for one FPN level."""
    centers = np.stack(np.mgrid[:height, :width][::-1], axis=-1).astype(np.float32)
    centers = (centers * stride).reshape(-1, 2)
    if num_anchors > 1:
        centers = np.stack([centers] * num_anchors, axis=1).reshape(-1, 2)
    flat_scores = scores.reshape(-1)
    keep = np.where(flat_scores >= threshold)[0]
    dist = bbox_preds.reshape(-1, 4)[keep] * stride
    kdist = kps_preds.reshape(-1, 10)[keep] * stride
    c = centers[keep]
    bboxes = np.stack(
        [c[:, 0] - dist[:, 0], c[:, 1] - dist[:, 1], c[:, 0] + dist[:, 2], c[:, 1] + dist[:, 3]],
        axis=-1,
    ).reshape(-1, 4)
    kpss = np.empty((len(keep), 5, 2), dtype=np.float32)
    kpss[:, :, 0] = c[:, 0:1] + kdist[:, 0::2]
    kpss[:, :, 1] = c[:, 1:2] + kdist[:, 1::2]
    return flat_scores[keep], bboxes, kpss


class ScrfdDetector:
    STRIDES = (8, 16, 32)
    NUM_ANCHORS = 2

    def __init__(self, model_path: str, input_size: tuple[int, int] = (320, 320), det_thresh: float = 0.5):
        import onnxruntime as ort

        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [o.name for o in self._session.get_outputs()]
        if len(self._output_names) != 9:
            raise ValueError(f"Expected SCRFD with keypoints (9 outputs), got {len(self._output_names)}")
        self._input_size = input_size
        self._det_thresh = det_thresh

    def detect_best(self, img_bgr: np.ndarray) -> FaceDetection | None:
        det_img, scale = letterbox(img_bgr, self._input_size)
        blob = cv2.dnn.blobFromImage(
            det_img, 1.0 / 128.0, self._input_size, (127.5, 127.5, 127.5), swapRB=True
        )
        outs = self._session.run(self._output_names, {self._input_name: blob})
        w_in, h_in = self._input_size
        best: tuple[float, np.ndarray, np.ndarray] | None = None
        for idx, stride in enumerate(self.STRIDES):
            s, b, k = (o[0] if o.ndim == 3 else o for o in (outs[idx], outs[idx + 3], outs[idx + 6]))
            scores, bboxes, kpss = decode_stride(
                s, b, k, stride, h_in // stride, w_in // stride, self.NUM_ANCHORS, self._det_thresh
            )
            if len(scores):
                i = int(np.argmax(scores))
                if best is None or float(scores[i]) > best[0]:
                    best = (float(scores[i]), bboxes[i], kpss[i])
        if best is None:
            return None
        score, bbox, kps = best
        return FaceDetection(score=score, bbox=bbox / scale, kps=kps / scale)
