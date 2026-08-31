# Bag-Trigger-Gated Dumping Detection — Design Spec

Date: 2026-08-31
Status: Approved direction (supersedes the full Approach 2 state-machine spec as the near-term build target)
Supersedes for now: the always-on/full-state-machine scope in `docs/superpowers/specs/2026-08-21-minicpmo-vlm-design.md` Phase 2. That spec's Phase 1 (MiniCPM-o trigger-model config swap) is unaffected and can still ship independently.

## 1. Why this design, not the others

Six approaches were explored (full writeup: `docs/wastewatch-approaches.pdf`). The two that mattered most:

- **Approach 2** (full ROI-gated temporal state machine, risk engine, safe-TTS rewrite, phone escalation with DTMF ack, versioned events, new DB tables, object storage) is the eventual target architecture, but Phase 0 inspection found it needs multiple pieces of infrastructure that don't exist yet (object storage, event versioning) and a rewrite of live TTS behavior. It's a multi-week build with real open questions.
- **Approach 3** (real hardware test) measured MiniCPM-o 4.5 at **~1.8 seconds per frame**, steady state, on the actual target GPU (RTX 3060, 12GB) using the standard `transformers` inference path. That is roughly 0.55 fps — nowhere near the 1fps continuous-narration target, and 5x too slow for the 3fps "suspicious" tier in Approach 2's own sampling table.

Rather than spend weeks either building Approach 2's full infrastructure or converting MiniCPM-o to a faster serving stack (TensorRT: weeks of custom work, no existing conversion path for this architecture; vLLM-omni: real candidate but unbenchmarked), this design routes around the latency problem structurally: **a fast, cheap detector decides when anything is worth looking at; MiniCPM-o is called rarely, as a verifier, not continuously.**

This is not a compromise that sacrifices the hard case. The sample clip that motivated Approach 6 (a person carrying two bags who discreetly releases one near a tree, occluded by their own body for under a second, no throwing motion) is still detectable — because the trigger isn't "is there a bag in frame," it's "did the number of bags moving with this person just decrease." That's a tracking-and-counting problem, not a captioning problem, and it doesn't need a VLM to solve it.

## 2. Facts this design is grounded in

All of the following were established this session, not assumed:

