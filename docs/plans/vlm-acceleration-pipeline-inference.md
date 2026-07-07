# VLM Acceleration + In-Pipeline Inference — Implementation Plan

**Owner:** neeraj-satyaki
**Status:** EXECUTING — Phase 0.1 done; Phase 1 in progress (gRPC trigger + bbox crop landed, commit f10ebcd)
**Target stack:** RTX 3060 12GB, DeepStream 7.0, vLLM/TRT-LLM, Qwen2.5-VL

## Goals

1. Cut VLM RTT (currently 5-8s ENTER→alert wall clock).
2. Fix wrong-target firing — VLM must see only escalated track.
3. Move VLM inference into DeepStream pipeline (zero host copy).
4. Add primary-secondary GIE cascade: object detection (PGIE rfdetr) → action recognition (SGIE-A MoViNet) → action confirmation (SGIE-B Qwen2.5-VL-3B).

## Architecture (target end-state)

```
RTSP → nvurisrcbin → nvstreammux → nvinfer (PGIE rfdetr 2-class)
   → nvtracker
   → nvinfer (SGIE-A MoViNet-A0 6-class action)
   → nvinfer (SGIE-B Qwen2.5-VL-3B; gated on action ∈ {dropping,dumping})
   → nvosd → kafka.events
```

SGIE-B emits `NVDS_USER_VLM_META` carrying: action, confidence, deterrent_message, is_dumping.

---

## Phase 0.1 — vLLM speculative decode + ENTER trigger ✅ DONE

**Changes:**
- `pipeline/app/event_fsm.py:49` — added `vlm_fired: bool = False` one-shot guard.
- `pipeline/app/pipeline.py:767-833` — VLM fires on FSM ENTER (was VIOLATION). VIOLATION is fallback only if ENTER fire skipped.
- `docker-compose.yml:340` — vLLM ngram speculative decoding flag.

**Why ngram (not draft model):** vLLM draft-model spec-decode has compatibility caveats with VLM targets. ngram = no extra VRAM, free 1.2-1.5× win on common token suffixes.

**Validation:** restart `vlm` container, trigger 5 violations, measure `Analyze done t=...ms` log + ENTER→alert wall clock vs baseline.

---

## Phase 1 — Bbox crop in Python probe (1 day)

**Goal:** Fix wrong-target firing. Cut payload + vision tokens.

**Files:**
- `pipeline/app/pipeline.py:capture_frame_probe` — cache full RGBA ndarray, not pre-encoded JPEG.
- `pipeline/app/pipeline.py:extract_metadata` ENTER block — crop on bbox + 30% margin, JPEG inline.
- `backend/protos/vlm.proto` — add `crop_bbox: BoundingBox` field.
- `backend/app/vlm/grpc_server.py` — drop existing `_crop_to_bbox` re-crop.
- `backend/app/vlm/prompts.py` — adjust system prompt for crop context.

**Sketch:**
```python
_full_rgba = ctx["frame_rgba_cache"].get(frame_meta.source_id)  # h×w×4
if _full_rgba is not None:
    H, W = _full_rgba.shape[:2]
    bx, by, bw, bh = bbox_dict["x"], bbox_dict["y"], bbox_dict["w"], bbox_dict["h"]
    mx, my = bw * 0.30, bh * 0.30
    cx1 = max(0, int((bx - mx) * W)); cy1 = max(0, int((by - my) * H))
    cx2 = min(W, int((bx + bw + mx) * W)); cy2 = min(H, int((by + bh + my) * H))
    crop = _full_rgba[cy1:cy2, cx1:cx2, :3]
    img = Image.fromarray(crop)
    out = io.BytesIO(); img.save(out, format="JPEG", quality=85)
    _jpeg = out.getvalue()
```

**Exit gate:** wrong-target rate 0% on 20-sample violations. Payload ~10-30KB (was ~80KB).

**Risks:** crop too tight loses scene context for deterrent reasoning. Margin 30% is start; tune per data.

---

## Phase 2 — GPU crop kernel (3-5 days, cuda-kernel-optimizer skill)

**Goal:** Eliminate host-side PIL encode (~30-80ms/frame).

