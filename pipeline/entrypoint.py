#!/usr/bin/env python3
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""WasteWatch pipeline entrypoint — init phase + pipeline startup.

Startup order:
    1. Auto-build TensorRT engines if missing (ONNX → FP16 engine)
    2. Download missing model files if needed
    3. Validate all required files exist
    4. Start the DeepStream GStreamer pipeline

This replaces the direct `python3 app/pipeline.py` CMD in the Dockerfile.
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("wastewatch.init")


def main() -> None:
    logger.info("=" * 60)
    logger.info("  WasteWatch Pipeline Init")
    logger.info("  Satyaki Solutions Pvt Ltd")
    logger.info("=" * 60)

    # --- Phase 1: Auto-build TensorRT engines ---
    logger.info("")
    logger.info("[Phase 1] Checking TensorRT engines...")
    from app.engine_builder import ensure_engines

    results = ensure_engines(skip_missing_onnx=False)

    # Check if critical engines failed
    rfdetr_status = results.get("RF-DETR (PGIE)", "unknown")
    if rfdetr_status in ("build_failed", "onnx_missing"):
        logger.error(
            "CRITICAL: RF-DETR engine not available (status=%s). "
            "Pipeline cannot start without the primary detector.",
            rfdetr_status,
        )
        logger.error("Fix: place rfdetr.onnx in models/rfdetr/ or run scripts/export_rfdetr_onnx.py")
        sys.exit(1)

    # Action classifier is optional — pipeline works without SGIE
    action_status = results.get("Action Classifier (SGIE)", "unknown")
    if action_status not in ("ready", "built"):
        logger.warning(
            "Action classifier not available (status=%s). "
            "Pipeline will run without action recognition SGIE — "
            "dwell-based detection only (slower alerts).",
            action_status,
        )

    # --- Phase 2: Validate critical files ---
    logger.info("")
    logger.info("[Phase 2] Validating configuration...")
    from pathlib import Path

    critical_files = [
        ("configs/nvinfer_rfdetr.txt", "RF-DETR nvinfer config"),
        ("configs/labels.txt", "Detection labels"),
        ("configs/nvtracker_bytetrack.yml", "ByteTrack tracker config"),
    ]

    optional_files = [
        ("configs/nvinfer_action_sgie.txt", "Action classifier SGIE config"),
        ("configs/action_labels.txt", "Action labels"),
        ("configs/smart_record.json", "Smart recording config"),
    ]

    all_ok = True
    for path, desc in critical_files:
        if Path(path).exists():
            logger.info("  [OK]   %s — %s", path, desc)
        else:
            logger.error("  [FAIL] %s — %s — MISSING", path, desc)
            all_ok = False

    for path, desc in optional_files:
        if Path(path).exists():
            logger.info("  [OK]   %s — %s", path, desc)
        else:
            logger.warning("  [SKIP] %s — %s — optional", path, desc)

    if not all_ok:
        logger.error("Critical configuration files missing. Cannot start pipeline.")
        sys.exit(1)

    # --- Phase 3: Start pipeline ---
    logger.info("")
    logger.info("[Phase 3] Starting DeepStream pipeline...")
    logger.info("=" * 60)

    from app.pipeline import main as pipeline_main
    pipeline_main()


if __name__ == "__main__":
    main()
