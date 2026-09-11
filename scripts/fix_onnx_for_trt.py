#!/usr/bin/env python3
# Copyright (c) 2024-2026 Satyaki Solutions Pvt Ltd. All rights reserved.
"""Sanitise the RF-DETR and Action-Classifier ONNX exports so TensorRT
8.6 (DeepStream 7.0 base image) can successfully build FP16 engines.

Problems observed:

1. RF-DETR (PGIE) — `ScatterND` in the shape-tensor compute path:
       [graph.cpp::symbolicExecute::539] Error Code 4: Internal Error
       (/transformer/ScatterND: an IScatterLayer cannot be used to
       compute a shape tensor)
   Fix: fold the shape-tensor sub-graph to constants. `onnxsim` is
   usually sufficient because the ScatterND inputs are static in RF-DETR
   once the model is exported with a fixed input size. If onnxsim alone
   leaves the ScatterND in place, we fall back to `onnx-graphsurgeon`
   and inline the ScatterND as a Constant node computed from its inputs
   via numpy.

2. Action Classifier (SGIE) — reshape wildcard has no integer solution:
       [shapeContext.cpp::operator()::3602] Error Code 4: Shape Error
       (reshape wildcard -1 has no integer solution.
        Reshaping [1,1,1,1] to [-1,1280].)
   Fix: the dynamic reshape mark-up leaks into a pre-classifier Flatten
   because the Global Average Pool output shape is being propagated as
   [1,1,1,1] instead of [1,1280,1,1]. `onnxsim` with the correct
   input-shape hint folds this to a plain Reshape([1, 1280]).

This script runs in a plain `python:3.11-slim` container; nothing needs
to be installed on the host. Usage:

    docker run --rm \\
      -v "$PWD/pipeline/models":/models \\
      -v "$PWD/scripts":/work:ro \\
      -w /work \\
      python:3.11-slim \\
      bash -c "pip install --quiet onnx==1.17.0 onnxsim==0.4.36 \\
                                   onnx-graphsurgeon==0.5.2 numpy==1.26.4 && \\
               python fix_onnx_for_trt.py /models"

Writes:
  /models/rfdetr/rfdetr.onnx               (backed up to rfdetr.orig.onnx)
  /models/action_classifier/action_classifier.onnx
                                           (backed up to action_classifier.orig.onnx)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

try:
    import onnx
    from onnx import numpy_helper
    import onnxsim
    import numpy as np
    import onnx_graphsurgeon as gs
except ImportError as e:  # pragma: no cover
    print(f"ERROR: missing dependency: {e}")
    print("Install inside the container:")
    print("  pip install onnx==1.17.0 onnxsim==0.4.36 onnx-graphsurgeon==0.5.2 numpy==1.26.4")
    sys.exit(1)


def backup(path: Path) -> Path:
    """Create .orig backup beside `path` (once)."""
    backup_path = path.with_name(path.stem + ".orig" + path.suffix)
    if not backup_path.exists():
        shutil.copy2(path, backup_path)
        print(f"  backed up -> {backup_path.name}")
    else:
        print(f"  backup already exists ({backup_path.name})")
    return backup_path


def simplify_onnx(input_path: Path, input_shapes: dict[str, list[int]] | None) -> onnx.ModelProto:
    """Run onnxsim's `simplify` with optional static input shapes."""
    print(f"  loading: {input_path}")
    model = onnx.load(str(input_path))
    print(f"  opset version(s): {[(o.domain, o.version) for o in model.opset_import]}")

    kwargs = {}
    if input_shapes:
        kwargs["overwrite_input_shapes"] = input_shapes

    print(f"  simplifying (onnxsim)... input_shapes={input_shapes}")
    simplified, ok = onnxsim.simplify(model, **kwargs)
    if not ok:
        print("  WARNING: onnxsim reported failure — proceeding with unsimplified graph")
        return model
    return simplified


