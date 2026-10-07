# Object-Release Trigger-Gated Dumping Detection - Corrected Design Spec

Date: 2026-08-31
Status: Recommended proof-of-concept direction
Scope: First production-oriented vertical slice under the target Civilytics architecture
Supersedes: the original version of this file (bag-count-decrease as primary trigger signal, latest-frame-only cache, single warning threshold) — corrected below after review found several unsafe/incomplete details. Also supersedes the always-on/full-state-machine scope in `docs/superpowers/specs/2026-08-21-minicpmo-vlm-design.md` Phase 2 as the near-term build target; that spec's Phase 1 (MiniCPM-o trigger-model config swap) is unaffected and can still ship independently.

## 1. Decision

Use a fast detector and tracker for continuous monitoring. Use MiniCPM-o only after a suspicious object-release sequence has been detected.

This is the best first approach for:

- Low warning latency
- Low average GPU use
- Low inference cost
- Short implementation time
- Explainable event decisions

Do not use MiniCPM-o for continuous 5-10 FPS captioning. The measured single-frame latency of about 1.8 seconds on the target RTX 3060 makes that unsuitable for a real-time control loop.

## 2. Why the previous draft needed correction

The overall trigger-gated direction was sound, but several implementation details were unsafe or incomplete:

| Previous assumption | Problem | Correction |
|---|---|---|
| Warn after the released bag is stationary and the person is moving away | This confirms an event after the release. It may be too late to prevent dumping | Add an earlier release-candidate warning threshold and a later confirmed-incident threshold |
| Read only the latest JPEG from a cache | A latest-frame cache cannot supply pre-event video | Keep a bounded per-camera ring buffer |
| Collect post-event frames when the trigger fires | Future frames do not exist yet | Open an event, then complete the clip asynchronously after the post-roll interval |
| Treat a reduction in bag count as the primary signal | Detection misses, occlusion, and track-ID changes can all reduce the count | Track the full object lifecycle and require supporting evidence |
| Find the released bag only in current detections | A small stationary or occluded bag may temporarily disappear | Use re-detection, last-known location, scene change, and persistence checks |
| Reuse the current aggressive TTS wording | A false or humiliating public accusation creates safety and trust risks | Use neutral, pre-approved warning templates before enabling live audio |
| Treat public speaker audio and admin phone audio as one path | They have different recipients, wording, urgency, and audit needs | Implement separate public-warning and admin-notification adapters |
| Accept one positive sample clip as sufficient validation | It cannot establish a false-warning rate | Validate in shadow mode on positive and negative clips before public activation |

## 3. Grounded facts and open assumptions

### Measured facts

- MiniCPM-o 4.5 took about 1.8 seconds per single frame in steady state on the target RTX 3060 12 GB using the tested Transformers path.
- Peak MiniCPM-o VRAM was measured at about 8.09 GB.
- Model loading and first-call warmup were large, so the service must remain loaded and warm.
- The existing application already has DeepStream detection, tracking, ROI filtering, asynchronous VLM calls, alert dispatch, and SIP integrations that can be extended.
- The reference clip contains a subtle release near the tree at about the fifth second. This should be used as one positive regression case, not as the entire acceptance set.

### Assumptions that must be tested

- An open-vocabulary detector can reliably detect relevant bags, sacks, boxes, and waste objects at the camera's real resolution and angle.
- The actual DeepStream low-level tracker can preserve small-object identity through brief occlusion and after motion stops.
- The complete GPU workload can coexist without out-of-memory errors or unacceptable latency.
- A neutral early warning can be triggered with an acceptable false-warning rate.
- MiniCPM-o can distinguish release, retrieval, temporary placement, and abandonment from a short ordered keyframe sequence.

## 4. Critical design principle

Separate the system into two speeds:

- The fast path detects people, vehicles, carried objects, release candidates, and movement changes.
- The reasoning path receives only a small number of suspicious events and explains or verifies them.

The detector and state machine control timing. MiniCPM-o does not sit in the real-time frame loop.

## 5. Runtime flow

