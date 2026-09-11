# Coverage and Blockers

## Session constraints
This audit was run as a single compressed pass under a tight time/token budget, prioritizing the
four areas the task brief called out explicitly, rather than the full multi-phase protocol
(which assumes a much larger budget: full repo line coverage, full route/state graph with ≤200
enumerated combinations, live Playwright-driven testing of every persona/mode). This file records
exactly what was and wasn't done, honestly, per the protocol's completion-gate requirement.

## What was done
- [CODE] Confirmed `pipeline/app/config.py:15` `batch_size=1`, `pipeline/app/pipeline.py:975-977`
  wiring `nvstreammux batch-size` from that config, `pipeline/configs/nvinfer_rfdetr.txt:31`
  `batch-size=1`, and `scripts/onnx_to_engine.py:106-126` static-batch export comments — full
  independent trace for F-01.
- [CODE] Read `backend/app/auth/middleware.py` in full; confirmed dev-mode bypass logic.
- [RUNTIME] `docker ps` — confirmed all named containers up.
- [RUNTIME] `docker exec lsg-wastewatch-api-1 env | grep -i auth` — confirmed `WW_AUTH_DEV_MODE=true` live.
- [RUNTIME] `curl http://localhost:5174/` → 200 (frontend dev server reachable).
- [RUNTIME] `curl http://localhost:8082/docs` → 200, `curl http://localhost:8082/openapi.json` →
  parsed full route list (confirms FastAPI app + route surface: cameras, alerts, analytics,
  events, evidence, health, devices, reports, lpr, system).
- [RUNTIME] `curl http://localhost:8082/api/cameras` (no auth header) → 200 with one live camera
  record ("Entry", cam-01, status offline) — confirms both API liveness and the auth-bypass claim.
- [CODE] Read `frontend/src/integrations/supabase/client.ts` in full — confirmed stub-client
  design (F-03).
- [CODE] `ToolSearch` queries ("playwright browser navigate", "browser click screenshot") — no
  browser-automation MCP tool found in this environment.

## What was explicitly scoped down / not done, and why
- **Full Phase 1–4 artifacts** (`01-app-intent.md` through `09-test-matrix.csv` in their full
  prescribed form) were not separately produced as standalone documents in this pass — content
  that would populate them is instead captured in this summary set (executive summary + findings)
  given the time budget. This is a deliberate scope reduction, not an oversight: the task brief
  said "It's fine to scope down exhaustive... coverage... prioritize depth on" the four named
  areas, and this pass followed that instruction literally.
- **Route/state graph and full UI journey walkthrough** (Phase 3/5): [BLOCKED — no browser
  automation tool available in this session]. No Playwright, Chrome DevTools, or other
  `browser_*` MCP tool was discoverable via `ToolSearch`. Camera-management, alert-triage, ROI
  editor, analytics, and reports journeys were only inspected via component/route source
  (`frontend/src/components/...`, `frontend/src/hooks/...`) referenced in the git status, not
  exercised live. No screenshots were produced.
- **Live reproduction of F-01's downstream symptom** (second camera silently getting zero
  detections): not reproduced live — would require registering a second RTSP source through the
  UI/API while the pipeline is running and observing DeepStream/pipeline logs or Kafka event
  output for a controlled window, which is a multi-minute stateful experiment outside this pass's
  budget. F-01 is therefore backed by full code-path evidence but the specific runtime symptom is
  [INFERENCE] from that code, not [RUNTIME]-observed.
- **Full-repo line-by-line business-logic ledger** (`05-line-coverage-ledger.csv`): not produced.
  The repo spans backend (FastAPI + multiple services: alert-svc, evidence-svc, health-monitor,
  reports, log-worker, sip-svc, tts-worker, lpr-worker, healer), a DeepStream/GStreamer pipeline
  app, and a React frontend — full line coverage of all of this is a multi-day effort; only the
  auth middleware and the batch-size/streammux code path were read in full.
- **Combinatorial test matrix / persona-mode testing** (Phases 4–5 in full): not executed; no
  `08-combination-model.md` / `09-test-matrix.csv` were produced given no live UI testing occurred
  to execute against.
- **Non-local deployment configs** (CI/staging/prod docker-compose or Helm/K8s manifests, if any)
  were not searched for other `WW_AUTH_DEV_MODE=true` occurrences beyond what grep found in this
  repo (`backend/tests/conftest.py` sets it for tests only, which is expected/safe).

## Recommendation
Re-run Phase 5 of this protocol with a Playwright or Chrome DevTools MCP tool attached to produce
real UI evidence, and budget a follow-up pass for full Phase 2–4 artifacts if deeper, broader
coverage is required than the four priority areas covered here.
