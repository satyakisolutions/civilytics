#!/usr/bin/env python3
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Standalone ONNX -> TensorRT engine converter for the LSG pipeline.

Replaces the scattered "export script + engine builder + manual trtexec"
dance with a single tool you can run one-shot:

    docker compose run --rm onnx-to-engine             # convert all models
    docker compose run --rm onnx-to-engine rfdetr      # convert one model
    docker compose run --rm onnx-to-engine --force     # rebuild even if engine exists

For each registered model this script:

  1. Reads the ONNX file and discovers its actual input dtype+shape.
  2. Runs onnxsim with static input shapes so TRT 8.6's parser can fold
     ScatterND/Shape/Reshape off the symbolic-execution path. (RF-DETR's
     default export has two ScatterND nodes on the shape-tensor graph
     that TRT 8.6 flatly refuses — the action classifier has a dynamic
     Flatten that shape inference fails to resolve.)
  3. Falls back to inline-folding any remaining ScatterND nodes whose
     inputs are all initializers, via onnx-graphsurgeon.
  4. Writes the sanitised ONNX in place (backup goes to `<name>.orig.onnx`
     on first run).
  5. Invokes `trtexec` with min/opt/max shapes that actually match the
     ONNX input (no more 560-vs-640 mismatches that silently corrupt the
     position embedding).
  6. Saves the engine to the path DeepStream expects.

This script runs inside the LSG `pipeline` image, which already ships
CUDA + TensorRT 8.6 + trtexec. The image also needs onnx / onnxsim /
onnx-graphsurgeon on the Python side — those are listed in
pipeline/requirements.txt.

Exit codes:
  0 — all requested models converted successfully
  1 — one or more conversions failed
  2 — bad CLI usage
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# Where the pipeline image mounts project files.
REPO_ROOT = Path("/app")
MODELS_DIR = REPO_ROOT / "models"
TRTEXEC = "/usr/src/tensorrt/bin/trtexec"  # path inside nvcr deepstream image


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    key: str
    display_name: str
    onnx_rel: str           # relative to MODELS_DIR
    engine_rel: str         # relative to MODELS_DIR
    # trtexec min/opt/max. %H and %W substituted from the ONNX input
    # shape on the fly so the engine matches the actual graph.
    min_batch: int
    opt_batch: int
    max_batch: int
    workspace_mb: int
    fp16: bool = True
    # Disable constant-fold-based pruning on this particular model
    # (onnxsim's default) to avoid removing weights that RF-DETR's
    # tokenwise logic relies on.
    skip_constant_folding: bool = False


MODELS: list[ModelSpec] = [
    ModelSpec(
        key="rfdetr",
        display_name="RF-DETR (PGIE)",
        onnx_rel="rfdetr/rfdetr.onnx",
        engine_rel="rfdetr/rfdetr_fp16.engine",
        min_batch=1, opt_batch=1, max_batch=1,
        workspace_mb=4096,
        fp16=True,
    ),
    ModelSpec(
        key="action",
        display_name="Action Classifier (SGIE)",
        onnx_rel="action_classifier/action_classifier.onnx",
        engine_rel="action_classifier/action_classifier_fp16.engine",
        min_batch=1, opt_batch=16, max_batch=32,
        workspace_mb=2048,
        fp16=True,
    ),
]


# ---------- utilities -----------------------------------------------------

def log(msg: str) -> None:
    print(f"[onnx_to_engine] {msg}", flush=True)


def read_onnx_input(onnx_path: Path) -> tuple[str, list[int], list[bool]]:
    """Return (input_name, dims, is_symbolic_per_dim).

    Symbolic dims (the `batch_size` style named params) are pinned to 1
    so we can still feed them to trtexec's shape flags. The caller gets
    a parallel bool list so it can decide whether any shape flag is
    needed at all — TRT 8.6 rejects `--minShapes` on a fully-static
    graph with "Static model does not take explicit shapes".
    """
    import onnx

    model = onnx.load(str(onnx_path))
    i = model.graph.input[0]
    name = i.name
    dims: list[int] = []
    symbolic: list[bool] = []
    for d in i.type.tensor_type.shape.dim:
        if d.dim_value and d.dim_value > 0:
            dims.append(d.dim_value)
            symbolic.append(False)
        else:
            dims.append(1)
            symbolic.append(True)
    return name, dims, symbolic


def count_ops(onnx_path: Path) -> dict[str, int]:
    import onnx

    model = onnx.load(str(onnx_path))
    counts: dict[str, int] = {}
    for node in model.graph.node:
        counts[node.op_type] = counts.get(node.op_type, 0) + 1
    return counts