1. Decode each camera stream once.
2. Detect and track people and vehicles at the existing pipeline rate.
3. Apply the configured ROI. Ignore activity outside it.
4. Run the waste-object detector only for active ROI tracks or scheduled re-detection areas.
5. Associate candidate objects with a person or vehicle using proximity, relative motion, overlap, and track history.
6. Maintain a per-track inventory of objects moving with that subject.
7. Detect an object-release candidate when an associated object separates, stops following, or creates a persistent local scene change.
8. If early evidence crosses the warning threshold, play a short neutral warning.
9. Continue observing. If the object persists and the subject separates without retrieving it, create a confirmed incident candidate.
10. Finish the pre-roll and post-roll event clip asynchronously.
11. Send 8-12 ordered keyframes plus structured context to the already-warm MiniCPM-o service.
12. If verification succeeds, notify the administrator with text and optional phone-call audio.
13. Store the event evidence, rule features, model result, timings, and final disposition.

## 6. Object lifecycle state machine

Use lifecycle state, not raw bag count:

| State | Meaning | Typical transition |
|---|---|---|
| OBSERVED | Candidate object exists in the ROI | Becomes associated with a subject |
| ASSOCIATED | Object moves consistently with a person or vehicle | Evidence suggests it separated |
| RELEASE_CANDIDATE | Object no longer follows the subject | Early warning or recovery |
| OCCLUDED_PENDING | Object is temporarily hidden or missed | Reappears, is recovered, or times out |
| PLACED_PENDING | Object appears stationary near the release location | Retrieved or persists |
| RETRIEVED | Object moves with the same subject again | Close as non-incident |
| ABANDONED_CANDIDATE | Object persists while the subject separates | Send for VLM verification |
| CONFIRMED_INCIDENT | Rule and verifier support dumping | Notify admin and retain evidence |
| DISMISSED | Evidence was insufficient or contradicted | Retain for tuning, no escalation |

Track records must retain:

- Camera ID and ROI ID
- Person or vehicle track ID
- Object track ID or candidate ID
- First seen, associated, release, and last seen timestamps
- Last-known box and release-location uncertainty
- Motion and distance history
- Detection confidence history
- Scene-change evidence
- Warning and escalation timestamps

## 7. Use two thresholds

### Threshold A: early release candidate

Purpose: a preventive public warning.

Possible evidence:

- An object previously moved with the subject.
- The object separates from the subject or stops following.
- The subject continues moving.
- The separation is consistent for a short debounce window.

Example neutral warning:

> Attention. An item may have been left near the tree. Please take it with you and use the designated waste bin.

This warning must not describe protected characteristics, identify a person, shame them, or state that a crime definitely occurred.

### Threshold B: confirmed incident candidate

Purpose: administrator escalation and evidence retention.

Require stronger evidence:

- The object remains near the release location for a configured persistence interval.
- Subject-to-object distance continues increasing or the subject exits the ROI.
- The object is not retrieved.
- Re-detection or scene-change evidence supports the object's continued presence.
- MiniCPM-o verification supports release and abandonment.

This split reduces response time without treating the early warning as a final accusation.

## 8. Temporal buffers and event assembly

Maintain a bounded ring buffer for every camera. Do not rely on a latest-frame cache.

Recommended starting configuration:

- Low-resolution evidence frames: 5 FPS
- Pre-roll: 5 seconds
- Post-roll: 4 seconds
- Maximum event assembly window: 12 seconds
- Ordered VLM keyframes: 8-12

When Threshold A or a release candidate fires:

1. Copy the relevant pre-roll references from the ring buffer.
2. Create an open event record.
3. Continue collecting frames until post-roll completes.
4. Select keyframes around association, release, reappearance, persistence, and departure.
5. Submit the completed package asynchronously.

The live pipeline must never wait for post-roll collection or MiniCPM-o.

## 9. Frame-rate policy

The requested 5-10 FPS applies to the fast perception loop, not to same-level VLM captioning.

Recommended starting rates:

| Work | Rate |
|---|---|
| Decode, person/vehicle tracking, ROI | Existing stream rate |
| Waste-object detection during active ROI activity | Start at 5 FPS |
| Waste-object re-detection around a release location | 5-10 FPS for a short burst |
| Inactive-camera sampling | 0-1 FPS or disabled |
| MiniCPM-o | One ordered keyframe batch per suspicious event |

