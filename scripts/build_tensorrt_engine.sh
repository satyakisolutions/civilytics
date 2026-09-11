#!/usr/bin/env bash
# Build TensorRT FP16 engine from RF-DETR ONNX model.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${SCRIPT_DIR}/../pipeline/models/rfdetr"
ONNX_FILE="${MODEL_DIR}/rfdetr.onnx"
ENGINE_FILE="${MODEL_DIR}/rfdetr_fp16.engine"

if [ ! -f "$ONNX_FILE" ]; then
    echo "ERROR: ONNX file not found at $ONNX_FILE"
    echo "Run: python scripts/export_rfdetr_onnx.py first"
    exit 1
fi

echo "Building TensorRT FP16 engine..."
echo "  ONNX:   $ONNX_FILE"
echo "  Engine: $ENGINE_FILE"

trtexec \
    --onnx="$ONNX_FILE" \
    --saveEngine="$ENGINE_FILE" \
    --fp16 \
    --minShapes=input:1x3x640x640 \
    --optShapes=input:4x3x640x640 \
    --maxShapes=input:8x3x640x640 \
    --workspace=4096 \
    --verbose

echo "TensorRT engine saved to: $ENGINE_FILE"
