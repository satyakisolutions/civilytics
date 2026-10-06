# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Auto TensorRT engine builder — runs at pipeline init before GStreamer starts.

Checks if TensorRT engine files exist for all models. If any are missing,
builds them from ONNX using trtexec. Blocks pipeline startup until all
engines are ready.

Build order:
    1. RF-DETR (PGIE) — primary detector, ~5 min build
    2. Action Classifier (SGIE) — secondary classifier, ~2 min build

If ONNX files are also missing, attempts to export them first using the
export scripts (requires PyTorch).

Engine files are cached in the models/ directory and persist across
container restarts via Docker volume mounts.

Startup flow:
    entrypoint.py → engine_builder.ensure_engines() → pipeline.main()
                     │
                     ├─ RF-DETR engine exists?
                     │   YES → skip
                     │   NO  → ONNX exists?
                     │          YES → trtexec → engine
                     │          NO  → export_rfdetr_onnx.py → trtexec → engine
                     │
                     └─ Action classifier engine exists?
                         YES → skip
                         NO  → same flow
"""

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Base paths (relative to pipeline working directory)
MODELS_DIR = Path("models")
SCRIPTS_DIR = Path("/app/scripts") if Path("/app/scripts").exists() else Path("../scripts")


@dataclass
class EngineSpec:
    """Specification for a TensorRT engine to build."""
    name: str
    onnx_path: Path
    engine_path: Path
    export_script: str | None      # Python script to generate ONNX from model
    min_shapes: str                 # trtexec --minShapes
    opt_shapes: str                 # trtexec --optShapes
    max_shapes: str                 # trtexec --maxShapes
    workspace_mb: int = 4096
    fp16: bool = True


# Engine specifications for all models
ENGINES: list[EngineSpec] = [
    EngineSpec(
        # RF-DETR base exports ONNX at 560x560 with a static batch of 1.
        # FP16 destroys the DETR transformer decoder — attention layers
        # lose precision and all scores collapse to 0.06-0.12 noise.
        # FP32 produces correct scores (0.3-0.8 on real targets).
        name="RF-DETR (PGIE)",
        onnx_path=MODELS_DIR / "rfdetr" / "rfdetr.onnx",
        engine_path=MODELS_DIR / "rfdetr" / "rfdetr_fp32.engine",
        export_script="export_rfdetr_onnx.py",
        min_shapes="input:1x3x560x560",
        opt_shapes="input:1x3x560x560",
        max_shapes="input:1x3x560x560",
        workspace_mb=4096,
        fp16=False,
    ),
    EngineSpec(
        name="Action Classifier (SGIE)",
        onnx_path=MODELS_DIR / "action_classifier" / "action_classifier.onnx",
        engine_path=MODELS_DIR / "action_classifier" / "action_classifier_fp16.engine",
        export_script="export_action_classifier_onnx.py",
        min_shapes="input:1x3x224x224",
        opt_shapes="input:16x3x224x224",
        max_shapes="input:32x3x224x224",
        workspace_mb=2048,
    ),
]


def _check_trtexec() -> bool:
    """Check if trtexec is available."""
    try:
        result = subprocess.run(
            ["trtexec", "--help"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _check_gpu() -> bool:
    """Check if an NVIDIA GPU is accessible."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("GPU detected: %s", result.stdout.strip().split("\n")[0])
            return True
        return False
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _export_onnx(spec: EngineSpec) -> bool:
    """Run the ONNX export script for a model."""
    if not spec.export_script:
        return False

    script_path = SCRIPTS_DIR / spec.export_script
    if not script_path.exists():
        # Try relative to current working directory
        alt_path = Path("scripts") / spec.export_script
        if alt_path.exists():
            script_path = alt_path
        else:
            logger.error("Export script not found: %s", spec.export_script)
            return False

    logger.info("Exporting %s to ONNX...", spec.name)
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.error("ONNX export failed for %s:\n%s", spec.name, result.stderr[-500:])
            return False
        logger.info("ONNX export complete: %s", spec.onnx_path)
        return True
    except subprocess.TimeoutExpired:
        logger.error("ONNX export timed out for %s (>10 min)", spec.name)
        return False
    except Exception:
        logger.exception("ONNX export error for %s", spec.name)
        return False


def _build_engine(spec: EngineSpec) -> bool:
    """Build a TensorRT engine from ONNX using trtexec."""
    logger.info(
        "Building TensorRT engine for %s (this may take several minutes)...",
        spec.name,
    )
    logger.info("  ONNX:   %s", spec.onnx_path)
    logger.info("  Engine: %s", spec.engine_path)
    logger.info("  Shapes: min=%s opt=%s max=%s",
                spec.min_shapes, spec.opt_shapes, spec.max_shapes)

    # Ensure output directory exists
    spec.engine_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "trtexec",
        f"--onnx={spec.onnx_path}",
        f"--saveEngine={spec.engine_path}",
        f"--minShapes={spec.min_shapes}",
        f"--optShapes={spec.opt_shapes}",
        f"--maxShapes={spec.max_shapes}",
        f"--workspace={spec.workspace_mb}",
    ]
    if spec.fp16:
        cmd.append("--fp16")

    start = time.monotonic()
    try:
        # Stream output so user sees progress
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            line = line.rstrip()
            # Only log key progress lines (trtexec is very verbose)
            if any(kw in line.lower() for kw in ("building", "timing", "serializ", "error", "warning", "total")):
                logger.info("  trtexec: %s", line)

        process.wait(timeout=1200)  # 20 min hard cap

        elapsed = time.monotonic() - start
        if process.returncode == 0:
            size_mb = spec.engine_path.stat().st_size / (1024 * 1024) if spec.engine_path.exists() else 0
            logger.info(
                "Engine built: %s (%.1f MB, %.0fs)",
                spec.name, size_mb, elapsed,
            )
            return True
        else:
            logger.error("trtexec failed for %s (exit code %d)", spec.name, process.returncode)
            return False

    except subprocess.TimeoutExpired:
        process.kill()
        logger.error("trtexec timed out for %s (>20 min)", spec.name)
        return False
    except Exception:
        logger.exception("Engine build error for %s", spec.name)
        return False


