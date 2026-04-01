#!/usr/bin/env python3
"""Export RF-DETR model to ONNX format for TensorRT conversion.

Usage:
    pip install rfdetr torch onnx onnxsim
    python scripts/export_rfdetr_onnx.py
"""
import os
import sys

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "pipeline", "models", "rfdetr")

def main():
    try:
        from rfdetr import RFDETRBase
    except ImportError:
        print("Install rfdetr: pip install rfdetr")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    onnx_path = os.path.join(OUTPUT_DIR, "rfdetr.onnx")

    print("Loading RF-DETR base model (COCO pretrained)...")
    model = RFDETRBase()

    print(f"Exporting to ONNX: {onnx_path}")
    model.export(onnx_path)

    print(f"ONNX model saved to: {onnx_path}")
    print("Next step: Run scripts/build_tensorrt_engine.sh to convert to TensorRT FP16")

if __name__ == "__main__":
    main()