def sanitise_onnx(spec: ModelSpec, onnx_path: Path) -> None:
    """Simplify the ONNX with static input shapes + graphsurgeon
    fallback."""
    import onnx
    import onnxsim
    import onnx_graphsurgeon as gs

    name, dims, _symbolic = read_onnx_input(onnx_path)
    # Force batch to min_batch=1 for the sanitise pass so TRT's shape
    # inference sees a fully concrete graph. This matters for
    # MobileNet-V2 (action classifier) where onnxsim + TRT otherwise
    # guess `1` for the channel dim of the post-GAP `mean` tensor and
    # explode at the `.view(-1, 1280)` Reshape. trtexec can still
    # stretch batch back out via its own min/opt/max profiles.
    pinned = dims[:]
    if pinned and pinned[0] <= 0:
        pinned[0] = max(1, spec.min_batch)
    log(f"{spec.display_name}: input={name} dims={pinned}")

    before = count_ops(onnx_path)
    log(f"  ops before: ScatterND={before.get('ScatterND', 0)} "
        f"Shape={before.get('Shape', 0)} Reshape={before.get('Reshape', 0)}")

    model = onnx.load(str(onnx_path))
    simplified, ok = onnxsim.simplify(
        model,
        overwrite_input_shapes={name: pinned},
        # RF-DETR needs constant folding fully enabled because that is
        # what collapses the ScatterND-on-shape path. We only expose
        # skip_constant_folding for future models that can't tolerate it.
        skip_constant_folding=spec.skip_constant_folding,
    )
    if not ok:
        log("  WARNING: onnxsim reported failure — keeping unsimplified graph")
    else:
        model = simplified

    # Run ONNX shape inference so downstream graphsurgeon passes can see
    # the propagated shapes. Non-fatal on failure — some ops from newer
    # opsets don't roundtrip cleanly, but we can still fold Reshapes
    # whose target is a numeric constant chain.
    try:
        from onnx import shape_inference
        model = shape_inference.infer_shapes(model, strict_mode=False, data_prop=True)
    except Exception as e:  # pragma: no cover
        log(f"  shape_inference skipped: {e}")

    # --- Fallback 1: inline ScatterND whose inputs are all constants.
    graph = gs.import_onnx(model)
    folded = 0
    for node in list(graph.nodes):
        if node.op != "ScatterND":
            continue
        if not all(isinstance(i, gs.Constant) for i in node.inputs):
            continue
        data = node.inputs[0].values.copy()
        indices = node.inputs[1].values
        updates = node.inputs[2].values
        idx_last = indices.shape[-1]
        flat_indices = indices.reshape(-1, idx_last)
        flat_updates = updates.reshape(-1, *data.shape[idx_last:])
        for k in range(flat_indices.shape[0]):
            target = tuple(flat_indices[k].tolist())
            data[target] = flat_updates[k]
        const = gs.Constant(name=node.outputs[0].name + "_folded", values=data)
        for consumer in list(node.outputs[0].outputs):
            for i, inp in enumerate(consumer.inputs):
                if inp is node.outputs[0]:
                    consumer.inputs[i] = const
        node.outputs = []
        folded += 1
    if folded:
        log(f"  graphsurgeon: folded {folded} residual ScatterND node(s)")

    # --- Fallback 2: ReduceMean / ReduceSum / ReduceMax / ReduceMin with
    # a constant axes input tensor (opset-13+ form) -> convert to the
    # legacy axes-attribute form (opset-11). TensorRT 8.6's ONNX parser
    # silently mis-propagates shapes through the input-tensor form for
    # Reduce ops, which collapses the downstream `mean -> view` pattern
    # in MobileNet-V2 classifier heads to `[1,1,1,1]` and breaks the
    # `.view(-1, 1280)` Reshape. This is specifically what hits the
    # LSG action classifier.
    reduce_converted = 0
    for node in list(graph.nodes):
        if node.op not in ("ReduceMean", "ReduceSum", "ReduceMax", "ReduceMin", "ReduceL2", "ReduceL1"):
            continue
        if len(node.inputs) < 2:
            continue  # already legacy-form (axes is an attribute)
        axes_input = node.inputs[1]
        if not isinstance(axes_input, gs.Constant):
            continue  # dynamic axes — leave alone
        axes_values = [int(v) for v in axes_input.values.tolist()]
        # Strip the second input and set axes as an attribute on the node.
        node.inputs = node.inputs[:1]
        node.attrs["axes"] = axes_values
        reduce_converted += 1
    if reduce_converted:
        log(f"  graphsurgeon: converted {reduce_converted} Reduce* op(s) to axes-attribute form")

    # --- Fallback 3: freeze every Reshape whose target shape tensor can
    # be evaluated to a concrete numpy array. Side-steps residual
    # TRT 8.6 shape-inference brittleness for patterns that survive the
    # ReduceMean fix.
    froze = 0
    for node in list(graph.nodes):
        if node.op != "Reshape":
            continue
        if len(node.inputs) < 2:
            continue
        shape_input = node.inputs[1]
        if isinstance(shape_input, gs.Constant):
            continue  # already concrete
        resolved = _resolve_constant_chain(shape_input)
        if resolved is None:
            continue
        const = gs.Constant(name=shape_input.name + "_frozen", values=resolved.astype("int64"))
        node.inputs[1] = const
        froze += 1
    if froze:
        log(f"  graphsurgeon: froze {froze} dynamic Reshape target shape(s)")

    if folded or froze or reduce_converted:
        graph.cleanup().toposort()
        model = gs.export_onnx(graph)
        # Our Reduce-op attribute conversion targets opset<=13 semantics
        # while the rest of the graph may still be opset 18. That is a
        # TRT-parser-friendly hybrid that `onnx.checker.check_model`
        # legitimately rejects under strict schema rules. Skip the
        # strict checker in that case — trtexec will catch anything
        # the parser actually can't consume.
        skip_check = reduce_converted > 0
    else:
        skip_check = False

    if not skip_check:
        try:
            onnx.checker.check_model(model, full_check=False)
        except Exception as e:  # pragma: no cover
            log(f"  checker warning: {e}")

    onnx.save(model, str(onnx_path))

    after = count_ops(onnx_path)
    log(f"  ops after : ScatterND={after.get('ScatterND', 0)} "
        f"Shape={after.get('Shape', 0)} Reshape={after.get('Reshape', 0)}")