**Files (new):**
- `pipeline/cuda/crop_baseline.cu` — naive 1-thread/pixel
- `pipeline/cuda/ref_crop.py` — torch reference (skill validation contract)
- `pipeline/cuda/python_bind.cpp` — pybind11 binding
- `pipeline/cuda/Makefile` — sm_86 build

**Skill invocation:**
```bash
python ~/.claude/skills/cuda-kernel-optimizer/scripts/state.py init \
  --baseline ./pipeline/cuda/crop_baseline.cu \
  --ref      ./pipeline/cuda/ref_crop.py \
  --iterations 3 --branches 4 \
  --dims '{"IN_W":1920,"IN_H":1080,"BBOX_W":640,"BBOX_H":640}'
```

**Expected wins per iter:**
- Iter 1: vectorized `uchar4` loads → 2-3×
- Iter 2: shared mem tile of source patch → 1.5×
- Iter 3: warp shuffle bilinear → 1.2×

**Integration:** replace PIL block in `capture_frame_probe` with binding call → bytes.

**Exit gate:** crop+encode latency < 2ms (was 30-80ms).

---

## Phase 3 — TRT-LLM Qwen2.5-VL-3B engine (3-5 days)

**Goal:** Replace vLLM HTTP with TensorRT-LLM C++ runtime. Faster TTFT, lower VRAM.

**Files (new):**
- `backend/services/vlm-trt/Dockerfile` — TensorRT-LLM base, sm_86
- `backend/services/vlm-trt/build_engine.py` — Qwen2.5-VL-3B-AWQ → vision + LLM engines
- `backend/services/vlm-trt/server.cpp` — gRPC server matching `vlm.proto`
- `backend/services/vlm-trt/CMakeLists.txt`

**Files modify:**
- `docker-compose.yml` — replace `vlm` service with `vlm-trt`

**Build steps:**
1. Clone TensorRT-LLM at version supporting Qwen2.5-VL (≥0.14)
2. `convert_checkpoint.py --model_dir Qwen/Qwen2.5-VL-3B-Instruct-AWQ`
3. `trtllm-build --gemm_plugin auto --use_paged_context_fmha enable`
4. Build vision encoder engine separately
5. Wrap in C++ executor + tokenizer

**Spec-decode in TRT-LLM:**
```
--speculative_decoding_mode draft_target
--draft_model_dir <Qwen2.5-0.5B-engine>
```

**Exit gate:** TTFT <250ms, total <800ms on 640×640 crop. JSON output parses.

**Risks:**
- Verify Qwen2.5-VL TRT-LLM support maturity before committing
- VRAM: vision ~2GB + LLM ~3GB + draft ~1GB = ~6GB; fits with RFDETR (2GB) + MoViNet (1GB) on 12GB
- Engine rebuild on resolution change

---

## Phase A — MoViNet-A0 stream training (parallel to Phase 3)

### A1 — Auto-label clips (2 days)

**Files (new):**
- `training/clip_extractor.py` — slice 6 LSG videos into 1-2s clips around track ENTER events (from existing Kafka events log)
- `training/auto_label.py` — center-frame VLM call: classify {idle, walking, carrying, dropping, dumping, picking_up}; threshold 0.7
- `training/datasets/actions/{class}/clip_<id>.mp4` — output

**Source mix:**
- 6 LSG videos × ~5min @ 25fps → ~1800 1s clips
- Kinetics-400 negatives: `putting_in_garbage`, `washing_hands`, `walking_the_dog` (~500 clips)
- Manual review queue for clips with confidence <0.7 + all dropping/dumping (rare)

### A2 — Train MoViNet-A0 stream (3 days)

**File (new):** `training/train_movinet.py`

**Setup:**
- TF-Keras `movinet_a0_stream`, input `(1,1,224,224,3)` + state buffers
- 6-class output head
- Focal loss (rare-class bias)
- Aug: temporal crop, color jitter
- Target: val acc 80%+, dropping+dumping recall ≥85%

### A3 — Export ONNX → TRT engine (1 day)

**Files (new):**
- `training/export_movinet_onnx.py` — `tf2onnx` with state inputs/outputs preserved
- `pipeline/models/movinet/build.sh` — `trtexec --onnx=movinet_a0_stream.onnx --fp16 --saveEngine=movinet.engine`
- `pipeline/configs/labels_action.txt` — 6-line class file

