#!/usr/bin/env bash
# Download and set up all WasteWatch AI models.
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."
MODELS_DIR="${PROJECT_ROOT}/pipeline/models"

echo "=========================================="
echo "  WasteWatch Model Setup"
echo "  Satyaki Solutions Pvt Ltd"
echo "=========================================="

# ---------------------------------------------------------------------------
# 1. RF-DETR Object Detection
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] RF-DETR Object Detection Model"
RFDETR_DIR="${MODELS_DIR}/rfdetr"
mkdir -p "$RFDETR_DIR"

if [ ! -f "$RFDETR_DIR/rfdetr.onnx" ]; then
    echo "  Exporting RF-DETR to ONNX..."
    if [ -f "${SCRIPT_DIR}/export_rfdetr_onnx.py" ]; then
        python3 "${SCRIPT_DIR}/export_rfdetr_onnx.py"
    else
        echo "  ERROR: export_rfdetr_onnx.py not found at ${SCRIPT_DIR}/export_rfdetr_onnx.py"
        echo "  Please provide the export script or place rfdetr.onnx manually in ${RFDETR_DIR}/"
    fi
else
    echo "  OK — RF-DETR ONNX already exists"
fi

if command -v trtexec &>/dev/null; then
    if [ ! -f "$RFDETR_DIR/rfdetr_fp16.engine" ]; then
        echo "  Building TensorRT FP16 engine..."
        if [ -f "${SCRIPT_DIR}/build_tensorrt_engine.sh" ]; then
            bash "${SCRIPT_DIR}/build_tensorrt_engine.sh"
        else
            echo "  Running trtexec directly..."
            trtexec \
                --onnx="$RFDETR_DIR/rfdetr.onnx" \
                --saveEngine="$RFDETR_DIR/rfdetr_fp16.engine" \
                --fp16 \
                --workspace=4096 \
                --minShapes=input:1x3x640x640 \
                --optShapes=input:4x3x640x640 \
                --maxShapes=input:8x3x640x640
        fi
    else
        echo "  OK — RF-DETR TensorRT engine already exists"
    fi
else
    echo "  WARN: trtexec not found — TensorRT engine will be built on first pipeline run"
fi

# ---------------------------------------------------------------------------
# 2. Action Classifier SGIE
# ---------------------------------------------------------------------------
echo ""
echo "[2/5] Action Classifier (MobileNet-V2)"
ACTION_DIR="${MODELS_DIR}/action_classifier"
mkdir -p "$ACTION_DIR"

if [ ! -f "$ACTION_DIR/action_classifier.onnx" ]; then
    echo "  Exporting action classifier to ONNX..."
    if [ -f "${SCRIPT_DIR}/export_action_classifier_onnx.py" ]; then
        python3 "${SCRIPT_DIR}/export_action_classifier_onnx.py"
    else
        echo "  ERROR: export_action_classifier_onnx.py not found at ${SCRIPT_DIR}/export_action_classifier_onnx.py"
        echo "  Please provide the export script or place action_classifier.onnx manually in ${ACTION_DIR}/"
    fi
else
    echo "  OK — Action classifier ONNX already exists"
fi

if command -v trtexec &>/dev/null; then
    if [ ! -f "$ACTION_DIR/action_classifier_fp16.engine" ]; then
        echo "  Building TensorRT FP16 engine..."
        if [ -f "${SCRIPT_DIR}/build_action_engine.sh" ]; then
            bash "${SCRIPT_DIR}/build_action_engine.sh"
        else
            echo "  Running trtexec directly..."
            trtexec \
                --onnx="$ACTION_DIR/action_classifier.onnx" \
                --saveEngine="$ACTION_DIR/action_classifier_fp16.engine" \
                --fp16 \
                --workspace=2048 \
                --minShapes=input:1x3x224x224 \
                --optShapes=input:16x3x224x224 \
                --maxShapes=input:32x3x224x224
        fi
    else
        echo "  OK — Action classifier TensorRT engine already exists"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Piper TTS
# ---------------------------------------------------------------------------
echo ""
echo "[3/5] Piper TTS (en_US-lessac-medium)"
TTS_DIR="${MODELS_DIR}/piper-tts"
mkdir -p "$TTS_DIR"