def _engine_is_loadable(engine_path: Path) -> bool:
    """Deserialise *engine_path* to prove it is usable, not merely present.

    A size>0 check is not enough: an engine truncated by a full disk, a
    killed trtexec, or an interrupted container build is non-empty but
    invalid. DeepStream then fails at nvinfer load, which surfaces as a
    cryptic pipeline death at startup rather than a clear build error.

    Returns True if TensorRT accepts the file. If TensorRT's Python module
    is unavailable we cannot judge, so we return True and leave the
    existing size check as the only gate -- refusing to start on an engine
    we simply could not inspect would be worse than trying it.
    """
    try:
        import tensorrt as trt  # noqa: PLC0415 -- optional, container-only
    except ImportError:
        logger.debug("tensorrt module unavailable — skipping deserialise check")
        return True

    try:
        trt_logger = trt.Logger(trt.Logger.ERROR)
        with open(engine_path, "rb") as f:
            blob = f.read()
        runtime = trt.Runtime(trt_logger)
        engine = runtime.deserialize_cuda_engine(blob)
        if engine is None:
            logger.warning("Engine failed to deserialise: %s", engine_path)
            return False
        return True
    except Exception as exc:
        logger.warning("Engine deserialise raised %s: %s", type(exc).__name__, exc)
        return False


def ensure_engines(skip_missing_onnx: bool = False) -> dict[str, str]:
    """Check and build all required TensorRT engines.

    Called at pipeline startup before GStreamer initializes.

    Returns a dict of {engine_name: status} where status is one of:
        "ready"         — engine file exists
        "built"         — engine was built during this call
        "skipped"       — ONNX missing and skip_missing_onnx=True
        "no_gpu"        — no GPU available for building
        "no_trtexec"    — trtexec not installed
        "build_failed"  — build attempted but failed
        "onnx_missing"  — ONNX export failed or unavailable
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    logger.info("=" * 60)
    logger.info("  WasteWatch Engine Builder")
    logger.info("  Satyaki Solutions Pvt Ltd")
    logger.info("=" * 60)

    results: dict[str, str] = {}

    # Pre-checks
    has_gpu = _check_gpu()
    has_trtexec = _check_trtexec()

    if not has_gpu:
        logger.warning("No NVIDIA GPU detected — cannot build TensorRT engines")
    if not has_trtexec:
        logger.warning("trtexec not found — cannot build TensorRT engines")
        logger.warning("Install TensorRT or use the DeepStream Docker container")

    for spec in ENGINES:
        logger.info("")
        logger.info("--- %s ---", spec.name)

        # 1. Engine present AND loadable? Then skip. Present but corrupt
        #    (truncated by a full disk, a killed trtexec, an interrupted
        #    build) is quarantined and rebuilt below, because nvinfer would
        #    otherwise die at load with no useful message.
        if spec.engine_path.exists() and spec.engine_path.stat().st_size > 0:
            size_mb = spec.engine_path.stat().st_size / (1024 * 1024)
            if _engine_is_loadable(spec.engine_path):
                logger.info("Engine exists: %s (%.1f MB) — skipping", spec.engine_path, size_mb)
                results[spec.name] = "ready"
                continue
            logger.warning(
                "Engine present but NOT loadable: %s (%.1f MB) — rebuilding",
                spec.engine_path, size_mb,
            )
            try:
                # Move aside rather than delete, so a failed rebuild leaves
                # evidence of what was there for debugging.
                spec.engine_path.replace(spec.engine_path.with_suffix(
                    spec.engine_path.suffix + ".corrupt"))
            except OSError as exc:
                logger.warning("Could not quarantine corrupt engine: %s", exc)
                try:
                    spec.engine_path.unlink()
                except OSError:
                    logger.error("Cannot remove corrupt engine %s", spec.engine_path)
                    results[spec.name] = "corrupt_unwritable"
                    continue

        # 2. Check if we can build
        if not has_gpu:
            results[spec.name] = "no_gpu"
            continue
        if not has_trtexec:
            results[spec.name] = "no_trtexec"
            continue

        # 3. Check if ONNX exists, export if not
        if not spec.onnx_path.exists():
            logger.info("ONNX file missing: %s", spec.onnx_path)
            if skip_missing_onnx:
                logger.info("Skipping %s (skip_missing_onnx=True)", spec.name)
                results[spec.name] = "skipped"
                continue
            exported = _export_onnx(spec)
            if not exported or not spec.onnx_path.exists():
                logger.error("Cannot build %s — ONNX export failed", spec.name)
                results[spec.name] = "onnx_missing"
                continue

        # 4. Build engine
        built = _build_engine(spec)
        results[spec.name] = "built" if built else "build_failed"

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("  Engine Status Summary")
    logger.info("=" * 60)
    for name, status in results.items():
        icon = {
            "ready": "OK", "built": "BUILT", "skipped": "SKIP",
            "no_gpu": "NO GPU", "no_trtexec": "NO TRTEXEC",
            "build_failed": "FAILED", "onnx_missing": "NO ONNX",
        }.get(status, "???")
        logger.info("  [%s] %s", icon, name)
    logger.info("=" * 60)

    return results


# Allow running standalone: python -m app.engine_builder
if __name__ == "__main__":
    ensure_engines()