Raise rates only after measuring end-to-end latency, GPU memory, queue age, and missed-event rate.

## 10. Fast detector selection

Start with YOLO-World or YOLOE as a no-training feasibility test because both support open-vocabulary detection.

Prompt examples:

- plastic bag
- garbage bag
- sack
- cardboard box
- waste bundle
- bottle
- container

Do not make the final production decision from marketing benchmarks. Benchmark the exact model size, image resolution, precision, export format, and target hardware.

If zero-shot recall is inadequate:

1. Label real frames from the deployment cameras.
2. Fine-tune a small conventional detector.
3. Export it to the existing TensorRT/DeepStream path.

A small domain-trained detector is likely to be cheaper and more predictable in production than a larger permanent open-vocabulary model.

Commercial deployments must review the chosen model, code, and package licenses before distribution or hosted use. YOLO-World's official repository is GPL-3.0, and Ultralytics publishes separate open-source and enterprise licensing terms.

## 11. Tracking requirements

Do not assume that a configured component called ByteTrack proves the needed behavior. Confirm the actual DeepStream low-level tracker and its settings.

Test:

- ID continuity for small carried objects
- Occlusion by the person's body
- Stationary-object retention
- Low-confidence association
- Track termination and reactivation
- Multiple people and multiple objects
- Camera shake, shadows, and partial ROI exits

Use a separate candidate identity if the tracker drops the stationary object. Match it back by location, appearance embedding when available, size, and scene-change evidence.

## 12. Stationary-object verification

The released object may not appear in every detector result. Verification should combine:

- Periodic open-vocabulary re-detection near the release region
- Last-known object location and uncertainty expansion
- Pre-event versus post-event scene change
- Persistence across multiple samples
- Subject departure or ROI exit
- Retrieval detection

No single missing detection should confirm or dismiss an event.

## 13. Corrected processing outline

```python
def on_camera_frame(camera, frame, timestamp):
    camera.ring_buffer.append(frame, timestamp)
    update_existing_person_vehicle_tracks(camera, frame)

    if not camera.has_active_roi_tracks():
        return

    if camera.object_detector_due(timestamp):
        objects = detect_candidate_objects(frame, camera.active_search_regions())
        update_object_lifecycles(camera, objects, timestamp)

    for event in camera.release_candidates:
        update_release_evidence(camera, event, timestamp)

        if event.crossed_warning_threshold() and not event.warning_sent:
            public_warning.play(
                template_id="possible_item_left",
                camera_id=camera.id,
                zone_name=event.safe_zone_name,
            )
            event.warning_sent = True

        if event.is_retrieved():
            event.close("retrieved")
            continue

        if event.crossed_incident_threshold():
            event.begin_post_roll_if_needed(timestamp)

        if event.post_roll_complete(timestamp) and not event.verification_submitted:
            keyframes = select_ordered_keyframes(
                camera.ring_buffer,
                event,
                maximum=12,
            )
            vlm_queue.submit(
                event_id=event.id,
                keyframes=keyframes,
                structured_context=event.rule_features(),
            )
            event.verification_submitted = True
```

The event object must initialize all histories, timers, warning flags, and verification flags when it is created. It must not depend on an undefined missing-bag object.

## 14. MiniCPM-o role

MiniCPM-o is a selective temporal verifier and explanation generator.

It should receive:

- 8-12 ordered frames
- Camera and safe zone label
- Approximate release time
- Subject and object boxes or cropped regions
- Detector and rule observations
- Explicit instructions to report uncertainty

It should answer:

- Was an item released or left behind?
- Was it retrieved?
- Did the person or vehicle move away?
- Where was it left?
- What visible evidence supports the conclusion?
- Is the evidence insufficient or ambiguous?

Do not ask it to infer intent, identity, ethnicity, health status, or other sensitive traits.

Keep the model loaded in a dedicated warm service. Apply bounded concurrency and a request deadline. Never load it per event.

## 15. Structured output contract

Use schema-validated JSON rather than free-form text:

