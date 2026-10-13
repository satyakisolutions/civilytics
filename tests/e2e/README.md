# Civilytics E2E suite

End-to-end tests covering the operator journey: onboard camera → set ROI →
synthetic detection via Kafka → alert → dashboard → evidence → analytics →
reporting → email → latency → edit/delete/logout.

Detection is **simulated by publishing synthetic messages to Kafka** — the GPU
DeepStream pipeline is never run.

## Run

### Against the running stack (live mode)
```bash
pip install --user -r tests/e2e/requirements.txt
python -m playwright install chromium          # for browser tests only
export CIVILYTICS_BASE_URL=https://192.168.1.8  # self-signed → TLS verify off
cd tests/e2e && python -m pytest -v
```
Kafka defaults to `127.0.0.1:9092` (the stack binds it there on the host). The
suite maps the broker's advertised name `kafka` → `127.0.0.1` in-process, so no
`/etc/hosts` or stack change is needed.

### Without a running stack (testcontainers mode)
Leave `CIVILYTICS_BASE_URL` unset. `conftest.py` brings up
postgres/kafka/redis via testcontainers; the API/keycloak/minio bring-up needs
the built `civilytics-prod-*` images + compose env (`CIVILYTICS_API_IMAGE`,
...). Until those are supplied the non-live path `pytest.skip`s with a clear
message — live mode is the supported path today.

### Markers / env
- `-m e2e` whole suite · `-m browser` Playwright tests · `-m latency` timing.
- `CIVILYTICS_USER` / `CIVILYTICS_PASS` (default `admin` / `Civilytics5829!`).
- `CIVILYTICS_KAFKA` (default `127.0.0.1:9092`).
- `E2E_FRAME_LATENCY_MS` (5000), `E2E_ALERT_LATENCY_S` (15),
  `E2E_DASHBOARD_LATENCY_S` (20) — generous regression guards; measured values
  are printed.
- `CIVILYTICS_MAILPIT_URL` — enables the email-delivery assertion (non-live).

Every test cleans up the cameras it creates; the suite is idempotent/re-runnable.

## Real system facts discovered (ground truth for the tests)

### Auth (cookie-based)
- `POST /api/auth/login` `{username,password}` → sets httpOnly `ww_access`
  (15m) + `ww_refresh` (8h). No bearer token in the body.
- `GET /api/auth/me` · `POST /api/auth/logout` (204, clears cookies) ·
  `POST /api/auth/forgot-password` `{username}` → **always 202** ·
  `GET /api/auth/ws-token` → `{"token": <ww_access value>}` (needs cookie).

### REST (all under `/api`, collection routes have **no** trailing slash)
- Cameras: `GET/POST /api/cameras`, `GET/PATCH/DELETE /api/cameras/{id}`.
  Create body: `name,zone` (required), `location,gps_lat,gps_lng,stream_url,
  source_type`. **lat/lng are `gps_lat`/`gps_lng`.** POST→201, DELETE→204.
  create/update/delete require **admin**.
- ROI: `GET/PUT/DELETE /api/cameras/{id}/roi`. PUT body is a **bare array**
  `[{"name","points":[{"x","y"},...]}]` (coords normalized 0–1).
- Events: `GET /api/events` (filters: camera_id, status, event_type, severity,
  dates, min_confidence, offset, limit), `GET/PATCH /api/events/{id}`,
  `POST /api/events/publish` (worker→API; only **updates** an existing row).
- Alerts: `GET /api/alerts` (camera_id, status, limit), `PATCH
  /api/alerts/{id}`, `POST /api/alerts/clear`.
- Analytics: `/api/analytics/{summary,hourly,daily,by-zone,by-camera,heatmap,
  daily-by-category}`.
- Evidence: `GET /api/evidence` (event_id), `GET /api/evidence/files`,
  `GET /api/evidence/file?path=` (FileResponse; 403 if path escapes root).