### A4 — SGIE-A nvinfer config + custom parse lib (3 days)

**Files (new):**
- `pipeline/configs/nvinfer_movinet.txt`:
  ```ini
  process-mode=2
  network-type=1
  operate-on-gie-id=1
  operate-on-class-ids=0;1
  classifier-async-mode=1
  classifier-threshold=0.5
  input-object-min-width=64
  input-object-min-height=64
  custom-lib-path=/app/lib/libnvds_movinet_parse.so
  parse-classifier-func-name=NvDsInferParseMoVinet
  model-engine-file=/app/models/movinet/movinet.engine
  labelfile-path=/app/configs/labels_action.txt
  ```
- `pipeline/nvdsinfer_custom_impl_MoVinet/parse.cpp` — implements `NvDsInferParseMoVinet`:
  - Per-track ring buffer of last 4 cropped tensors (track_id keyed map, mutex-guarded)
  - Stitch + run engine inference
  - Argmax → class_id
  - Append `NvDsLabelInfo` to `NvDsObjectMeta.classifier_meta_list`
- `pipeline/nvdsinfer_custom_impl_MoVinet/Makefile`
- `pipeline/nvdsinfer_custom_impl_MoVinet/ring_buffer.hpp`

**Files modify:**
- `pipeline/Dockerfile` — link custom impl lib

**Pipeline insertion:** after `nvtracker`, before any FSM probe.

**Exit gate:** SGIE-A fires per track without stalling. Live class accuracy >75%.

**Hard problem — stateful streaming in SGIE:**
SGIE doesn't support stateful classifiers natively. Two options:
- **(a)** Drop streaming, use 4-frame stack input. Ring buffer per track in parse lib.
- **(b)** Custom GST plugin (heavier work).

Default to **(a)** — ring buffer of last 4 cropped tensors per track.

---

## Phase 4 — SGIE-B Qwen2.5-VL-3B as classifier (5-7 days)

**Goal:** Fire only when SGIE-A predicts dropping/dumping. Output: action confirmation + deterrent_message.

**Files (new):**
- `pipeline/configs/nvinfer_vlm.txt` — SGIE config (`network-type=100`, custom parse)
- `pipeline/nvdsinfer_custom_impl_VLM/parse.cpp`:
  - `pre_inference`: serialize crop tensor to TRT-LLM input, build prompt
  - `post_inference`: read text output, parse JSON `{action, confidence, deterrent_msg}`, write `NVDS_USER_VLM_META`
- `pipeline/nvdsinfer_custom_impl_VLM/vlm_meta.hpp` — custom NvDs meta type registration
- `pipeline/nvdsinfer_custom_impl_VLM/trtllm_client.cpp` — link TRT-LLM lib from Phase 3
- `pipeline/nvdsinfer_custom_impl_VLM/Makefile`

**Filter on SGIE-A class:**
SGIE→SGIE chaining filter behavior must be verified. If `operate-on-gie-id=2` + `operate-on-class-ids=3;4` not supported on classifier-output classes, gate inside parse lib pre-inference: read PGIE label + SGIE-A `classifier_meta_list`, skip if action ∉ {3,4}.

**Async dispatch:** VLM ~500ms+ blocks downstream. `classifier-async-mode=1` + result lands on later frame's NvDsObjectMeta. Correlate via `track_session_id`.

**Exit gate:** End-to-end FSM ENTER → TTS audio out <2s.

---

## Phase 5 — Wire alerts to SGIE-B meta (2 days)

**Files modify:**
- `pipeline/app/pipeline.py` — replace `vlm_client.submit()` with `NVDS_USER_VLM_META` reads
- `backend/app/services/alert_consumer.py` — read deterrent_message + is_dumping from pipeline-emitted Kafka events (drop separate vlm.results topic)
- `backend/app/vlm/grpc_server.py` — deprecate (keep as fallback OR delete)
- `docker-compose.yml` — remove standalone `vlm` / `vlm-trt` service (now in pipeline)

