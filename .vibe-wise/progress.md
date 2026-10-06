# Learning Progress

## Alerting state machine and the IWDD acceptance window

Introduced
- IWDD benchmark protocol (Greco et al., IVC 174, 2026): a detection counts only
  within -5 s to +10 s of event onset; ranked on F1. Their baseline F1 0.79.
- Current repo thresholds (vehicle stop 20 s, generic dwell 15 s, person dwell
  10 s) fall at or outside that window, so dwell-triggered escalations would
  score as false negatives even when correct.

Demonstrated understanding
- Learner proposed a design that decouples the dwell timer from the alert
  trigger: dwell/ROI entry acts as an observation gate, while the alert fires on
  the relation-model event (the drop). This addresses the window problem at its
  cause rather than by shortening timers.
- States: OBSERVE (P/V in zone) -> SUSPICIOUS (bag near P/V, monitor closely)
  -> CRITICAL (waste dumped; relation model supplies attribution), then fan-out
  to LPR, ReID, evidence, messaging.
- Learner resolved: SUSPICIOUS is not a required predecessor to CRITICAL;
  proximity alone can connect actor to dropped object when the bag was not
  visible beforehand.
- Learner resolved: retrieval case handled by continuing to monitor and
  auto-stopping the alert if the object is picked back up.
- Learner noted the fan-out is currently synchronous ("not fire and forget").

Needs reinforcement
- None recorded yet.

## Evidence recording trigger

Introduced
- DeepStream Smart Record has a 15 s circular GPU pre-buffer, so a trigger at the
  event retroactively captures the preceding 15 s without speculative writing.
- `smart_record.py` keys `self._active` by `(camera_id, track_id)` while its own
  docstring claims one recording per source, and `create_sr_bin(source_id,
  camera_id)` builds one SR bin per camera. `max_concurrent: 8` is shared across
  all sources.

Demonstrated understanding
- Learner rejected trigger-at-event: evidence must show how the actor entered the
  zone, so recording starts at ROI entry and is discarded when no event occurs.
- Learner resolved the concurrency problem unprompted: if a recording is already
  running for that camera, further ROI entries do not start another one. One
  footage per camera regardless of how many actors are present.
- Learner resolved the stop rule as two rules split by whether a critical fired:
  no critical -> ends when ROI occupancy reaches zero, discard; critical ->
  latches to the culprit track and ends when that track is lost or leaves frame.
- Learner chose no grace period after track loss.

Needs reinforcement
- Claude flagged that track loss is not the same as leaving frame (nvtracker
  churns IDs on occlusion; this is why the OSD needed IoU-based red inheritance
  this session). Learner accepted track loss as the stop condition anyway. Worth
  revisiting if clips are seen cut short by occlusion.

## Waste persistence and long-horizon identity (parked)

Introduced
- v13 dropped the `old_waste` class, so new-vs-old is no longer a detector signal
  (`nvdsparseseg_RfdetrSeg.cpp:92`); no waste-aging state exists in `pipeline/app/`.
- nvtracker cannot supply hours-scale identity: `maxShadowTrackingAge: 50` frames
  (~2 s), `earlyTerminationAge: 20`, and no survival across restarts or scene cuts.
  The 50 was deliberately lowered from 750 to 375 to 50 to kill a stale-mask bug.
- The repo has no visual ReID of any kind. The only embedding code is
  `backend/app/logs/qdrant_store.py`, sentence-transformers over log text.
- Evidence window (seconds, tied to the act) is a different problem from
  persistence window (hours, tied to whether the waste is still there).
- Track identity vs location identity: counting hours requires a key that survives
  track churn and process restarts.

Demonstrated understanding
- Learner specified a temporal FSM over waste objects: NEW -> OLD -> RETIRED, where
  retirement suppresses alerting and the on-screen overlay while the database row
  stays live and its elapsed-time counter keeps running.
- Learner identified the reporting requirement: report how long a pile has been
  present ("been there for XYZ hours").
- Claude added the suppression rationale: waste that stays alert-eligible remains a
  candidate for relation-model pairing with later passers-by, risking attribution of
  existing garbage to an innocent person.
- Learner initially proposed tracker-held identity; corrected after being shown the
  shadow-tracking limits.

Unresolved (parked by learner)
- The identity key for a waste object across hours.
- Thresholds for NEW -> OLD and OLD -> RETIRED.
- Clearance detection: distinguishing cleared from occluded or missed.
- An external model proposed WasteSite vs DumpEvent separation, spatial-gate-first
  matching with appearance as a ranking signal, camera registration transforms,
  waste-specific embeddings, and background-restoration clearance. Claude assessed
  the site/deposit split as the high-value low-cost piece and the embedding and
  registration work as expensive relative to fixed-camera spatial matching. No
  decision taken.

## Pending decision

Decision name: commit point for the CRITICAL fan-out
Stage: awaiting reasoning (Build checkpoint)

Claude raised that two branches of the same CRITICAL transition pull opposite
ways: dynamic dumping (125 of 250 MIVIA positives, waste thrown from a moving
vehicle) needs instant commit, while the retrieval negative needs delayed commit
to avoid alerting on an object that is picked back up. Retraction is also
asymmetric: irreversible side effects (operator messaging, enforcement record)
cannot be undone once sent.

Awaiting the learner's reasoning on whether to split the fan-out into
reversible actions (evidence, LPR, ReID) firing immediately versus irreversible
ones gated on a short confirmation, or to treat dynamic and static dumping as
separate paths with different commit rules.

Unresolved: whether dynamic dumping reaches CRITICAL without passing the dwell
gate at all. Learner answered "this is the initial check"; the concrete path for
a non-dwelling vehicle is not yet specified.

Also pending: `plan-roi-entry-recording.md` is designed and confirmed but not
implemented; learner deferred it to work on UI flow.
