# Waste Alert & Evidence Flow

## 1. Purpose and scope

Documents the current waste-dumping detection → attribution → confirmation →
alert → evidence flow in `pipeline/app/pipeline.py`, as of the production-
hardening pass in this session. Covers what changed, what's still deferred,
and how to validate it against a live DeepStream stream.

## 2. Detection and tracking inputs

- Primary detector: RF-DETR-Seg (`nvinfer_rfdetr_seg.txt`), classes include
  `person` and `waste` (custom-trained, class_id 2 — see `COCO_CLASS_MAP`).
- Tracker: NvMultiObjectTracker assigns a stable `track_id` per object across
  frames within one stream continuity (see §13 for what breaks this).
- Per-frame, `extract_metadata()` (the `queue_meta` branch, post-tee) reads
  `NvDsObjectMeta` for each detection: `class_name`, `track_id`, `bbox`,
  `frame_meta.frame_num`, `frame_meta.source_id`.

## 3. N-of-M waste confirmation

`pipeline/app/confirmation.py`, class `RollingConfirmation`.

Replaces the previous `WASTE_CONFIRM_FRAMES=1` counter (not a confirmation —
a single misclassified frame triggered the full alert flow).

- `_WASTE_CONFIRM_N = 1`, `_WASTE_CONFIRM_M = 1` (`pipeline.py`, validated
  `1 <= N <= M` at import time via `assert`).
- A bounded `deque(maxlen=M)` of booleans per waste_key.
- **Observation feed**: the per-object loop in `extract_metadata()` only
  runs when `class_name == "waste"` was actually detected this frame — a
  true "the object was expected but the detector missed it" observation
  isn't directly visible from inside that branch. Approximated via the
  `frame_num` gap since the waste_key's last sighting: each frame skipped
  between two sightings backfills one `observe(False)`, capped at M so a
  scene cut or long occlusion can't wipe out an otherwise-confirmable
  window with one enormous gap.
- Confirmed when `len(window) == M and sum(window) >= N`.
- Edge-triggered via `should_fire()`/`mark_fired()` — fires `force_violation()`
  on the waste track's own FSM exactly once per attribution episode.

## 4. Attribution (existing behavior and limitations)

`pipeline.py:~1150-1250`. Two-stage:

1. **Immediate check**: on the waste object's very first-seen frame, and
   again on the first `PERSON_AND_WASTE_TOGETHER` frame if the waste appeared
   earlier, find the nearest person by edge-to-edge bbox gap (calibrated to
   source-image pixels). If within `waste_proximity_px` (default 60px,
   per-camera configurable), attribute immediately.
2. **Retry**: if nobody qualifies, re-check every `_WASTE_ATTRIBUTION_DELAY_FRAMES`
   (11) frames until someone qualifies, the track is lost, or the initial
   attribution timeout expires.

Once attribution succeeds, the alert uses an absolute source-frame deadline:
`match_frame + 10`. Thus co-presence and a successful match on frame 2544
schedule the red highlight and dashboard alert for frame 2554. If the waste
track is not detected on exactly frame 2554, the trigger occurs on its next
visible frame.

**Known limitation (documented, not fixed this session)**: this is
proximity-only, not causal. It does not build a temporal history of person
positions before/after the waste appeared, does not score approach-vs-leave
motion, does not use segmentation-mask distance (RF-DETR-Seg produces masks;
only bbox distance is used), and is not perspective-corrected (60px means a
different physical distance near vs. far from the camera). A confidently
wrong attribution is not currently distinguished from an "unknown" case —
see §19 deferred work.

## 5. Final overlay-decision pass

`decorate_for_osd()` (`pipeline.py:462`), attached to `nvdsosd`'s **sink**
pad — i.e. it runs immediately before `nvosd` rasterizes each buffer,
on the live pre-tee stream. This already re-evaluates
`fsm_manager.is_escalated(camera_id, track_id)` fresh, every frame, for
every object — it is the "final decision pass" that determines box color
and label text right before the pixels are fixed.

