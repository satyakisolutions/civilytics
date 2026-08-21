# MiniCPM-o 4.5 VLM Integration — Trigger Swap + Real-Time Narration

Date: 2026-08-21

## Context

Current VLM path: `pipeline/app/vlm_grpc_client.py` → gRPC `Analyze` (`backend/app/vlm/grpc_server.py`, `backend/protos/vlm.proto`) → `backend/app/vlm/client.py` (`VLMClient`, OpenAI-compatible HTTP to a self-hosted vLLM instance) → Qwen2.5-VL-7B-Instruct-AWQ (`backend/app/config/settings.py:14-15`). Fired on FSM `ENTER`/`VIOLATION` states (`pipeline/app/pipeline.py:768-823`) via a synchronous crop, fire-and-forget from the GStreamer probe thread with a circuit breaker.

Goal: move to MiniCPM-o 4.5 for two purposes — (1) same triggered violation-crop analysis, (2) new always-on narration that watches the stream continuously and flags violation types the FSM/RFDETR detector doesn't catch.

## Phase 1 — Trigger model swap

Replace Qwen2.5-VL with MiniCPM-o 4.5 behind the existing `VLMClient`, vision-only (`init_audio=False`).

- Served via `vllm-omni` (separate extension of vLLM for omni-modal models; MiniCPM-o 4.5 is not in mainline vLLM) exposing `/v1/chat/completions`.
- Config changes only, no proto/gRPC/API shape changes: `settings.vlm_base_url`, `settings.vlm_model` → MiniCPM-o 4.5, deploy config (`minicpmo_4_5.yaml`), request-level `chat_template_kwargs.enable_thinking=false` (disables reasoning/CoT mode — trigger path is synchronous and latency-sensitive; thinking mode adds tokens Qwen2.5-VL never emitted).
- `Analyze` RPC, `AnalyzeRequest.frames_jpeg`/`trigger_bbox`, Kafka `wastewatch.vlm.results` — all unchanged.
- Hardware: MiniCPM-o 4.5 is 9B params. BF16 ≈19GB VRAM, int4 ≈11GB. AWQ/GPTQ quantized checkpoints not confirmed to exist for 4.5 (unlike current Qwen2.5-VL-AWQ) — deploy BF16 or int4, verify GPU headroom before rollout; this is a step up from the current AWQ footprint and needs capacity check.
- Risk: `vllm-omni` is less mature/battle-tested than mainline vLLM's native Qwen2.5-VL support — treat as an operational risk to monitor (version pinning, community coverage) rather than a blocker.

## Phase 2 — Always-on real-time narration

New capability: a continuous narration session per active camera that free-narrates the scene and flags anomalies the FSM/RFDETR path doesn't catch (illegal dumping motion, uncovered/overflowing load, open-ended anomalies).

### Pipeline restructure

Current: primary `nvinfer` (RFDETR 2-class detector) → FSM/tracking/OSD → tee after `nvosd` into 3 branches (RTSP output, metadata, capture) (`pipeline.py:1044-1108`).

New: tee inserted immediately after primary `nvinfer`, before `nvstreamdemux`:

- **Branch A** (existing path, re-routed to new tee point): RFDETR detection output → FSM/tracking/OSD/evidence path unchanged (`fsm_manager.update`, capture probe, trigger `Analyze` calls per `pipeline.py:748-866`).
- **Branch B** (new): narration tap — post-inference, pre-OSD-decoration frames, sampled at `WW_NARRATION_FPS` (default TBD at implementation, e.g. 1fps), RGBA→JPEG, fed to a new per-camera `pipeline/app/narration_client.py` duplex streaming session.

Both branches read the same inference output; detection does not re-run for narration.

### Narration client

`narration_client.py`: one long-lived duplex session per camera against the shared MiniCPM-o `vllm-omni` service, using its native streaming API (`streaming_prefill`/`streaming_generate`, ~1s chunk alignment per MiniCPM-o's Omni-Flow framework) — not the stateless `/v1/chat/completions` shape used by Phase 1's trigger path. Runs on its own thread/task, not the GStreamer probe thread (narration is not latency-critical).

Model is prompted to free-narrate continuously and emit structured flags: `{narration: str, flags: [{type, confidence, description}]}`, `type` ∈ {dumping_motion, uncovered_load, anomaly}.

### Evidence + review queue

On any flag: capture current + recent frames as an evidence clip (reuse `evidence_writer.py` capture mechanics), publish to new Kafka topic `wastewatch.narration.flags`.

New table `narration_flags` (new alembic migration): `id`, `camera_id` (FK cameras), `timestamp`, `flag_type`, `confidence`, `narration_text`, `evidence_path`, `review_status` (pending/confirmed/dismissed), `reviewed_by`, `reviewed_at`.

New consumer (`backend/app/evidence/worker.py`-style, group e.g. `wastewatch.group.narration-worker`) on `wastewatch.narration.flags` persists rows.

New API (`backend/app/api/routes/narration_flags.py`): `GET /narration-flags` (list, filter by `review_status`), `POST /narration-flags/{id}/confirm` (promotes to a real `ViolationEvent` row, reusing the existing evidence pipeline), `POST /narration-flags/{id}/dismiss`.

This is a deliberately separate, lower-trust queue from `violation_events` — free-text narration flags are noisier than FSM detection and need human triage before becoming official violation records.

### GPU sharing

Single `vllm-omni` process serves both Phase 1 trigger calls and Phase 2 narration sessions (N cameras). vLLM/vllm-omni's continuous batching means these interleave on the GPU scheduler rather than running strictly serially. Safety cap: `WW_NARRATION_MAX_CONCURRENT_CAMS` bounds total concurrent narration sessions so an unbounded number of long-lived streaming sessions can't queue-delay the latency-sensitive trigger path — a throttle on session count, not a switch to disable batching/parallelism.

## Out of scope

- Audio input/output (MiniCPM-o's speech capabilities) — vision-only for both phases.
- Segmentation model details for Branch A's existing RFDETR path — unchanged, just re-routed to the new tee point.
- Exact `WW_NARRATION_FPS` / `WW_NARRATION_MAX_CONCURRENT_CAMS` tuning values — determined during implementation/load testing.