**Final data flow:**
```
nvinfer (rfdetr) → nvtracker → SGIE-A (movinet) → SGIE-B (qwen-vl) → osd → kafka.events
                                                     │
                                                     └─ NvDsUserMeta {
                                                          action, is_dumping, deterrent_message,
                                                          confidence, latency_ms
                                                        }

alert_consumer reads kafka.events with embedded VLM fields → alerts API → ws/tts
```

---

## Cumulative effort + ordering

| Phase | Effort | Cumulative | Cuts |
|--|--|--|--|
| 0.1 ✅ | 0.5d | 0.5d | TTFT 1.2-1.5×, ENTER→alert −3 to −5s |
| 1 | 1d | 1.5d | wrong-target fixed; payload −70% |
| 2 | 3-5d | ~6d | host crop latency −95% |
| 3 | 3-5d | ~10d | TTFT 2-3× |
| A1 | 2d | parallel | training data |
| A2 | 3d | parallel | trained classifier |
| A3 | 1d | parallel | TRT engine |
| A4 | 3d | ~13d | SGIE-A live |
| 4 | 5-7d | ~20d | SGIE-B live |
| 5 | 2d | ~22d | unified flow |

## Decision gates

- **After Phase 1:** if wrong-target = 0% AND latency <2s → **stop** (defer 2-5).
- **After Phase 2:** if latency <1.5s → **stop** (defer 3-5).
- **After Phase 3:** if no need for action gating → **stop** (defer A+B).
- A & B are coupled — A standalone has no use without B.

## Open risks

1. Qwen2.5-VL TRT-LLM support maturity — verify in Phase 3 first hour.
2. MoViNet streaming state in SGIE — may force custom plugin (Phase 4 expansion).
3. SGIE→SGIE chaining filter behavior — DeepStream config to verify.
4. VRAM budget on 12GB 3060 — RFDETR 2GB + MoViNet 1GB + Qwen-VL-3B 6GB + draft 1GB + KV cache = tight.
5. Kinetics-400 noise + auto-label bias — manual review on dump class essential.

## Files to create (full list)

```
training/
  clip_extractor.py
  auto_label.py
  train_movinet.py
  export_movinet_onnx.py
  datasets/actions/{idle,walking,carrying,dropping,dumping,picking_up}/

pipeline/cuda/
  crop_baseline.cu
  ref_crop.py
  python_bind.cpp
  Makefile

pipeline/nvdsinfer_custom_impl_MoVinet/
  parse.cpp
  Makefile
  ring_buffer.hpp

pipeline/nvdsinfer_custom_impl_VLM/
  parse.cpp
  vlm_meta.hpp
  trtllm_client.cpp
  Makefile

pipeline/configs/
  nvinfer_movinet.txt
  nvinfer_vlm.txt
  labels_action.txt

pipeline/models/
  movinet/movinet.engine
  qwen-vl-3b/llm.engine
  qwen-vl-3b/vision.engine

backend/services/vlm-trt/
  Dockerfile
  build_engine.py
  server.cpp
  CMakeLists.txt
```

## Files to modify

```
pipeline/app/pipeline.py        (Phase 1, 5)
pipeline/app/event_fsm.py       ✅ done
pipeline/Dockerfile             (link custom impl libs)
backend/protos/vlm.proto        (Phase 1: crop_bbox)
backend/app/vlm/grpc_server.py  (Phase 1; deprecate Phase 5)
backend/app/services/alert_consumer.py (Phase 5)
docker-compose.yml              ✅ Phase 0.1; Phase 3, 5
```

## Decisions locked

- VLM size for SGIE-B: **Qwen2.5-VL-3B-AWQ** (vs 7B)
- Action classifier: **MoViNet-A0** (4-frame stack, drops streaming-state requirement)
- Action classes: `{idle, walking, carrying, dropping, dumping, picking_up}` — gate VLM on classes 3+4
- Speculative decode: **ngram** in vLLM (Phase 0); **draft-target** in TRT-LLM (Phase 3)
- VLM output schema: single call returns `{action, confidence, deterrent_message, is_dumping}`
- Crop margin: 30% bbox padding, native resolution (no downscale)
- Auto-label data mix: 6 LSG videos + Kinetics-400 negatives, threshold 0.7
- Decision cadence (MoViNet): 0.5s, 2s smoothing window
