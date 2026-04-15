"""Offline RF-DETR inference sanity check.

Feeds a single frame from a real sample video through:
  1. The original (pre-surgery) RF-DETR ONNX — with both ImageNet-normalised
     and plain 0..1 inputs, so we can tell whether DeepStream's
     `net-scale-factor=1/255` preprocessing is compatible.
  2. The post-surgery ONNX (only if it loads cleanly under onnxruntime) —
     catches the case where `append_rfdetr_deepstream_head` / the ReduceSum
     attribute rewrite produced an invalid graph that TRT silently accepts
     but executes as a no-op (which collapses every score to ~0.06).

This script is meant to be run INSIDE the lsg-wastewatch-pipeline container:

    # 1. get a real frame onto the host
    ffmpeg -ss 10 -i sample_videos/VID-20260131-WA0007.mp4 -frames:v 1 /tmp/frame.png

    # 2. copy both the frame and this script into the container
    docker cp /tmp/frame.png lsg-wastewatch-pipeline-1:/tmp/frame.png
    docker cp scripts/test_rfdetr_inference.py lsg-wastewatch-pipeline-1:/tmp/

    # 3. make sure Pillow is available (install as root if needed)
    docker exec -u 0 lsg-wastewatch-pipeline-1 pip install -q Pillow

    # 4. run
    docker exec lsg-wastewatch-pipeline-1 python3 /tmp/test_rfdetr_inference.py \
        --frame /tmp/frame.png \
        --orig-onnx /app/models/rfdetr/rfdetr.orig.onnx \
        --surgery-onnx /app/models/rfdetr/rfdetr.onnx

Expected (healthy) output for a daylight LSG clip with motion: max score
above 0.4 for car/motorcycle/person classes (1, 3, 4, 8). Max score stuck
around 0.06 means the surgery stage has broken the decoder — see the
docstring on `ModelSpec.convert_reduce_axes` in scripts/onnx_to_engine.py.

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np


def _load_frame(path: str, size: int = 560) -> tuple[np.ndarray, np.ndarray]:
    """Return (`plain_01_nchw`, `imagenet_norm_nchw`)."""
    from PIL import Image

    img = Image.open(path).convert("RGB").resize((size, size), Image.BILINEAR)
    a = np.asarray(img, dtype=np.float32) / 255.0

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    a_norm = (a - mean) / std

    plain = a.transpose(2, 0, 1)[None].astype(np.float32)
    norm = a_norm.transpose(2, 0, 1)[None].astype(np.float32)
    return plain, norm


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _summarise_dets_labels(dets: np.ndarray, labels: np.ndarray, top_n: int) -> None:
    scores_all = _sigmoid(labels[0])
    cls = scores_all.argmax(axis=-1)
    sc = scores_all.max(axis=-1)
    print(f"  score max={sc.max():.4f} mean={sc.mean():.4f}")
    top = np.argsort(-sc)[:top_n]
    for i in top:
        cx, cy, w, h = dets[0, i]
        print(
            f"    q{i:3d} score={sc[i]:.4f} cls={int(cls[i]):2d} "
            f"cxcywh=({cx:.3f},{cy:.3f},{w:.3f},{h:.3f})"
        )


def _summarise_fused(out: np.ndarray, top_n: int) -> None:
    scores = out[0, :, 4]
    cls = out[0, :, 5].astype(int)
    print(f"  fused output score max={scores.max():.4f} mean={scores.mean():.4f}")
    top = np.argsort(-scores)[:top_n]
    for i in top:
        x1, y1, x2, y2 = out[0, i, :4]
        print(
            f"    row{i:3d} score={scores[i]:.4f} cls={int(cls[i]):2d} "
            f"xyxy=({x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f})"
        )


def _run_session(model_path: str, inp: np.ndarray) -> list[np.ndarray]:
    import onnxruntime as ort

    sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name
    return sess.run(None, {input_name: inp})


def test_original_onnx(path: str, plain: np.ndarray, norm: np.ndarray, top_n: int) -> None:
    print(f"\n== ORIGINAL ONNX: {path} ==")
    print("  preprocessing: ImageNet-normalised (mean/std)")
    dets, labels = _run_session(path, norm)
    _summarise_dets_labels(dets, labels, top_n)

    print("  preprocessing: plain 0..1 (matches DeepStream net-scale-factor=1/255)")
    dets, labels = _run_session(path, plain)
    _summarise_dets_labels(dets, labels, top_n)


def test_surgery_onnx(path: str, plain: np.ndarray, top_n: int) -> None:
    print(f"\n== POST-SURGERY ONNX: {path} ==")
    try:
        outs = _run_session(path, plain)
    except Exception as exc:
        print("  ONNX RUNTIME REJECTED THE GRAPH — surgery broke the model.")
        print(f"  {exc}")
        print(
            "  (TRT may still accept this invalid graph silently; the engine "
            "will look like it runs but produce garbage output.)"
        )
        return

    if len(outs) == 1 and outs[0].ndim == 3 and outs[0].shape[-1] == 6:
        _summarise_fused(outs[0], top_n)
    else:
        print(f"  unexpected output signature: {[(o.shape, o.dtype) for o in outs]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--frame", required=True, help="Path to an input image (e.g. /tmp/frame.png)")
    ap.add_argument(
        "--orig-onnx",
        default="/app/models/rfdetr/rfdetr.orig.onnx",
        help="Path to the pre-surgery RF-DETR ONNX",
    )
    ap.add_argument(
        "--surgery-onnx",
        default="/app/models/rfdetr/rfdetr.onnx",
        help="Path to the post-surgery RF-DETR ONNX (with DeepStreamOutput head)",
    )
    ap.add_argument("--top", type=int, default=8, help="Number of top queries to dump")
    args = ap.parse_args()

    plain, norm = _load_frame(args.frame)
    print(f"input tensor shape: {plain.shape} dtype={plain.dtype}")

    test_original_onnx(args.orig_onnx, plain, norm, args.top)
    test_surgery_onnx(args.surgery_onnx, plain, args.top)
    return 0


if __name__ == "__main__":
    sys.exit(main())
