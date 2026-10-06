# Plan: camera onboarding flow

Status: designed. One decision open. Nothing implemented.
Scope: first-run camera setup only.

## Learner's flow
1. Landing page with no cameras shows an "add camera" placeholder/CTA.
2. It routes to Camera Management.
3. On successful add, the camera settings modal opens immediately.
4. That modal carries the ROI and all thresholds the camera needs.

## What already exists (verified in code, not assumed)
This is mostly wiring. `CameraSettingsDialog` already holds everything step 4 asks for:

| Capability | Location |
|---|---|
| RTSP input stream URL | `AddCameraDialog.tsx:217` |
| Name / zone / location | `AddCameraDialog.tsx:182,195,207` |
| ROI drawing on live feed | `CameraSettingsDialog.tsx:103` renders `CameraFeed` |
| Suspicious threshold (s) | default 1.5 |
| Violation threshold (s) | default 3.0 |
| Waste proximity (px) | default 60 |
| Bag proximity (px) | default 15 |
| GPS click-to-set map | `AddCameraDialog.tsx:228` `<MapPicker>` |

Wired into `CameraManagementView.tsx:79`, opened by the gear on each `CameraCard`
(`onSettings`, line 148).

Correction to an earlier claim in this session: ROI editing is NOT monitoring-only.
It is reachable from camera management through the settings dialog. The earlier
reading traced `ROIOverlay` import sites and missed that the dialog reaches it via
`CameraFeed`.

All three of the learner's original complaints (ROI in the wrong place, no
thresholds in camera management, raw lat/long entry) are therefore discoverability
problems, not missing features.

## Field order matters
The RTSP input stream URL is the gating field for the whole flow. ROI is drawn on a
live feed, the feed exists only once the stream URL resolves, and the thresholds are
meaningless without an ROI. So the dependency chain is:

    stream URL -> live feed -> ROI -> thresholds

That is why the URL belongs in the add step (where it already is) and everything
downstream belongs in the settings modal that opens after a successful add. It also
means stream-URL validation is what determines whether the mandatory flow can
complete -- see the open decision below.

## Work required
1. Empty-state CTA on the landing page routing to camera management. NEW.
2. On add success, open `CameraSettingsDialog` for the new camera instead of
   closing the add dialog. State change only.
3. ROI and thresholds: already present, no change.

## Learner decisions taken
- The settings dialog is mandatory on first add. A camera with no ROI detects
  nothing, so it should not exist in a usable state without one.
- Keep all four existing thresholds in the dialog. No additions. This leaves
  `vehicle_stop_threshold_s: 20.0` unexposed, so vehicle dwell stays a global
  policy number rather than a per-camera one.

## Open decision
What "mandatory" means in practice. ROI is drawn on a live video feed, so the
camera must be streaming before an ROI can exist. If the RTSP URL is wrong or the
camera is offline at setup time, there is no feed to draw on. A URL that is
syntactically valid can still fail to stream (bad credentials, wrong path, camera
down), so form validation on the URL is not enough -- the flow needs an actual
connection result:

  (a) blocked at creation until the feed comes up
  (b) saved in an explicit "setup incomplete" state to return to
  (c) saved but flagged unusable, with the pipeline skipping it until an ROI exists

Also unresolved: what happens to cameras already in the DB with no ROI. A hard
requirement has to either migrate or exempt them. Three such cameras were parked
earlier in this session.

## Blocking bug for this flow
Threshold defaults disagree with the pipeline. The dialog uses 1.5 s / 3.0 s;
`event_fsm.py` uses 3.0 s enter, 15.0 s violation, 10.0 s person, 20.0 s vehicle
stop. The dialog sends only changed keys, so an operator who opens it and saves
anything silently writes 1.5/3.0 over the pipeline defaults for that camera.

Making the dialog mandatory on every add means every new camera will pass through
it, so this stops being an edge case and becomes the default path. Fix before
shipping the mandatory flow.

Related: the "Violation threshold" hint text says it applies to a "non-person
track (e.g. vehicle)", which is now wrong -- vehicles use `vehicle_stop_threshold_s`.