def _resolve_constant_chain(tensor) -> "np.ndarray | None":
    """Attempt to evaluate `tensor` as a numpy array by walking upstream
    through nodes whose inputs are either Constants or other resolvable
    outputs. Returns None if the chain includes any non-constant or
    unsupported op.

    Supports the small set of shape-computing ops that typically appear
    in PyTorch ONNX exports: Concat, Reshape, Cast, Unsqueeze, Squeeze,
    Shape, Gather, Slice, Constant, ConstantOfShape, Mul, Div, Add, Sub.
    """
    import numpy as np
    import onnx_graphsurgeon as gs

    def _eval(t):
        if isinstance(t, gs.Constant):
            return np.asarray(t.values)
        if t.inputs and len(t.inputs) == 1:
            producer = t.inputs[0]
            op = producer.op
            try:
                if op == "Constant":
                    return np.asarray(producer.attrs.get("value").values)
                if op == "Concat":
                    axis = int(producer.attrs.get("axis", 0))
                    parts = [_eval(i) for i in producer.inputs]
                    if any(p is None for p in parts):
                        return None
                    return np.concatenate(parts, axis=axis)
                if op == "Cast":
                    val = _eval(producer.inputs[0])
                    if val is None:
                        return None
                    return val  # dtype detail doesn't matter for shape resolution
                if op == "Unsqueeze":
                    val = _eval(producer.inputs[0])
                    if val is None:
                        return None
                    axes = producer.attrs.get("axes")
                    if axes is None and len(producer.inputs) > 1:
                        axes = _eval(producer.inputs[1])
                        if axes is None:
                            return None
                        axes = axes.tolist()
                    for a in sorted(axes):
                        val = np.expand_dims(val, axis=int(a))
                    return val
                if op == "Squeeze":
                    val = _eval(producer.inputs[0])
                    if val is None:
                        return None
                    return np.squeeze(val)
                if op == "Gather":
                    data = _eval(producer.inputs[0])
                    idx = _eval(producer.inputs[1])
                    if data is None or idx is None:
                        return None
                    axis = int(producer.attrs.get("axis", 0))
                    return np.take(data, idx, axis=axis)
                if op == "Shape":
                    # Only resolvable if the source tensor has a static shape.
                    src = producer.inputs[0]
                    if src.shape is None or any(s is None or (isinstance(s, str)) for s in src.shape):
                        return None
                    return np.asarray(list(src.shape), dtype=np.int64)
                if op == "Mul":
                    a = _eval(producer.inputs[0]); b = _eval(producer.inputs[1])
                    return None if a is None or b is None else a * b
                if op == "Add":
                    a = _eval(producer.inputs[0]); b = _eval(producer.inputs[1])
                    return None if a is None or b is None else a + b
                if op == "Sub":
                    a = _eval(producer.inputs[0]); b = _eval(producer.inputs[1])
                    return None if a is None or b is None else a - b
                if op == "Div":
                    a = _eval(producer.inputs[0]); b = _eval(producer.inputs[1])
                    return None if a is None or b is None else a // b
            except Exception:
                return None
        return None

    val = _eval(tensor)
    if val is None:
        return None
    return val