- Reports: `POST /api/reports/generate/{weekly|monthly}`,
  `GET /api/reports/history`, `GET/PUT/POST/DELETE /api/reports/recipients`.

### WebSockets (prefix `/ws`)
- `/ws/alerts` (role viewer), `/ws/events`, `/ws/metadata/{camera_id}`.
- **Auth = subprotocol**, not query param: `new WebSocket(url, ["bearer",
  token])`; backend echoes `accept(subprotocol="bearer")`. Token from
  `/api/auth/ws-token`.
- Alerts frame: `{"type":"alert", id, camera_id, zone, severity, title,
  message, snapshot_url, timestamp}`; VLM frames use `"type":"vlm_result"`.

### Kafka (client: **aiokafka**; broker advertises `kafka:9092`)
Two subsystems:
- **Pipeline (`wastewatch.*`)** — the one the synthetic tests use:
  - `wastewatch.events`: `{camera_id, track_id, state, prev_state,
    dwell_time_s, class_name, bbox, timestamp}` → EventConsumer **broadcasts to
    /ws/events only (no DB row)**.
  - `wastewatch.alerts`: `{camera_id, severity, title, message, track_id,
    tts_text, suppress_tts, snapshot_url, detected_at}` → AlertConsumer
    (group `wastewatch.group.api-alert-consumer`, `auto_offset_reset=latest`)
    resolves camera by UUID/name, inserts an `alerts` row, broadcasts /ws/alerts.
  - `wastewatch.vlm.results` → VLM alert path.
- **Civilytics (`civilytics.dumping.events`)** — Conductor workflow intake;
  `publish_event` only **updates** an existing `violation_events` row, so a lone
  synthetic message does not create event/alert rows without Conductor.

### Frontend (React, served same-origin via nginx)
- **No `/login` route** — `RequireAuth` renders `<Login>` inline when
  unauthenticated. Login form: `#username`, `#password`, `button[type=submit]`
  ("Sign in"). **No `data-testid` anywhere.**
- Views are URL-driven via MonitoringContext: dashboard/live = `/`,
  cameras = `/cameras`, analytics/history/map/etc. = `/<view>`.
- Live alerts panel: `AlertPanel` with `<h2>Alerts</h2>`; items are
  `.alert-card-critical | -warning | -info`.
- **ROI editor is an `<svg>`** (ROIOverlay.tsx), opened as an overlay in the
  cameras view — *not* a `<canvas>` (the only `<canvas>` is WebRTC frame grab).
- `frontend/src/lib/google-maps-link.ts` parses lat/lng from a Maps URL
  client-side (`@lat,lng`, `!3d..!4d..`, `?q=/ll=`, bare `lat,lng`). **No
  backend equivalent** — ported into `helpers.parse_google_maps_latlng`.

## Gaps / bugs found in the product
1. **Worker/pipeline alerts never persist (bug).** `AlertConsumer._handle_alert`
   (and `_handle_vlm_result`) insert `.values(zone=<str>)`, but the `alerts`
   table column is `zone_id` (UUID FK to `zones`). The insert raises
   ("Failed to save alert to DB"); the `alerts` table stays empty and
   `GET /api/alerts` returns nothing for a pipeline alert. Only the transient
   `/ws/alerts` broadcast survives. `backend/app/services/alert_consumer.py`
   lines ~217–230 and ~387–399. Covered by `test_04_alerts.py`
   (WS = pass, DB/API = `xfail`).
2. **Synthetic detection creates no `violation_events` row.** The pipeline event
   consumer is WS-only, and the Civilytics `publish_event` task only updates an
   existing row. So a Kafka-only E2E cannot assert `GET /api/events` without the
   GPU pipeline + Conductor. Detection is asserted via `/ws/events` instead.
3. **Tenancy visibility.** `GET /api/alerts|events` are tenant-scoped
   (`scope(...)`), but worker inserts land `tenant_id=NULL`; a scoped admin
   never sees `NULL`-tenant rows (only a tenantless owner would). Compounds #1.
