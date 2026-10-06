# Problem: long-horizon identity for static objects in a CCTV detection pipeline

## System
A DeepStream 7.x pipeline on a single Jetson-class GPU does real-time illegal-dumping
detection on municipal CCTV. Per camera, per frame:

- RF-DETR-Seg-Nano (384x384, instance segmentation) emits detections in 4 classes:
  bag, new_waste, person, vehicle.
- nvtracker (ByteTrack) assigns short-lived track IDs.
- nvdsanalytics filters detections to operator-drawn ROI polygons.
- A per-track finite state machine (IDLE -> ENTER -> OBSERVE -> VIOLATION ->
  ALERTING -> ESCALATED -> EXITED) drives alerting for person and vehicle tracks,
  using dwell time in ROI and centroid speed.
- A relation model attributes a dumping action to an actor (person/vehicle) by
  pairing it with a nearby waste detection.

## What works
Actor-side alerting works. A person or vehicle is present for seconds to a couple of
minutes, so a track ID is a sufficient identity for its whole lifecycle.

## What does not work
Dumped waste is static and persists for hours or days. We need a second state machine
over waste objects with age-driven states:

  NEW      -> just dumped, alert-eligible, drawn on the operator video
  OLD      -> aged past a threshold, still tracked
  RETIRED  -> suppressed from alerting and from the on-screen overlay, but the
              database record stays live and its elapsed-time counter keeps running

Retirement has a specific purpose beyond reporting: a pile that stays alert-eligible
forever is re-detected every frame and remains a candidate for relation-model pairing
with any later passer-by, which would attribute existing garbage to an innocent person.
Retirement makes known waste ineligible for new attribution.

This requires a stable identity for a waste object across hours. We do not have one.

## Hard constraints (measured in this codebase, not assumed)
- nvtracker `maxShadowTrackingAge: 50` frames, approx. 2 seconds at stream rate.
  After ~2s of non-detection the track is dropped; re-detection yields a new ID.
  This value was deliberately reduced from 750 (~30s) to 375 to 50, because long
  shadow tracking kept segmentation masks alive after the subject had left frame.
  Raising it back is not acceptable.
- `earlyTerminationAge: 20` frames is a second termination path (~0.8s).
- Track IDs do not survive pipeline restarts (deploys, crashes, config reloads).
- Track IDs do not survive scene cuts; per-camera state is cleared on cut.
- Occlusion is normal and can last many minutes: a parked truck or van can hide a
  pile entirely. Absence of detection must not be read as "the waste is gone."
- Cameras are fixed but not perfectly rigid; wind and mounting flex cause small
  drift in image coordinates over hours.
- The existing actor FSM is in-memory only (`dict[tuple[str, int], TrackFSM]`) with
  `exit_timeout_s: 300`. Hours-scale state cannot use that lifetime.
- Detector output per waste instance: camera id, bbox, instance mask, class,
  confidence, timestamp. No depth, no re-ID embedding currently computed for waste.

## Question
Define the identity scheme for a waste object that survives track-ID churn, long
occlusion, and process restarts. Specifically:

1. What key makes two waste detections, separated by 40 minutes, the same pile?
2. What rule distinguishes "cleared by the municipality" from "occluded" or
   "missed by the detector this frame"?
3. How is a second bag dropped 0.5 m from an existing pile handled: same object,
   or a new one? What about a pile that is kicked and spreads over time?
4. Where does this state live so it survives a restart, and what is the write
   frequency, given the pipeline runs at 25-30 fps across multiple cameras on one
   embedded GPU?

State the failure modes of the proposed scheme.