def count_op(model: onnx.ModelProto, op_type: str) -> int:
    return sum(1 for n in model.graph.node if n.op_type == op_type)


def inline_shape_tensor_scatternds(model: onnx.ModelProto) -> onnx.ModelProto:
    """Replace any remaining ScatterND nodes whose outputs feed Shape/
    Reshape operators with constant pre-computed tensors. Only triggered
    when all three inputs to the ScatterND are initializers (constants),
    which is the pattern TRT complains about in RF-DETR."""
    graph = gs.import_onnx(model)
    init_map = {t.name: t for t in graph.tensors().values() if isinstance(t, gs.Constant)}
    changed = 0

    for node in list(graph.nodes):
        if node.op != "ScatterND":
            continue
        # All three inputs must be constants for us to fold.
        if not all(isinstance(i, gs.Constant) for i in node.inputs):
            continue

        data = node.inputs[0].values.copy()
        indices = node.inputs[1].values
        updates = node.inputs[2].values

        # Numpy emulation of ScatterND for arbitrary rank.
        idx_last = indices.shape[-1]
        flat_indices = indices.reshape(-1, idx_last)
        flat_updates = updates.reshape(-1, *data.shape[idx_last:])
        for k in range(flat_indices.shape[0]):
            target = tuple(flat_indices[k].tolist())
            data[target] = flat_updates[k]

        out_name = node.outputs[0].name
        const = gs.Constant(name=out_name + "_folded", values=data.astype(data.dtype))
        # Rewire consumers of the ScatterND output to consume the new constant.
        for consumer in list(node.outputs[0].outputs):
            for i, inp in enumerate(consumer.inputs):
                if inp is node.outputs[0]:
                    consumer.inputs[i] = const
        node.outputs = []
        changed += 1

    if changed:
        graph.cleanup().toposort()
        print(f"  folded {changed} ScatterND node(s) into constants via graphsurgeon")
        return gs.export_onnx(graph)
    return model


def process_model(
    path: Path,
    input_shapes: dict[str, list[int]] | None,
    expect_no_scatternd: bool = False,
) -> None:
    print(f"\n== {path.name} ==")
    backup(path)

    model = simplify_onnx(path, input_shapes)
    print(f"  ops after onnxsim: ScatterND={count_op(model, 'ScatterND')}, "
          f"Reshape={count_op(model, 'Reshape')}, "
          f"Shape={count_op(model, 'Shape')}")

    if expect_no_scatternd and count_op(model, "ScatterND") > 0:
        model = inline_shape_tensor_scatternds(model)
        print(f"  ops after graphsurgeon: ScatterND={count_op(model, 'ScatterND')}")

    onnx.checker.check_model(model, full_check=False)
    onnx.save(model, str(path))
    print(f"  saved: {path}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fix_onnx_for_trt.py <models_root>")
        return 2
    models_root = Path(sys.argv[1])

    rfdetr_path = models_root / "rfdetr" / "rfdetr.onnx"
    action_path = models_root / "action_classifier" / "action_classifier.onnx"

    if not rfdetr_path.exists():
        print(f"ERROR: missing {rfdetr_path}")
        return 1
    if not action_path.exists():
        print(f"ERROR: missing {action_path}")
        return 1

    # RF-DETR base: 1x3x560x560 (standard export). If your export is 640x640,
    # adjust here — trtexec also passes 1x3x640x640 via min/opt/max in the
    # engine builder, so we pick the same opt size for the sanitisation pass.
    process_model(
        rfdetr_path,
        input_shapes={"input": [1, 3, 560, 560]},
        expect_no_scatternd=True,
    )

    # Action classifier: MobileNet-V2 crops, 1x3x224x224.
    process_model(
        action_path,
        input_shapes={"input": [1, 3, 224, 224]},
    )

    print("\nDone. Retry the TensorRT engine build:")
    print("  docker compose up -d pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
