#!/usr/bin/env bash
# Build TensorRT FP16 engine for action classification SGIE.
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="${SCRIPT_DIR}/../pipeline/models/action_classifier"
ONNX_FILE="${MODEL_DIR}/action_classifier.onnx"
ENGINE_FILE="${MODEL_DIR}/action_classifier_fp16.engine"

if [ ! -f "$ONNX_FILE" ]; then
    echo "ERROR: ONNX file not found at $ONNX_FILE"
    echo "Run: python scripts/export_action_classifier_onnx.py first"
    exit 1
fi

echo "Building TensorRT FP16 engine for action classifier SGIE..."
echo "  ONNX:   $ONNX_FILE"
echo "  Engine: $ENGINE_FILE"
echo

trtexec \
    --onnx="$ONNX_FILE" \
    --saveEngine="$ENGINE_FILE" \
    --fp16 \
    --minShapes=input:1x3x224x224 \
    --optShapes=input:16x3x224x224 \
    --maxShapes=input:32x3x224x224 \
    --workspace=2048 \
    --verbose

echo
echo "TensorRT engine saved to: $ENGINE_FILE"
echo "Copy to pipeline/models/action_classifier/ if not already there."