This means a `force_violation()` call in `extract_metadata()` (a different,
post-tee, `queue_meta`-buffered branch) takes effect starting from
whichever live frame `decorate_for_osd` is processing at the moment the
call executes — not retroactively on frames already rasterized. Both
`queue_meta` and `queue_capture` are configured `leaky=2` (drop-**old**
buffers under backpressure, per `gst-inspect-1.0 queue`), which keeps both
branches close to the live position rather than developing a growing
backlog — this is what keeps the gap between "FSM updated" and "OSD
reflects it" small in practice (confirmed via live capture logs showing
`lateness=0`).

## 6. Primary alert publication

`_fire_waste_alert_async()` (`pipeline.py:~211`).

**Changed this session**: the Kafka publish (`kafka_producer.send_alert`)
now happens immediately, using whatever `jpeg_bytes` was already captured
synchronously on the pad-probe thread (the `frame_jpeg_cache` entry frozen
at fire time) — it no longer waits on the exact-target-frame poll loop.
A separate step afterward (same background thread, after publish
completes) attempts to fetch a better frame (the exact requested target,
or the OSD-confirmed red-box frame) and, if one arrives within its
timeout, **overwrites the same deterministic file path** with it.

**What this means operationally**: the alert is never late or lost because
evidence capture stalled, fails, or times out. The image attached to the
alert may initially be a pre-confirmation frame and get sharper/more
accurate moments later at the same URL.

**Not implemented this session** (see §19): a distinct `event_id` +
`evidence_status` (`PRELIMINARY`/`READY`/`PARTIAL`/`FAILED`) field in the
Kafka payload, and a second `EVIDENCE_UPDATED` event with `revision`. The
current design achieves "alert never blocks on evidence" without a payload
schema change, at the cost of not being able to tell, from the Kafka
message alone, whether the attached snapshot is the immediate or the
upgraded one.

## 7. Post-OSD evidence capture

`capture_frame_probe()` (`pipeline.py:~750`), on `fakesink_capture`'s sink
pad (branch 3, post-tee, post-`nvosd` — i.e. it sees the fully rendered
frame, includes whatever `decorate_for_osd` painted).

A capture request is `(source_id, target_frame)`. On each processed
buffer, any request with `target_frame <= actual_frame` is fulfilled by
that buffer — this is a nearest-**future** capture, not a guaranteed exact
one (documented honestly in the log line, see §10).

## 8. Evidence update behavior

Not a separate Kafka event this session (see §6, §19). The "update" is a
same-file overwrite performed by the same background thread that already
published the alert — no second notification fan-out (Telegram/TTS/siren),
since nothing re-publishes to Kafka.

## 9. Evidence statuses

Only two states currently exist in practice, distinguished by whether the
snapshot write happened at all:
- Snapshot attached (`snapshot_url` set) — could be the immediate frame or
  the upgraded one; not distinguishable from the payload.
- No snapshot (`snapshot_url: null`) — `jpeg_bytes` was empty/too small and
  the upgrade attempt (if any) also failed or timed out.

The spec's proposed `PRELIMINARY`/`READY`/`PARTIAL`/`FAILED` enum is **not
implemented** — deferred (§19).

## 10. Target vs. actual frame semantics

Logged explicitly per capture:

```
Captured requested evidence source_id=<id> target_frame=<N> actual_frame=<M> lateness=<M-N> frames
```

- `lateness == 0`: exact match.
- `lateness > 0`: nearest-future capture, the exact target frame was
  likely dropped by the `leaky=2` queue under load.
- `lateness > _MAX_EVIDENCE_LATENESS_FRAMES` (30): logged at `WARNING`
  instead of `INFO`, explicitly called out as "degraded capture, likely
  queue drop" — never silently reported as if it were exact.

## 11. Kafka fields and idempotency

No changes to `kafka_producer.send_alert()`'s schema this session. Fields
unchanged: `camera_id`, `track_id`, `severity`, `title`, `message`,
`dumped_by_track_id`, `dwell_time_s`, `bbox`, `snapshot_url`, `detected_at`.
No `event_id`/idempotency key exists yet — see §19.