PIPER_MODEL="en_US-lessac-medium"
PIPER_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

if [ ! -f "$TTS_DIR/${PIPER_MODEL}.onnx" ]; then
    echo "  Downloading Piper TTS model..."
    curl -fSL --progress-bar \
        -o "$TTS_DIR/${PIPER_MODEL}.onnx" \
        "${PIPER_BASE_URL}/en_US-lessac-medium.onnx"
    echo "  Downloading Piper TTS config..."
    curl -fSL --progress-bar \
        -o "$TTS_DIR/${PIPER_MODEL}.onnx.json" \
        "${PIPER_BASE_URL}/en_US-lessac-medium.onnx.json"
else
    echo "  OK — Piper TTS model already exists"
fi

# ---------------------------------------------------------------------------
# 4. PaddleOCR
# ---------------------------------------------------------------------------
echo ""
echo "[4/5] PaddleOCR (en_PP-OCRv4)"
OCR_DIR="${MODELS_DIR}/paddleocr"
mkdir -p "$OCR_DIR"

# PaddleOCR downloads models automatically on first use via the paddleocr
# Python package.  We just ensure the cache directory exists.
echo "  PaddleOCR models download on first use via the paddleocr Python package."
echo "  Ensure the package is installed: pip install paddleocr"

# ---------------------------------------------------------------------------
# 5. Verification
# ---------------------------------------------------------------------------
echo ""
# --- 5. BitNet (Report Narrator) ---
echo ""
echo "[5/6] BitNet 1.58b (Report Narrator)"
BITNET_DIR="${MODELS_DIR}/bitnet"
mkdir -p "$BITNET_DIR"
BITNET_MODEL="BitNet-b1.58-2B-4T-Q4_K_M.gguf"
BITNET_URL="https://huggingface.co/microsoft/BitNet-b1.58-2B-4T-GGUF/resolve/main/${BITNET_MODEL}"

if [ -f "$BITNET_DIR/$BITNET_MODEL" ]; then
    echo "  OK  BitNet model already downloaded ($(du -h "$BITNET_DIR/$BITNET_MODEL" | cut -f1))"
else
    echo "  Downloading BitNet 1.58b GGUF (~1.5 GB)..."
    curl -L --progress-bar -o "$BITNET_DIR/$BITNET_MODEL" "$BITNET_URL"
    if [ -f "$BITNET_DIR/$BITNET_MODEL" ]; then
        echo "  OK  BitNet downloaded ($(du -h "$BITNET_DIR/$BITNET_MODEL" | cut -f1))"
    else
        echo "  WARN  BitNet download failed — reports will use template fallbacks"
    fi
fi

# --- 6. Verification ---
echo ""
echo "[6/6] Verification"
echo "  Checking model files..."

MISSING=0

check_file() {
    local path="$1"
    local label="$2"
    if [ -f "$path" ]; then
        SIZE=$(du -h "$path" | cut -f1)
        echo "  OK  $label ($SIZE)"
    else
        echo "  MISSING  $label — $path"
        MISSING=$((MISSING + 1))
    fi
}

check_file "$RFDETR_DIR/rfdetr.onnx" "RF-DETR ONNX"
check_file "$RFDETR_DIR/rfdetr_fp16.engine" "RF-DETR TensorRT FP16"
check_file "$ACTION_DIR/action_classifier.onnx" "Action Classifier ONNX"
check_file "$ACTION_DIR/action_classifier_fp16.engine" "Action Classifier TensorRT FP16"
check_file "$TTS_DIR/${PIPER_MODEL}.onnx" "Piper TTS Model"
check_file "$TTS_DIR/${PIPER_MODEL}.onnx.json" "Piper TTS Config"
check_file "$BITNET_DIR/$BITNET_MODEL" "BitNet 1.58b GGUF"

echo ""
if [ "$MISSING" -gt 0 ]; then
    echo "WARNING: $MISSING model file(s) missing — see above."
else
    echo "All model files present."
fi
echo ""
echo "=========================================="
echo "  Model setup complete!"
echo "=========================================="