- MiniCPM-o 4.5 real steady-state single-frame latency: **~1.8s** (range 1.4–2.1s), naive `transformers.chat()` path, 4-bit-quantized LLM backbone + bf16 vision modules, on an RTX 3060 12GB. Peak VRAM 8.09GB.
- Model load time: 382s one-time; first-call CUDA warmup: 19.5s extra.
- The model, when called, produces accurate, factually-grounded, non-accusatory descriptions of a real scene (verified on a real press-brake work-area clip) — the *quality* of MiniCPM-o's output is not in question, only the frequency at which it can be called.
- The existing pipeline (`pipeline/app/pipeline.py`) already runs RF-DETR (2-class, TensorRT FP16) via `nvinfer`, ByteTrack via `nvtracker`, and a ROI polygon filter via `nvdsanalytics`, all at full camera frame rate today. This is the layer Approach 5's fast detector sits alongside.
- The existing trigger-VLM path (`vlm_grpc_client.py` → `grpc_server.py` → `client.py`) already does exactly the "fire rarely, call VLM, don't block the pipeline thread" pattern this design needs — it is being reused, not reinvented.
- `backend/app/alerts/sip_gateway.py` already has a working (if incomplete — no DTMF ack, no idempotency, no persisted call records) three-backend call escalation system (pjsua2 / Asterisk AMI / Twilio). This design uses it as-is for the first release; no call-platform change is in scope here.
- The current TTS engine (`backend/app/tts/engine.py`) hardcodes an aggressive, all-caps, shaming tone wrapper. This design does **not** touch that in its first phase — the public warning message content is a new, separate concern from the trigger/tracking pipeline being built here, and rewriting TTS tone is explicitly out of scope for this spec (tracked separately under Approach 2's TTS-conflict finding).
- No labeled bag/container dataset exists for this camera environment. YOLO-World is chosen specifically because it needs no training data to start (open-vocabulary, zero-shot).

## 3. Assumptions (stated explicitly, not hidden in code)

- **A1**: YOLO-World's zero-shot accuracy on this camera's real footage (resolution, lighting, camera angle) is good enough to trigger on, without custom training, for a PoC. Unverified — this is the first thing Phase 1 tests.
- **A2**: Running YOLO-World at 5–10fps on every camera, continuously, fits in the GPU/CPU budget alongside the existing DeepStream pipeline. Unverified — needs a latency+VRAM measurement identical in spirit to what was done for MiniCPM-o in Approach 3.
- **A3**: A simple rule (bag-count decrease + object stationary + person-object distance increasing + no retrieval within N seconds) is sufficient to distinguish real dumping from a person setting a bag down and picking it back up, without needing MiniCPM-o's temporal reasoning for that distinction. If false, MiniCPM-o's verification call (Phase 3) is the backstop — but if this rule is too noisy, the verifier will be called too often, eroding the whole point of the design.
- **A4**: The existing ByteTrack tracker (`nvtracker`) can be extended to track bag-class objects with stable IDs, not just person/vehicle. Unverified — ByteTrack is class-agnostic in principle, but this hasn't been tested against small, frequently-occluded objects like bags.
- **A5**: 3–5 seconds of pre-drop buffer plus the release frames plus 2–3 seconds post-drop is enough temporal context for MiniCPM-o to correctly verify a real event, given it was accurate on a 25-second/10-frame batch in Approach 3's test. Not re-tested at this specific shorter window.

## 4. Architecture and pseudocode

### 4.1 Pipeline flow

```
Camera stream (25fps, existing)
        |
        v
DeepStream: streammux -> nvinfer (RF-DETR, existing) -> nvtracker (ByteTrack, existing) -> nvdsanalytics (ROI filter, existing)
        |
        v
[NEW] Bag detector tap, 5-10fps sampled from existing frame_jpeg_cache (same cache the trigger-VLM path already reads)
        |
        v
[NEW] Person-bag association + inventory tracking
        |
        v
   bag count decreased for this person-track?  -----> no  --> keep observing
        |
       yes
        v
   released bag stationary + person-object distance increasing, sustained N seconds?  --> no --> keep observing (bag may be retrieved)
        |
       yes
        v
[EXISTING PATTERN, reused] Immediate TTS warning via existing sip/speaker path (no new tone/template work in this phase)
        |
        v
[NEW, rare] gRPC Analyze-style call to MiniCPM-o with pre-drop + release + post-drop frames, for verification only
        |
        v
   MiniCPM-o confirms dumping?  --> no --> log as false trigger, tune rule, no admin alert
        |
       yes
        v
[EXISTING] Admin alert via existing alert dispatcher + SIPGateway (Twilio/Asterisk, as today)
```

### 4.2 Bag detector tap (pseudocode)

```python
# pipeline/app/bag_trigger.py (new)

def bag_detector_tick(ctx: dict) -> None:
    """Runs on a GLib timer at 5-10fps, reads the same frame_jpeg_cache
    the existing capture_frame_probe already populates. Mirrors the
    narration_sampler.py pattern already designed for the (deferred)
    Approach 1 narration path -- same tap point, different consumer."""
    cache = ctx["frame_jpeg_cache"]
    detector = ctx["bag_detector"]  # YOLO-World instance, one per process, not per camera
    tracker_state = ctx["bag_tracker_state"]  # per-camera inventory state

    for source_id, jpeg_bytes in cache.items():
        detections = detector.detect(jpeg_bytes, classes=["bag", "sack", "container", "cardboard box"])
        camera_state = tracker_state[source_id]
        update_bag_inventory(camera_state, detections, person_tracks=ctx["active_person_tracks"][source_id])
```

```python
def update_bag_inventory(camera_state, detections, person_tracks) -> None:
    """Associates each detected bag with the nearest carrying person-track
    (by bounding-box proximity + motion correlation), then checks for
    an inventory decrease -- the core Approach 6 signal, simplified."""
    for person_track in person_tracks:
        prev_count = camera_state.carried_count.get(person_track.id, 0)
        current_bags = associate_bags_to_person(detections, person_track)
        current_count = len(current_bags)

        if current_count < prev_count:
            # a bag that WAS associated with this person is no longer moving with them
            dropped_bag = find_missing_bag(camera_state, person_track, current_bags)
            camera_state.pending_release[dropped_bag.id] = {
                "person_track_id": person_track.id,
                "release_frame": camera_state.frame_idx,
                "last_known_bbox": dropped_bag.bbox,
                "state": "OCCLUDED_PENDING_REAPPEARANCE",  # do not delete on brief occlusion
            }

        camera_state.carried_count[person_track.id] = current_count

    check_pending_releases(camera_state, detections)
```

```python
def check_pending_releases(camera_state, detections) -> None:
    """For each bag pending release confirmation: has it reappeared
    stationary near its last known location, and is the owning person's
    distance from it increasing? This is the confirm-or-drop-the-flag step."""
    now = camera_state.frame_idx
    for bag_id, pending in list(camera_state.pending_release.items()):
        reappeared = find_stationary_object_near(detections, pending["last_known_bbox"])
        if reappeared is None:
            if now - pending["release_frame"] > OCCLUSION_TIMEOUT_FRAMES:
                del camera_state.pending_release[bag_id]  # false alarm, object never reappeared (tracking noise)
            continue

        person = camera_state.get_person_track(pending["person_track_id"])
        distance = bbox_distance(person.bbox, reappeared.bbox)
        pending["distance_history"].append(distance)

        if len(pending["distance_history"]) >= MIN_CONSECUTIVE_FRAMES and is_increasing(pending["distance_history"]):
            fire_dumping_trigger(camera_state, pending, reappeared)
            del camera_state.pending_release[bag_id]
```

```python
def fire_dumping_trigger(camera_state, pending, bag) -> None:
    """This is the ONLY place MiniCPM-o gets called in this design.
    Reuses the existing vlm_grpc_client.py fire-and-forget pattern --
    same thread-pool-submit, same circuit breaker, same Kafka result
    topic. Only the request content differs: pre/release/post frames
    instead of a single crop."""
    frames = collect_temporal_window(
        camera_state,
        pre_seconds=4, post_seconds=2.5,
        center_frame=pending["release_frame"],
    )
    vlm_client.submit_verification(
        camera_id=camera_state.camera_id,
        track_id=pending["person_track_id"],
        frames_jpeg=frames,
        context={"event": "bag_released", "bag_id": bag.id},
    )
    trigger_immediate_tts_warning(camera_state, pending["person_track_id"])  # do not wait for MiniCPM-o
```

Note the ordering in `fire_dumping_trigger`: the TTS warning fires immediately on the deterministic rule (bag separated + stationary + distance increasing), **before** MiniCPM-o responds. MiniCPM-o's job is to confirm the event for the admin alert / incident record, not to gate the preventive warning — the whole point of this design is that the warning can't wait 1.8+ seconds for a VLM response.

### 4.3 Decision logic summary

```python
if bag_count_for_person_decreased:
    state = "PENDING_RELEASE_CONFIRMATION"

elif released_bag_reappeared_stationary and person_bag_distance_increasing_for_N_frames:
    state = "DUMPING_TRIGGERED"
    play_tts_warning(person_track)          # immediate, existing TTS path
    submit_to_minicpmo_for_verification()   # async, rare, ~1.8s is fine here

# on MiniCPM-o response (async callback, existing Kafka wastewatch.vlm.results consumer):
if minicpmo_confirms_dumping:
    create_admin_alert()                     # existing alert dispatcher + SIPGateway
else:
    log_false_trigger_for_rule_tuning()      # no admin alert, rule gets tuned over time
```

## 5. Possible bottlenecks

| Bottleneck | Why it matters | How this design addresses / defers it |
|---|---|---|
| **YOLO-World zero-shot accuracy on real camera footage** | If it misses bags or false-positives heavily on this specific camera's resolution/lighting, the whole trigger is unreliable | Phase 1 is explicitly a measurement phase before anything else is built on top of it (Assumption A1) |
| **YOLO-World throughput at 5-10fps per camera, N cameras** | Unmeasured GPU/CPU cost, could compete with the existing RF-DETR/DeepStream pipeline for the same GPU | Needs the same kind of latency+VRAM measurement done for MiniCPM-o in Approach 3, scaled to N cameras — not yet done (Assumption A2) |
| **ByteTrack stability on small, frequently-occluded bag objects** | If bag track IDs flicker/reassign across brief occlusions, `update_bag_inventory` will misfire (see false "count decreased" from a tracking glitch, not a real drop) | `OCCLUDED_PENDING_REAPPEARANCE` state + `OCCLUSION_TIMEOUT_FRAMES` window is the mitigation, but the timeout value itself needs tuning against real footage (Assumption A4) |
| **False-trigger rate of the deterministic rule** | Every false trigger costs one MiniCPM-o call (1.8s + GPU contention) plus one unnecessary public TTS warning — the second cost is worse, since warnings are user-facing and a bad one erodes trust | `check_pending_releases` requires `MIN_CONSECUTIVE_FRAMES` of sustained increasing distance, not a single-frame signal, specifically to reduce noise (Assumption A3) |
| **Person-bag association under multiple people/bags in frame simultaneously** | The sample clip has one person, two bags — a busier scene (multiple people, overlapping bags) stresses `associate_bags_to_person`'s proximity heuristic much harder | Not addressed in this phase; explicitly scoped as a v2 concern, tracked in "out of scope" below |
| **MiniCPM-o verification window sizing** | 4s pre + 2.5s post is a guess extrapolated from the 25s/10-frame test that worked; a shorter, more targeted window is unverified | Phase 3 explicitly benchmarks this against real trigger events before treating the verification step as reliable |
| **GPU contention between YOLO-World (continuous) and MiniCPM-o (rare but heavy, 8GB peak VRAM)** | Both need to coexist on the same 12GB GPU as the existing DeepStream/RF-DETR process | YOLO-World is lightweight enough to likely coexist, but this is unverified — if MiniCPM-o's 8GB peak collides with YOLO-World + DeepStream's combined footprint, one of them needs to be moved off-GPU or made even smaller |

## 6. Exact plan

### Phase 1 — Fast trigger detector (Week 1)

**Goal**: Prove YOLO-World can detect bag-class objects in this camera's real footage, at a measured latency/VRAM cost, with no custom training.

- Install `ultralytics` (YOLO-World) in the existing test venv (`/mnt/1tb-backup/mlcache/venvs/minicpmo`, or a fresh sibling venv if dependency conflicts arise — do not assume compatibility with the pinned `transformers==4.51.0` environment without checking).
- Run zero-shot detection with prompt classes: `garbage bag`, `plastic bag`, `sack`, `container`, `cardboard box`, `waste bundle` against the same sample clips already captured (the press-brake clip, and ideally a real dumping-pattern clip matching the tree-side scenario if one exists or can be captured).
- Measure: per-frame latency (same methodology as Approach 3's MiniCPM-o measurement — steady state, excluding model load), VRAM footprint, and qualitative detection accuracy (does it actually find the bags, does it false-positive on background clutter).
- **Exit criterion**: latency supports 5-10fps sustained, accuracy is visibly usable on real footage. If not, this phase's finding — not an assumption — determines whether to proceed to custom training instead (out of scope for this plan, tracked as a fallback).

### Phase 2 — Person-bag tracking and inventory rule (Week 2)

**Goal**: Implement `bag_trigger.py` (pseudocode in section 4.2) against DeepStream's existing per-camera track outputs.

- Extend the existing `frame_jpeg_cache` tap pattern (already used by both the trigger-VLM path and the deferred narration path) with the bag-detector tick.
- Implement `associate_bags_to_person`, `update_bag_inventory`, `check_pending_releases` as described.
- Test against the sample clip's known ground truth (2 bags carried in, 1 bag released and left, 1 bag carried out) — this is the acceptance test for the whole rule, not a generic unit test.
- Tune `OCCLUSION_TIMEOUT_FRAMES` and `MIN_CONSECUTIVE_FRAMES` against this real footage rather than picking arbitrary defaults.

### Phase 3 — TTS warning + MiniCPM-o verification + admin alert (Week 3)

**Goal**: Wire the trigger into the existing alert/TTS/call infrastructure, unchanged.

- On `DUMPING_TRIGGERED`, call the existing TTS/speaker path immediately (reuse as-is — no tone rewrite in this phase).
- Implement `vlm_client.submit_verification()` as a thin extension of the existing `VLMGrpcClient`/`VLMServicer.Analyze` path (section 4.2's `fire_dumping_trigger`) — same fire-and-forget, same circuit breaker, same Kafka result topic, different payload (temporal window instead of single crop).
- On MiniCPM-o confirmation, route through the existing alert dispatcher and `SIPGateway` (Twilio/Asterisk, as today) — no Jambonz, no DTMF ack, no new call infrastructure in this phase.
- Log every trigger (confirmed and false) for later rule-tuning, even before a formal incident-persistence schema exists.

### Explicitly deferred (not in this plan)

- Continuous MiniCPM-o narration (Approach 1's narration-review-queue scope) — separate, smaller, already spec'd elsewhere, can proceed independently if wanted.
- TensorRT conversion of MiniCPM-o — no existing path, weeks of speculative work, not justified while the trigger-gating approach avoids needing it.
- Full Approach 2 state machine (8 states, risk-score weighting, recovery transitions, object storage, versioned events) — this plan implements a minimal 2-state version of it (`observing` / `pending-release-confirmation`) sufficient for the sample pattern; the full state machine remains the eventual target if/when this simpler version proves insufficient.
- Hand-pose association — Approach 6's fuller design included explicit hand-object motion correlation; this plan uses simpler bounding-box proximity for person-bag association as a first pass.
- Jambonz integration — recommended in the approaches document for its DTMF-ack fit, but out of scope until phone-escalation acknowledgement is actually needed; Phase 3 uses the existing SIPGateway unchanged.
- Public-TTS tone/template rewrite — tracked separately as an Approach 2 finding; this plan reuses whatever TTS behavior exists today without judgment on its wording.
- Multi-person/multi-bag disambiguation in busy scenes — the sample clip and this plan's acceptance test are single-person, two-bag; busier scenes are a known unhandled case, not silently assumed away.