## 12. Reminder behavior

`waste_repeat_alert_at` is seeded at the moment `force_violation()` marks
the waste track's own FSM as VIOLATION (i.e. at N-of-M confirmation time),
**not** only when the primary alert eventually fires post-highlight. This
closes a real bug found live this session: without this seed, the periodic
repeat-reminder check (which fires on any track already in
violation/alerting/escalated state) saw an unseeded timer defaulting to
0 and fired a "still unresolved" reminder *before* the primary alert had
even gone out.

## 13. Cleanup and timeout behavior

Three cleanup sites, all pop the same set of waste-keyed maps
(`waste_dumper_map`, `waste_attribution_pair`, `waste_confirm_windows`,
`waste_confirm_last_frame`, `waste_person_violation_frames_remaining`,
`waste_attribution_due_frame`, `waste_repeat_alert_at`):

1. Waste track's own `EXITED` FSM transition (graceful ROI exit).
2. `track_manager.prune_lost()` (hard track loss, 30s timeout) — also now
   restores the attributed waste object's `reset_dwell()` call when the
   *dumper* (not the waste) is lost, which had been silently dropped
   during an earlier attribution rewrite this session and is restored here.
3. Scene-cut detection (`capture_frame_probe`'s pixel-diff heuristic,
   needed because a looping RTSP test source gives no EOS signal) resets
   the same maps for that camera.

None of these three represent *physical* clearance of the waste (see §19 —
"track loss ≠ waste removed" is explicitly deferred).

## 14. Thread and state ownership

- All the `waste_*` dicts are plain Python dicts, mutated only from the
  single GStreamer pad-probe thread that runs `extract_metadata()` (branch
  2) for read-modify-write, and from the `capture_frame_probe()` thread
  (branch 3) for `frame_jpeg_exact_cache`/`frame_capture_requests`
  claim-and-remove. `_fire_waste_alert_async`'s background thread only
  *reads* these caches (`.pop()` for its own claimed key) — no other
  thread targets the same request key, so compound claim-remove races are
  low-risk in the current single-writer-per-key pattern.
- `RollingConfirmation` instances are not shared across threads.
- No new locks were added this session; none were judged necessary given
  the existing single-writer pattern held for the sites touched.

## 15. Configuration parameters

| Name | Default | File |
|---|---|---|
| `_WASTE_CONFIRM_N` | 1 | `pipeline.py` |
| `_WASTE_CONFIRM_M` | 1 | `pipeline.py` |
| `_WASTE_ATTRIBUTION_DELAY_FRAMES` | 11 | `pipeline.py` |
| `_PERSON_HIGHLIGHT_DELAY_FRAMES` | 10 | `pipeline.py` |
| `_EVIDENCE_CAPTURE_DELAY_FRAMES` | 10 | `pipeline.py` |
| `_MAX_EVIDENCE_LATENESS_FRAMES` | 30 | `pipeline.py` |
| `waste_proximity_px` (per-camera override) | 60.0 | `config.py` / `cameras.detection_config` |

No new environment variables were introduced.

## 16. Structured logs

```
WASTE ALERT SCHEDULED <camera> waste=<id> person=<id> match_frame=<N> trigger_frame=<N+10>
WASTE ATTRIBUTION CAPTURE <camera> frame=<N> waste=<id> person=<id|none> gap=<px> threshold=<px>
WASTE ALERT (post-highlight) <camera> waste=<id> person=<id>: dumped by <who>; trigger_frame=<N> evidence_frame=<N+10>
Captured requested evidence source_id=<id> target_frame=<N> actual_frame=<M> lateness=<M-N> frames
WASTE ALERT (repeat) <camera> track=<id>: still unresolved after <s>s
Resetting track/FSM state for <camera> after scene cut
Scene cut detected on source_id=<id> (mean diff=<x>)
```

## 17. Failure modes