```json
{
  "event_type": "possible_dumping",
  "verdict": "confirmed",
  "confidence": 0.86,
  "subject_type": "person",
  "object_description": "light-colored bag",
  "location_description": "ground beside the tree",
  "actions": [
    "subject approached while carrying the item",
    "item appeared on the ground",
    "subject moved away",
    "item remained visible"
  ],
  "retrieved": false,
  "uncertainty": "brief body occlusion at the release moment",
  "admin_summary": "A person appeared to leave a light-colored bag beside the tree and moved away without retrieving it."
}
```

Validate values, length, confidence range, and allowed enums before using model output downstream.

## 16. Public warning safety

The current aggressive, all-caps, or shaming TTS wrapper must not be used for public warnings.

Use cached, pre-approved, neutral audio templates so warning playback does not wait for generative TTS.

Examples:

- "Attention. An item may have been left behind. Please take it with you."
- "Please use the designated waste bin and remove the item from this area."

Dynamic content should be limited to safe zone names such as "near the tree" or "at the entrance." Do not describe gender, race, age, clothing, disability, or other personal characteristics over a public speaker.

Provide rate limiting, quiet hours where required, manual mute, replay protection, and an audit record.

## 17. Separate public and administrator channels

**Public speaker**

- Immediate
- Neutral
- Short
- Cached audio
- No definitive accusation
- No personal description

**Administrator text**

- Event time and camera
- Safe location description
- Object description
- Rule evidence and VLM summary
- Confidence and uncertainty
- Evidence link or clip ID

**Administrator phone call**

Generate or select a separate audio message after confirmation:

> Civilytics alert for Camera 3. A possible dumping event was detected near the tree at 10:42. A light-colored bag appeared to be left on the ground, and the person moved away without retrieving it. Please review incident WW-1042.

The SIP gateway is only the delivery adapter. Add idempotency, retry limits, persisted call status, and acknowledgement later if operationally required.

## 18. Concurrency and multi-camera behavior

A sequential loop over every camera will not scale.

Use:

- One decoder path per configured stream or the existing DeepStream batching
- A bounded detector work queue
- Batched inference where supported
- Per-camera freshness timestamps
- Drop-stale behavior instead of building an old-frame backlog
- Priority for active release candidates
- A maximum number of concurrent VLM requests
- Backpressure metrics and circuit breakers

If overloaded, reduce sampling on inactive cameras first. Never delay an active release candidate behind stale routine frames.

## 19. GPU capacity gate

MiniCPM-o already consumed about 8.09 GB of the 12 GB target GPU in the tested path. Capacity is not proven until the entire workload is measured together.

Before integration approval, record:

- DeepStream baseline VRAM and latency
- Detector incremental VRAM and latency
- MiniCPM-o warm idle VRAM
- Peak memory during simultaneous detector and VLM inference
- End-to-end queue delay
- Thermal throttling over a sustained run

If coexistence fails, use one of these options:

- Run MiniCPM-o on a second GPU or remote inference node.
- Use a smaller or more aggressively quantized verifier.
- Serialize expensive inference and accept slower admin verification.
- Keep the real-time detector local and call a hosted verifier only for rare events, subject to privacy and cost requirements.

## 20. Phased implementation plan

### Phase 0: establish truth

Duration: 1-2 days

- Confirm the actual repository components and tracker configuration.
- Reproduce MiniCPM-o latency and memory measurements.
- Define the ROI, safe zone labels, and warning policy.
- Extract the reference event around the fifth second as a regression case.
- Collect initial negative examples: walking, standing, setting down then retrieving, occlusion, shadows, and empty scenes.

Exit: repeatable benchmarks and a small labeled evaluation set.

### Phase 1: detector feasibility

Duration: 2-3 days

- Test YOLO-World and optionally YOLOE on deployment footage.
- Measure recall, false positives, latency, VRAM, and small-object behavior.
- Use ROI/track-gated inference.

Exit: adequate recall at 5 FPS on the target machine, or a documented decision to label and train a small detector.

### Phase 2: lifecycle tracker and replay harness

Duration: 4-6 days

- Add the ring buffer.
- Implement association and object lifecycle states.
- Add retrieval, occlusion, persistence, and scene-change handling.
- Build an offline replay runner that produces event timelines.

Exit: the reference event is detected and common negative cases close without escalation.

### Phase 3: shadow mode

Duration: 3-5 days plus observation time

