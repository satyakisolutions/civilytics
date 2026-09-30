"""Multi-face detection + per-face quality gate and embedding, using the feature's code."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

import facelab  # noqa: F401  (puts backend/ on sys.path)
from app.subjects.config import SubjectSettings
from app.subjects.detector import FaceDetection, ScrfdDetector, decode_stride, letterbox
from app.subjects.embedder import ArcFaceEmbedder, align_face
from app.subjects.quality import laplacian_sharpness, quality_score, yaw_proxy


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Indices kept by greedy non-maximum suppression (highest score first)."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        w = np.maximum(0.0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        h = np.maximum(0.0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = w * h
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


class MultiFaceDetector(ScrfdDetector):
    """The feature's SCRFD detector, returning every face instead of only the best one.

    The feature runs on head crops (one person each); the lab runs on whole
    camera frames, so it needs all faces plus NMS.
    """

    def detect_all(self, img_bgr: np.ndarray, nms_iou: float = 0.4) -> list[FaceDetection]:
        det_img, scale = letterbox(img_bgr, self._input_size)
        blob = cv2.dnn.blobFromImage(
            det_img, 1.0 / 128.0, self._input_size, (127.5, 127.5, 127.5), swapRB=True
        )
        outs = self._session.run(self._output_names, {self._input_name: blob})
        w_in, h_in = self._input_size
        all_scores, all_boxes, all_kps = [], [], []
        for idx, stride in enumerate(self.STRIDES):
            s, b, k = (o[0] if o.ndim == 3 else o for o in (outs[idx], outs[idx + 3], outs[idx + 6]))
            scores, boxes, kpss = decode_stride(
                s, b, k, stride, h_in // stride, w_in // stride, self.NUM_ANCHORS, self._det_thresh
            )
            all_scores.append(scores)
            all_boxes.append(boxes)
            all_kps.append(kpss)
        scores = np.concatenate(all_scores)
        boxes = np.concatenate(all_boxes)
        kpss = np.concatenate(all_kps)
        return [
            FaceDetection(score=float(scores[i]), bbox=boxes[i] / scale, kps=kpss[i] / scale)
            for i in nms(boxes, scores, nms_iou)
        ]


def gate_reason(det_score: float, width: float, sharpness: float, yaw: float, s: SubjectSettings) -> str | None:
    """Why a face fails the feature's quality gate (same checks as quality.passes_gate), or None."""
    if det_score < s.min_det:
        return "low confidence"
    if width < s.min_px:
        return "too small"
    if sharpness < s.min_sharpness:
        return "blurry"
    if abs(yaw) > s.max_yaw:
        return "turned away"
    return None


@dataclass
class FaceObs:
    """One face in one frame."""
    bbox: np.ndarray          # x1, y1, x2, y2 in frame pixels
    det_score: float
    width: float
    sharpness: float
    yaw: float
    reason: str | None        # None = passed the quality gate
    quality: float            # 0 when the gate failed
    embedding: np.ndarray | None  # only for faces that passed
    aligned: np.ndarray       # 112x112 BGR aligned face

    @property
    def passed(self) -> bool:
        return self.reason is None


class FaceAnalyzer:
    def __init__(self, detector: MultiFaceDetector, embedder: ArcFaceEmbedder, settings: SubjectSettings):
        self._detector = detector
        self._embedder = embedder
        self._settings = settings

    def analyze(self, frame: np.ndarray) -> list[FaceObs]:
        faces: list[FaceObs] = []
        for det in self._detector.detect_all(frame):
            width = float(det.bbox[2] - det.bbox[0])
            try:
                aligned = align_face(frame, det.kps)
            except ValueError:
                continue
            sharpness = laplacian_sharpness(cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY))
            yaw = yaw_proxy(det.kps)
            reason = gate_reason(det.score, width, sharpness, yaw, self._settings)
            passed = reason is None
            faces.append(FaceObs(
                bbox=det.bbox,
                det_score=det.score,
                width=width,
                sharpness=sharpness,
                yaw=yaw,
                reason=reason,
                quality=quality_score(det.score, width, sharpness, yaw) if passed else 0.0,
                embedding=self._embedder.embed(aligned) if passed else None,
                aligned=aligned,
            ))
        return faces