- Evidence capture never arrives / times out → alert already sent with
  whatever was available at fire time; upgrade silently gives up.
- Disk write failure in `_write_alert_snapshot` → logged, alert already
  sent regardless (publish happens before any write in the primary path).
- Kafka producer failure → not decoupled from the calling thread; not
  addressed this session (see §19, "bounded retry/backpressure").
- Attribution finds nobody within `waste_proximity_px` → retries every 10
  frames indefinitely; if the track is lost first, cleanup removes the
  pending due-frame entry and nothing fires.

## 18. Test coverage

`pipeline/tests/test_confirmation.py` (new, 9 tests): all four spec-required
N-of-M sequences, edge-triggering fires-once semantics, window boundedness,
invalid N/M rejection.

Full suite: 39/39 passing (`PYTHONPATH=pipeline python3 -m pytest -q
pipeline/tests/`).

**Not covered by automated tests this session** (require live DeepStream
hardware/CUDA/RTSP, or a much larger mocking effort than this session's
scope): the overlay-ordering/OSD-timing behavior, the actual capture-branch
frame matching, Kafka delivery, and the alert-decoupling behavior under a
simulated capture stall. See §20 for manual validation commands.

## 19. Deferred architectural work

Explicitly out of scope for this patch, per the original hardening request:

- Consolidating all `waste_*` maps into one `WasteIncidentState` object.
- Full PTS-based (not frame-count-based) timing for all deadlines.
- Temporal/causal attribution scoring (person history before/after
  appearance, motion direction, mask-distance, ambiguity penalty,
  "unknown" fallback instead of a forced guess).
- Pre-event ring-buffer capture architecture (currently only a small
  throttled + exact-frame cache, no true rolling frame buffer).
- Full overlay-based person FSM (multiple independent violation "causes"
  per person, reference-counted) — currently `force_violation()` on a
  person is a single flag, and two unrelated incidents both attributing
  the same person could interact.
- Physical-clearance detection (waste region visibly empty for a duration,
  or operator acknowledgement) — cleanup today only reacts to tracker
  events (exit/loss/scene-cut), not physical removal.
- Stream-epoch versioning after camera restart/reconnect.
- Cross-camera identity resolution.
- `event_id`/`revision`/`evidence_status` Kafka payload fields and a
  distinct `EVIDENCE_UPDATED` event type (see §6, §11).
- Immutable (non-overwritten) evidence filenames.
- Bounded worker pool for alert firing (currently one `threading.Thread`
  per alert — acceptable at current volumes, revisit if alert frequency
  grows significantly).
- Capture-request TTL and explicit atomic claim-and-remove locking
  (currently relies on single-writer-per-key discipline, not a lock).

## 20. Manual validation (live DeepStream stream)

```bash
# Watch full attribution -> confirm -> alert -> evidence cycle
docker logs -f lsg-wastewatch-pipeline-1 2>&1 | grep -E \
  "WASTE ATTRIBUTION CAPTURE|WASTE ALERT|Captured requested evidence"

# Confirm N-of-M is active (should NOT fire on a single-frame blip anymore)
# -- trigger a brief false detection and confirm no WASTE ALERT follows
# within a few frames.

# Confirm alert timing is decoupled from evidence:
# temporarily rename/chmod the evidence dir to force write failures,
# confirm WASTE ALERT (post-highlight) still logs and Kafka still receives
# wastewatch.alerts messages (docker exec kafka kafka-console-consumer...).
docker exec lsg-wastewatch-kafka-1 kafka-console-consumer \
  --bootstrap-server localhost:9092 --topic wastewatch.alerts --from-beginning

# Confirm dwell reset on dumper loss (regression fixed this session):
# watch for "Reset waste dwell for ... dumper person=... lost" after the
# attributed person walks out of frame while the waste object remains.
docker logs -f lsg-wastewatch-pipeline-1 2>&1 | grep "Reset waste dwell"

# Run the full pipeline unit test suite
cd pipeline && PYTHONPATH=. python3 -m pytest -q tests/
```