- Run on real streams with no public audio.
- Record candidate events, timings, and false triggers.
- Tune thresholds per camera if necessary.
- Keep human review as ground truth.

Exit: agreed precision, recall, and time-to-warning targets on representative footage.

### Phase 4: MiniCPM-o verification and admin alerts

Duration: 3-5 days

- Keep the model warm.
- Add asynchronous post-roll assembly and keyframe selection.
- Enforce the structured output schema.
- Run MiniCPM-o in shadow mode first: log its verdicts against shadow-mode events from Phase 3, but do not deliver any admin text or phone audio yet. Compare verdicts to human-reviewed ground truth on the validation set (section 22).
- Only after MiniCPM-o's shadow-mode verdicts meet accuracy targets, enable text alerts and optional administrator phone audio as a live, operational path.

Exit: MiniCPM-o verdicts validated against ground truth in shadow mode first; verified events are then delivered once live, with evidence and uncertainty.

### Phase 5: supervised public warning

Duration: 2-4 days

- Replace unsafe TTS behavior with neutral templates.
- Enable warnings first in supervised hours or selected cameras.
- Add rate limits, mute controls, health monitoring, and audit logs.

Exit: public warning is enabled only after operational approval.

## 21. Delivery expectation

A realistic focused proof of concept is about 2-3 weeks after access to the repository, target GPU, camera configuration, and representative footage.

A safe production rollout takes longer because shadow-mode evidence and operational approval cannot be compressed into coding time.

## 22. Validation set

The test set must include more than the known positive clip.

**Positive patterns:**

- Small bag dropped beside a tree
- Box left near a wall
- Object removed from a vehicle and abandoned
- Brief body occlusion during release
- Subject exits the ROI after release

**Negative patterns:**

- Bag set down and picked up
- Bag handed to another person
- Bag hidden behind a person but still carried
- Person walks past existing litter
- Worker intentionally places an approved object
- Tracker ID switch
- Shadow or foliage movement
- Object detector flicker
- Subject waits near the object

Evaluate by event, not by individual frame.

## 23. Acceptance metrics

Agree on target values before live warning activation:

- Event recall on labeled dumping clips
- False alerts per camera-hour
- False public warnings per camera-hour
- Median and p95 time from release candidate to warning
- Median and p95 time from confirmation to admin delivery
- Retrieval cancellation accuracy
- Queue age and dropped-frame rate
- GPU peak memory and utilization
- VLM verification precision and invalid-JSON rate
- Duplicate alert rate

The most important safety metric is false public warnings, not detector frame accuracy.

## 24. Deferred work

These are outside the first vertical slice:

- Continuous scene narration
- Full multi-class waste taxonomy
- Cross-camera identity tracking
- Biometric identification
- Intent inference
- Automated fines or enforcement
- Full DTMF acknowledgement workflow
- Large-scale custom detector training
- MiniCPM-o TensorRT conversion

The full target architecture can still add versioned events, object storage, richer risk scoring, recovery transitions, and durable call records after the vertical slice proves the behavior.

## 25. Final recommendation

Proceed with the trigger-gated architecture, but implement it as an object-release lifecycle system rather than a bag-count rule.

The minimum safe sequence is:

1. Benchmark the fast detector on real footage.
2. Add a per-camera ring buffer and lifecycle tracker.
3. Validate in offline replay and shadow mode.
4. Add selective MiniCPM-o verification.
5. Enable administrator text and phone audio.
6. Enable neutral public warnings only after false-warning targets are met.

This preserves the low-latency, low-compute advantage while correcting the timing, buffering, tracking, safety, and validation gaps in the earlier draft.

## 26. Official references

- YOLO-World official repository: https://github.com/AILab-CVC/YOLO-World
- Ultralytics YOLO-World documentation: https://docs.ultralytics.com/models/yolo-world/
- Ultralytics YOLOE documentation: https://docs.ultralytics.com/models/yoloe/
- vLLM-Omni MiniCPM-o online serving example: https://docs.vllm.ai/projects/vllm-omni/en/v0.26.0/user_guide/examples/online_serving/minicpmo/
- OpenBMB MiniCPM repository: https://github.com/OpenBMB/MiniCPM-V
