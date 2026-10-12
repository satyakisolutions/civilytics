#!/usr/bin/env python3
"""Download and verify all WasteWatch AI model files.

Suitable for use in Docker builds and CI where bash may not be ideal.
Requires only the Python standard library (uses urllib for downloads).

Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
MODELS_DIR = PROJECT_ROOT / "pipeline" / "models"

RFDETR_DIR = MODELS_DIR / "rfdetr"
ACTION_DIR = MODELS_DIR / "action_classifier"
TTS_DIR = MODELS_DIR / "piper-tts"
OCR_DIR = MODELS_DIR / "paddleocr"

# ---------------------------------------------------------------------------
# Piper TTS download URLs
# ---------------------------------------------------------------------------

PIPER_MODEL_NAME = "en_US-lessac-medium"
PIPER_BASE_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    "/en/en_US/lessac/medium"
)

PIPER_FILES: list[tuple[str, str | None]] = [
    # (filename, sha256 or None)
    (f"{PIPER_MODEL_NAME}.onnx", None),
    (f"{PIPER_MODEL_NAME}.onnx.json", None),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_RESET = "\033[0m"


def _header(msg: str) -> None:
    print(f"\n{_BOLD}{msg}{_RESET}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}OK{_RESET}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YELLOW}WARN{_RESET}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {_RED}MISSING{_RESET}  {msg}")


def _download(url: str, dest: Path, sha256: str | None = None) -> bool:
    """Download *url* to *dest* with a simple progress bar.

    Returns True on success, False on failure.
    """
    print(f"  Downloading {dest.name} ...")
    try:
        resp = urllib.request.urlopen(url)  # noqa: S310
    except urllib.error.URLError as exc:
        _warn(f"Download failed: {exc}")
        return False

    total = int(resp.headers.get("Content-Length", 0))
    chunk_size = 1 << 16  # 64 KiB
    downloaded = 0
    hasher = hashlib.sha256() if sha256 else None

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as fh:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            fh.write(chunk)
            if hasher:
                hasher.update(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = downloaded * 100 // total
                bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
                print(f"\r  [{bar}] {pct:3d}%", end="", flush=True)

    if total > 0:
        print()  # newline after progress bar

    if sha256 and hasher and hasher.hexdigest() != sha256:
        _warn(f"SHA256 mismatch for {dest.name} (expected {sha256[:16]}...)")
        dest.unlink(missing_ok=True)
        return False

    return True


def _run_export(script_name: str) -> bool:
    """Run a Python export script from the scripts/ directory."""
    script = SCRIPT_DIR / script_name
    if not script.exists():
        _warn(f"{script_name} not found at {script}")
        return False
    print(f"  Running {script_name} ...")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _warn(f"{script_name} failed:\n{result.stderr[:500]}")
        return False
    return True


def _has_trtexec() -> bool:
    return shutil.which("trtexec") is not None


def _build_engine(
    onnx_path: Path,
    engine_path: Path,
    *,
    min_shapes: str,
    opt_shapes: str,
    max_shapes: str,
    workspace: int = 4096,
    build_script: str | None = None,
) -> bool:
    """Build a TensorRT FP16 engine from ONNX."""
    if build_script:
        script = SCRIPT_DIR / build_script
        if script.exists():
            print(f"  Building TensorRT engine via {build_script} ...")
            result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
            return result.returncode == 0

    if not _has_trtexec():
        _warn("trtexec not found — TensorRT engine will be built on first pipeline run")
        return False

    print(f"  Building TensorRT FP16 engine for {onnx_path.name} ...")
    cmd = [
        "trtexec",
        f"--onnx={onnx_path}",
        f"--saveEngine={engine_path}",
        "--fp16",
        f"--memPoolSize=workspace:{workspace}",
        f"--minShapes={min_shapes}",
        f"--optShapes={opt_shapes}",
        f"--maxShapes={max_shapes}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        _warn(f"trtexec failed:\n{result.stderr[:500]}")
        return False
    return True


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


def step_rfdetr() -> None:
    """Step 1: RF-DETR object detection model."""
    _header("[1/5] RF-DETR Object Detection Model")
    RFDETR_DIR.mkdir(parents=True, exist_ok=True)

    onnx_path = RFDETR_DIR / "rfdetr.onnx"
    engine_path = RFDETR_DIR / "rfdetr_fp16.engine"

    if not onnx_path.exists():
        _run_export("export_rfdetr_onnx.py")
    else:
        _ok("RF-DETR ONNX already exists")

    if not engine_path.exists() and onnx_path.exists():
        _build_engine(
            onnx_path,
            engine_path,
            min_shapes="input:1x3x640x640",
            opt_shapes="input:4x3x640x640",
            max_shapes="input:8x3x640x640",
            build_script="build_tensorrt_engine.sh",
        )
    elif engine_path.exists():
        _ok("RF-DETR TensorRT engine already exists")


def step_action_classifier() -> None:
    """Step 2: Action classifier SGIE model."""
    _header("[2/5] Action Classifier (MobileNet-V2)")
    ACTION_DIR.mkdir(parents=True, exist_ok=True)

    onnx_path = ACTION_DIR / "action_classifier.onnx"
    engine_path = ACTION_DIR / "action_classifier_fp16.engine"

    if not onnx_path.exists():
        _run_export("export_action_classifier_onnx.py")
    else:
        _ok("Action classifier ONNX already exists")

    if not engine_path.exists() and onnx_path.exists():
        _build_engine(
            onnx_path,
            engine_path,
            min_shapes="input:1x3x224x224",
            opt_shapes="input:16x3x224x224",
            max_shapes="input:32x3x224x224",
            workspace=2048,
            build_script="build_action_engine.sh",
        )
    elif engine_path.exists():
        _ok("Action classifier TensorRT engine already exists")


def step_piper_tts() -> None:
    """Step 3: Piper TTS model files."""
    _header("[3/5] Piper TTS (en_US-lessac-medium)")
    TTS_DIR.mkdir(parents=True, exist_ok=True)

    for filename, sha256 in PIPER_FILES:
        dest = TTS_DIR / filename
        if dest.exists():
            _ok(f"{filename} already exists")
        else:
            url = f"{PIPER_BASE_URL}/{filename}"
            _download(url, dest, sha256)


def step_paddleocr() -> None:
    """Step 4: PaddleOCR models (downloaded on first use)."""
    _header("[4/5] PaddleOCR (en_PP-OCRv4)")
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    print("  PaddleOCR models download on first use via the paddleocr Python package.")
    print("  Ensure the package is installed: pip install paddleocr")


def step_verify() -> None:
    """Step 5: Verify all model files exist."""
    _header("[5/5] Verification")
    print("  Checking model files...")

    files_to_check: list[tuple[Path, str]] = [
        (RFDETR_DIR / "rfdetr.onnx", "RF-DETR ONNX"),
        (RFDETR_DIR / "rfdetr_fp16.engine", "RF-DETR TensorRT FP16"),
        (ACTION_DIR / "action_classifier.onnx", "Action Classifier ONNX"),
        (ACTION_DIR / "action_classifier_fp16.engine", "Action Classifier TensorRT FP16"),
        (TTS_DIR / f"{PIPER_MODEL_NAME}.onnx", "Piper TTS Model"),
        (TTS_DIR / f"{PIPER_MODEL_NAME}.onnx.json", "Piper TTS Config"),
    ]

    missing = 0
    for path, label in files_to_check:
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            _ok(f"{label} ({size_mb:.1f} MB)")
        else:
            _fail(f"{label} -- {path}")
            missing += 1

    print()
    if missing > 0:
        print(f"  WARNING: {missing} model file(s) missing -- see above.")
    else:
        print("  All model files present.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    print("==========================================")
    print("  WasteWatch Model Setup")
    print("  Satyaki Solutions Pvt Ltd")
    print("==========================================")

    step_rfdetr()
    step_action_classifier()
    step_piper_tts()
    step_paddleocr()
    step_verify()

    print()
    print("==========================================")
    print("  Model setup complete!")
    print("==========================================")


if __name__ == "__main__":
    main()
