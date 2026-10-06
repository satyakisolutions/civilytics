# Plan: ROI-entry evidence recording with per-camera dedup

Status: designed, confirmed by learner, NOT implemented.
Deferred in favour of UI flow work.

## Requirement (learner's)
Evidence must show how the actor entered the zone, so recording starts at ROI
entry rather than at the violation. Recordings with no event are discarded.

## Rules
| Phase | Rule |
|---|---|
| Start | First actor enters ROI and camera has no active recording -> start. Later entries -> no-op. |
| Stop, no critical | ROI occupancy drops to zero -> stop, delete file. |
| Stop, after critical | Latch the culprit track. Track lost or out of frame -> stop, keep file. |

No grace period after track loss (learner's decision: track lost or out of frame
ends the recording).

## Changes: pipeline/app/smart_record.py
1. Re-key `self._active` from `(camera_id, track_id)` to `camera_id`. Matches the
   one-SR-bin-per-source reality of `create_sr_bin(source_id, camera_id)` and bounds
   `max_concurrent` by camera count instead of by traffic volume. Resolves the
   docstring/code contradiction between line 103 and line 427.
2. `start_recording(camera_id)` returns the existing path if that camera is already
   recording.
3. Add a per-camera ROI occupancy refcount. `stop_recording` discards the file when
   no critical was latched.
4. Add `latch_culprit(camera_id, track_id)`, called on VIOLATION. Once set, occupancy
   no longer governs the stop; the culprit track does.

## Changes: pipeline/app/pipeline.py
- Call the ROI-entry trigger where ROI membership is already computed (~line 1648).
- Call `latch_culprit` from the existing VIOLATION path.

## Claude's proposals (not learner decisions)
- Discarded files: `unlink()` only after `NvDsSRStop` finalizes, so DeepStream can
  write the moov atom. Deleting mid-write leaves a dangling muxer.
- `max_duration_s: 120` left unchanged for now. It will truncate a long loiter;
  raising it costs GPU memory. Measure real clip lengths first.

## Unresolved
- Whether 120s is sufficient once real footage is reviewed.

## Test
One refcount sequence test: A enters, B enters, A leaves, B leaves -> exactly one
start and one stop.

## Why the trigger could not stay as-is
`max_concurrent: 8` is shared across all sources. With the trigger on VIOLATION it is
never contended. Moving the trigger to ROI entry without per-camera dedup would let
ordinary pedestrian traffic exhaust the budget, and `start_recording` would return
`None` for a real dumping event ("Max concurrent recordings reached") precisely when
the scene is busy.