def build_engine(spec: ModelSpec, onnx_path: Path, engine_path: Path) -> bool:
    """Invoke trtexec with shapes that actually match the ONNX input."""
    name, dims, symbolic = read_onnx_input(onnx_path)
    if len(dims) != 4:
        log(f"  ERROR: expected 4-D input for {spec.display_name}, got {dims}")
        return False
    _, c, h, w = dims

    def mk(batch: int) -> str:
        return f"{name}:{batch}x{c}x{h}x{w}"

    engine_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        TRTEXEC,
        f"--onnx={onnx_path}",
        f"--saveEngine={engine_path}",
        f"--memPoolSize=workspace:{spec.workspace_mb}",
    ]
    # Only emit --minShapes/--optShapes/--maxShapes if the ONNX actually
    # has at least one dynamic dim. Fully-static models (e.g. RF-DETR
    # after onnxsim pinned batch=1,560,560) make TRT 8.6 reject the
    # explicit-shape flags with "Static model does not take explicit
    # shapes since the shape of inference tensors will be determined by
    # the model itself".
    if any(symbolic):
        cmd += [
            f"--minShapes={mk(spec.min_batch)}",
            f"--optShapes={mk(spec.opt_batch)}",
            f"--maxShapes={mk(spec.max_batch)}",
        ]
    else:
        log("  ONNX is fully static — skipping --minShapes/--optShapes/--maxShapes")
    if spec.fp16:
        cmd.append("--fp16")

    log(f"  trtexec: {' '.join(cmd)}")
    t0 = _now()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = _now() - t0
    if result.returncode != 0:
        log(f"  trtexec FAILED ({elapsed:.1f}s, exit {result.returncode})")
        tail = (result.stderr or result.stdout).splitlines()[-30:]
        for line in tail:
            log(f"    {line}")
        return False

    log(f"  built {engine_path.name} in {elapsed:.1f}s")
    return True


def _now() -> float:
    import time
    return time.monotonic()


def convert(spec: ModelSpec, force: bool) -> bool:
    log(f"=== {spec.display_name} ===")
    onnx_path = MODELS_DIR / spec.onnx_rel
    engine_path = MODELS_DIR / spec.engine_rel

    if not onnx_path.exists():
        log(f"  MISSING ONNX: {onnx_path}")
        return False

    if engine_path.exists() and not force:
        log(f"  engine already exists — skipping (use --force to rebuild): {engine_path.name}")
        return True

    # Backup + sanitise (once).
    # Use shutil.copy (not copy2) everywhere — copy2 tries to preserve
    # metadata via utime(), which fails on bind-mounted Linux FS dirs
    # when the host mount disables nanosecond precision.
    backup = onnx_path.with_name(onnx_path.stem + ".orig" + onnx_path.suffix)
    if not backup.exists():
        shutil.copy(onnx_path, backup)
        log(f"  backed up original -> {backup.name}")
    else:
        log(f"  using existing backup {backup.name}")

    try:
        sanitise_onnx(spec, onnx_path)
    except Exception as e:  # pragma: no cover
        log(f"  sanitise FAILED: {e}")
        # Roll back to the backup so a subsequent run can retry cleanly.
        try:
            shutil.copy(backup, onnx_path)
        except Exception as rollback_err:
            log(f"  rollback failed too: {rollback_err}")
        return False

    return build_engine(spec, onnx_path, engine_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "models",
        nargs="*",
        help=f"Model keys to convert (default: all). Options: {', '.join(m.key for m in MODELS)}",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Rebuild even if the engine file already exists.",
    )
    args = parser.parse_args()

    if args.models:
        keys = set(args.models)
        unknown = keys - {m.key for m in MODELS}
        if unknown:
            print(f"unknown model keys: {sorted(unknown)}", file=sys.stderr)
            return 2
        selected = [m for m in MODELS if m.key in keys]
    else:
        selected = list(MODELS)

    if not TRTEXEC_exists():
        print(f"trtexec not found at {TRTEXEC} — run inside the pipeline image", file=sys.stderr)
        return 1

    log(f"models root: {MODELS_DIR}")
    log(f"targets    : {[m.display_name for m in selected]}")

    ok = 0
    for spec in selected:
        if convert(spec, force=args.force):
            ok += 1

    log("")
    log(f"summary: {ok}/{len(selected)} converted successfully")
    return 0 if ok == len(selected) else 1


def TRTEXEC_exists() -> bool:
    return os.path.exists(TRTEXEC) and os.access(TRTEXEC, os.X_OK)


if __name__ == "__main__":
    sys.exit(main())
