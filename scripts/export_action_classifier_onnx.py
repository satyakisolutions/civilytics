#!/usr/bin/env python3
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Export action classification model to ONNX for TensorRT conversion.

The action classifier is a MobileNet-V2 fine-tuned on waste dumping action
categories. It takes 224x224 cropped person/vehicle regions and classifies
the observed action into one of 8 classes (see configs/action_labels.txt).

Usage:
    pip install torch torchvision onnx onnxsim
    python scripts/export_action_classifier_onnx.py

This exports to pipeline/models/action_classifier/action_classifier.onnx
Then use scripts/build_action_engine.sh to convert to TensorRT FP16.
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "pipeline", "models", "action_classifier")
ONNX_PATH = os.path.join(OUTPUT_DIR, "action_classifier.onnx")

NUM_CLASSES = 8
INPUT_SIZE = 224


def main() -> None:
    # --- Check dependencies -------------------------------------------------
    try:
        import torch
        import torchvision
    except ImportError:
        print("ERROR: Install dependencies first:")
        print("  pip install torch torchvision onnx onnxsim")
        sys.exit(1)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- Load MobileNet-V2 pretrained on ImageNet ---------------------------
    print("Loading MobileNet-V2 (ImageNet pretrained)...")
    model = torchvision.models.mobilenet_v2(
        weights=torchvision.models.MobileNet_V2_Weights.IMAGENET1K_V1,
    )

    # Replace the final classifier head with 8-class output
    in_features = model.classifier[1].in_features
    model.classifier[1] = torch.nn.Linear(in_features, NUM_CLASSES)
    print(f"Replaced classifier head: {in_features} -> {NUM_CLASSES} classes")

    # NOTE: In production, you would load fine-tuned weights here:
    #   model.load_state_dict(torch.load("path/to/finetuned_weights.pth"))
    # For now, we export the architecture with ImageNet backbone + random head.
    # The TensorRT engine will work once fine-tuned weights are loaded.

    model.eval()

    # --- Export to ONNX -----------------------------------------------------
    print(f"Exporting to ONNX: {ONNX_PATH}")
    dummy_input = torch.randn(1, 3, INPUT_SIZE, INPUT_SIZE)

    torch.onnx.export(
        model,
        dummy_input,
        ONNX_PATH,
        opset_version=17,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={
            "input": {0: "batch_size"},
            "output": {0: "batch_size"},
        },
    )

    print("ONNX export complete.")

    # --- Simplify with onnxsim ----------------------------------------------
    try:
        import onnx
        from onnxsim import simplify

        print("Running onnxsim simplification pass...")
        onnx_model = onnx.load(ONNX_PATH)
        simplified_model, check = simplify(onnx_model)
        if check:
            onnx.save(simplified_model, ONNX_PATH)
            print("onnxsim: model simplified successfully.")
        else:
            print("WARNING: onnxsim simplification check failed, keeping original.")
    except ImportError:
        print("WARNING: onnxsim not installed, skipping simplification.")
        print("  Install with: pip install onnxsim")

    # --- Summary ------------------------------------------------------------
    file_size_mb = os.path.getsize(ONNX_PATH) / (1024 * 1024)
    print()
    print("=" * 60)
    print(f"ONNX model saved to: {ONNX_PATH}")
    print(f"File size: {file_size_mb:.1f} MB")
    print(f"Input shape: (batch, 3, {INPUT_SIZE}, {INPUT_SIZE})")
    print(f"Output shape: (batch, {NUM_CLASSES})")
    print()
    print("Next steps:")
    print("  1. Fine-tune on waste dumping action dataset")
    print("  2. Re-export with fine-tuned weights")
    print("  3. Build TensorRT engine:")
    print("     bash scripts/build_action_engine.sh")
    print("=" * 60)


if __name__ == "__main__":
    main()
