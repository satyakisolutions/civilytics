# RF-DETR Model Files

Place the RF-DETR model files in this directory before running the pipeline.

## Required files

| File | Description |
|------|-------------|
| `rfdetr.onnx` | RF-DETR ONNX export |
| `rfdetr_fp16.engine` | TensorRT FP16 engine (built on target GPU) |

## Exporting the ONNX model

Use the export script provided in `/scripts/`:

```bash
python3 scripts/export_rfdetr_onnx.py \
    --weights path/to/rfdetr_weights.pth \
    --output pipeline/models/rfdetr/rfdetr.onnx \
    --input-size 640
```

## Building the TensorRT engine

After exporting the ONNX model, build the TensorRT FP16 engine on the target GPU
using the build script in `/scripts/`:

```bash
python3 scripts/build_trt_engine.py \
    --onnx pipeline/models/rfdetr/rfdetr.onnx \
    --output pipeline/models/rfdetr/rfdetr_fp16.engine \
    --fp16 \
    --batch-size 4 \
    --input-shape 3 640 640
```

Alternatively, use `trtexec` directly (available inside the DeepStream container):

```bash
trtexec \
    --onnx=models/rfdetr/rfdetr.onnx \
    --saveEngine=models/rfdetr/rfdetr_fp16.engine \
    --fp16 \
    --minShapes=input:1x3x640x640 \
    --optShapes=input:4x3x640x640 \
    --maxShapes=input:4x3x640x640
```

The engine must be built on the same GPU model that will run inference — it is not
portable across GPU architectures.
