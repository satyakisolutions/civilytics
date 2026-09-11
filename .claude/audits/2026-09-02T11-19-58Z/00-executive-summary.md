# WasteWatch Audit — Executive Summary

Run ID: 2026-09-02T11-19-58Z. Scope: illegal-dumping-detection platform (DeepStream RF-DETR
pipeline + FastAPI backend + React/Vite frontend). Entry point: `/home` (frontend at
http://localhost:5174, backend at http://localhost:8082).

**What the app is for**: watch RTSP camera feeds (currently `cam-01`, `cam-02` via an RTSP
simulator), detect illegal dumping events with a RF-DETR object detector + ByteTrack tracker +
action classifier, raise alerts with evidence (images/clips), and let operators manage cameras,
view alerts/analytics, and generate reports through a web console.

**Primary journeys**: camera management (add/edit/ROI), live monitoring (multi-camera grid,
WebRTC), alert triage, analytics/reporting. **Business-critical invariant that is broken**:
the pipeline is advertised/operated as multi-camera but is hard-capped to single-camera
detection throughput by a batch=1 TensorRT engine — see Finding F-01.

**Coverage**: This was executed as a compressed, single-pass audit under a tight time/token
budget, not the full multi-day protocol. Static code inspection (Phases 0–4/6, code side) was
done for the four priority areas named in the task brief: /home + primary journeys, the
single-camera-capacity bug, auth enforcement, and any other Critical/High issue surfaced along
the way. Full line-by-line coverage of the whole repo, the full route/state graph, and the full
combinatorial test matrix were **not** attempted — see `11-coverage-and-blockers.md`.

**Dynamic/live testing**: [BLOCKED] No Playwright or other browser-automation MCP tool was
available in this session (confirmed via `ToolSearch` — only Gmail/Calendar/Drive/Design/WebFetch
tools surfaced, no `browser_*`/`playwright_*` tools). Phase 5 UI-driven testing could not be
performed. Compensating evidence: `curl` smoke tests against the live frontend dev server and
backend REST API (see `11-coverage-and-blockers.md` for exact commands/outputs), plus full static
trace of the relevant code paths.

## Findings summary

| ID | Severity | Title |
|----|----------|-------|
| F-01 | **High** | Pipeline is single-camera-capacity despite UI allowing N active cameras (batch=1 TensorRT engine + `nvstreammux batch-size=1`) |
| F-02 | Low/Informational | Auth is fully bypassed on the live stack via `WW_AUTH_DEV_MODE=true` (mock owner/admin/viewer identity, no token required) — intentional dev-mode design, but confirmed live and unauthenticated |
| F-03 | Low | Frontend ships a Supabase client/config that is dead code in this deployment (silently replaced by a stub) — maintainability/clarity risk, not a functional defect |

No Critical findings were confirmed (no evidence of tenant-escape, data corruption, or
money-loss class issues found within the scoped review).

## Exact blockers
- No browser-automation tool (Playwright/Chrome DevTools MCP) available → all Phase 5 live-UI
  journeys, screenshots, and route/state-graph runtime verification are [BLOCKED]. Recommend
  re-running Phase 5 with a browser tool attached, or a human walking the flagged journeys.
- Did not have a way to trigger a live dumping-detection event end-to-end (would need to inject
  frames through the RTSP sim during a monitored window) — F-01's downstream effect (no alert
  fires for cam-02) is argued from code, not observed as a live "0 alerts" outcome via UI.

## Five highest-value next actions
1. Fix or explicitly descope F-01: either export/build a dynamic-batch (or per-stream batch≥2)
   TensorRT engine for RF-DETR and raise `pipeline/app/config.py: batch_size`, or change the UI/
   API to reject/warn when a second camera is activated while `batch_size == 1`, so operators
   aren't given a false ACTIVE state with zero detection throughput.
2. Re-run this audit's Phase 5 with a Playwright/browser MCP tool attached to get real UI
   evidence (console errors, network traces, screenshots) for the /home, camera-add, and alert
   journeys.
3. Decide and document the auth story for anything beyond localhost dev: confirm
   `WW_AUTH_DEV_MODE` is never set `true` outside dev compose overrides, and add a startup
   assertion/log-level warning when the API boots in dev-auth mode.
4. Remove or clearly quarantine the unused Supabase client/config (F-03) to reduce confusion for
   future contributors reading `frontend/src/integrations/supabase/`.
5. Follow up with full-repo line coverage and the complete route/state graph (Phases 2–4 in full)
   once a live browser tool is available, particularly for the ROI editor and reports/export
   flows which were not inspected in this pass.
