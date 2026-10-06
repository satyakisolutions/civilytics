# Civilytics — screen event flows, API triggers, and auth

Derived from the 7-screen prototype and verified against the code in this repo.
Every endpoint, role and topic below was read from source, not assumed.
Items marked **MISSING** do not exist yet.

---

## 1. Module map

    DeepStream pipeline (pipeline/app/pipeline.py)
      RF-DETR-Seg -> nvtracker -> nvdsanalytics (ROI) -> event_fsm
      -> PipelineKafkaProducer (pipeline/app/kafka_producer.py)
      -> smart_record.py (evidence clips)
                |
                v  Kafka
    Backend (FastAPI, backend/app/main.py)
      Consumers started in lifespan():
        MetadataConsumer      -> /ws/metadata/{camera_id}
        EventConsumer         -> /ws/events
        AlertConsumer         -> /ws/alerts
        CameraStatusConsumer  -> (camera status updates)
      REST routers: health cameras config events roi alerts evidence
                    lpr analytics system devices reports logs
      Auth: app/auth/keycloak.py (JWKS/RS256) + app/auth/middleware.py
                |
                v  REST + WebSocket
    Frontend (frontend/src, single route "/")
      MonitoringContext.currentView switches 7 views
      useAnalytics / useMonitoring hooks -> fetch(API_BASE + path)

---

## 2. Auth and roles

### Implemented (backend)
Keycloak is already wired. `backend/app/auth/keycloak.py`:
- JWKS fetched from `{keycloak_url}/realms/{realm}/protocol/openid-connect/certs`,
  cached 600 s.
- `validate_token()` decodes RS256, verifies `exp` and `aud` against
  `settings.keycloak_client_id`.
- Roles read from the `realm_access.roles` claim.

`backend/app/auth/middleware.py` exposes three dependencies:

| Dependency | Role |
|---|---|
| `require_owner` | owner |
| `require_admin` | admin |
| `require_viewer` | viewer |

Dev bypass: `WW_AUTH_DEV_MODE=1` makes `get_current_user` return a mock user with
all three roles when no Authorization header is present. It is off unless the env
var is explicitly set. **This must be off in production** -- with it on, every
endpoint below is open.

### Role assignment per route (read from the route files)
| Area | Role required |
|---|---|
| `GET /api/analytics/*` | viewer |
| `GET /api/events`, `/events/{id}`, `/events/export/csv`, `/events/tracks/active` | viewer |
| `PATCH /api/events/{id}` | admin |
| `GET /api/evidence`, `/evidence/files`, `/evidence/file` | viewer |
| `GET /api/alerts` | viewer |
| `PATCH /api/alerts/{id}`, `POST /api/alerts/clear` | admin |
| `GET /api/cameras`, `/cameras/{id}` | viewer |
| `POST /api/cameras`, `PATCH`, `DELETE` | admin |
| `GET /api/cameras/{id}/roi` | viewer |
| `PUT`, `DELETE /api/cameras/{id}/roi` | admin |
| `GET /api/lpr/plates` | viewer |
| `GET /api/reports/recipients`, `/reports/history` | viewer |
| `PUT /api/reports/recipients`, `DELETE /recipients/{email}` | admin |
| `GET /api/devices/*` | viewer (reads) / admin (writes) |
| `GET /api/system/health/*` | admin |
| `GET /api/logs/*` | admin |
| `GET /api/config`, `/config/{key}` | viewer |
| `PUT /api/config/{key}` | **owner** |

So the role model is already three-tier and maps onto the requested
super admin / admin / user as:

| Requested | Existing role | Scope |
|---|---|---|
| Super admin | `owner` | the only role that can write global config |
| Admin | `admin` | cameras, ROI, thresholds, alert state, devices, reports, system health |
| User / operator | `viewer` | read events, analytics, evidence, live feeds |

### MISSING (frontend)
Nothing in `frontend/src` touches auth. Verified by grep: no Keycloak adapter,
no token storage, no `Authorization` header, no login route. `api-base.ts` builds
URLs and `fetchJson` calls `fetch()` with no credentials.

Consequences:
1. The app only works today because `WW_AUTH_DEV_MODE` is on, or because requests
   are failing 401 and the hooks swallow it -- `fetchJson` catches every error and
   returns a fallback (`useAnalytics.ts:33-39`), so an unauthenticated deployment
   shows zeros rather than an error.
2. Turning auth on without frontend work makes every screen render empty, silently.

Required frontend work:
- Keycloak JS adapter (`keycloak-js`), PKCE public client, redirect login.
- Token in `Authorization: Bearer` on every REST call and on the WebSocket
  connection (WS needs the token as a query param or a subprotocol -- browsers
  cannot set headers on `WebSocket`).
- Silent refresh before `exp`; 401 handling that re-authenticates rather than
  returning a zeroed fallback.
- Role-gated UI: hide admin-only controls instead of letting them 403.

### MISSING pages
| Page | For | Purpose |
|---|---|---|
| Login | all | Keycloak redirect / callback handling |
| Super admin onboarding | owner | first-run: realm config, workspace name, zones, global config keys |
| Admin onboarding | admin | add cameras, draw ROI, set thresholds (this is the camera-onboarding flow already planned) |
| User onboarding | viewer | orientation only: what the alert colours mean, where evidence lives |
| User management | owner/admin | invite, assign role, deactivate -- or defer entirely to the Keycloak admin console |

Open decision: whether user management lives in Civilytics or in Keycloak's own
admin console. Deferring to Keycloak is far less work and is the usual choice;
building it in-app means proxying the Keycloak Admin REST API with an
owner-only service account.

---

## 3. Screen flows

Notation: `UI action -> endpoint (role) -> module -> effect`

### Screen 1 — Analytics Dashboard
On mount (`useAnalytics.ts:66-69`), four parallel GETs:

    GET /api/analytics/summary   (viewer) -> app.analytics.aggregator.get_summary
    GET /api/analytics/hourly    (viewer) -> get_hourly_stats   ?hours= (default 24, max 168)
    GET /api/analytics/daily     (viewer) -> get_daily_trend    ?days=  (default 7,  max 90)
    GET /api/analytics/by-zone   (viewer) -> get_by_zone

| Panel | Served by | Gap |
|---|---|---|
| Events today / this week | `summary.events_today`, `.events_this_week` | none |
| Repeat match groups (8 person / 4 vehicle) | `summary.repeat_offenders` (flat int) | **MISSING** person/vehicle split -- needs match groups |
| Avg. review time | `summary.avg_response_time_minutes` | none |
| Daily Peak Dumping Pattern (4 series x 7 days) | `/daily` returns `{date, count}` | **MISSING** category breakdown |
| Violation categories (4 slices) | -- | **MISSING** entirely |
| When dumping happens (24 x 4 heatmap) | `/hourly` returns `{hour, count}` | **MISSING** category + window shading |
| Events by zone | `/by-zone` | none |
| System health (online/warnings/offline/evidence) | `summary.cameras_online`, `.cameras_total` | **MISSING** warning count, offline count, evidence ratio |
| Date range "24-30 Sep" | endpoints take `days`/`hours` only | **MISSING** `from`/`to` params |
| Export report | `reports.generate_report` exists | **MISSING** wiring |

Drill-through (all **MISSING**): each panel should navigate with URL params.
Seven of nine targets are Event History; the heatmap cell targets Timeline Playback
with `camera` + `date`.

### Screen 2 — Camera Management
    on mount        GET    /api/cameras                (viewer)
    Add camera      POST   /api/cameras                (admin)  -> cameras table
                                                                 -> ROISyncer picks up new camera
    Edit            PATCH  /api/cameras/{id}           (admin)
    Delete          DELETE /api/cameras/{id}           (admin)
    Settings dialog PATCH  /api/cameras/{id}           (admin)  -> detection_config JSON
                    GET    /api/cameras/{id}/roi       (viewer)
                    PUT    /api/cameras/{id}/roi       (admin)  -> ROISyncer -> nvdsanalytics
    Live preview    WS     /ws/metadata/{camera_id}             -> MetadataConsumer -> Kafka

Per-camera `detection_config` keys written by the settings dialog are read live by
the pipeline via `ROISyncer` (`_dcfg.get(...)` in `pipeline.py`).

**Live bug:** the dialog's defaults (1.5 s / 3.0 s) disagree with `event_fsm.py`
(3.0 s enter, 15.0 s violation, 10.0 s person, 20.0 s vehicle stop), and it PATCHes
only changed keys -- so an operator who opens it and saves anything silently
overwrites the pipeline defaults for that camera.

### Screen 3 — Camera Map
    on mount     GET /api/cameras   (viewer)
    pin / tab    client-side only (no fetch)
    View camera  navigate -> Timeline Playback with ?camera=

`CameraMapView.tsx:66,81` filters on `c.gps`. A camera with no GPS silently
disappears from this screen -- relevant because the GPS map picker in the add
dialog is currently skippable.

### Screen 4 — Timeline Playback
    on mount   GET /api/events?camera=&date=            (viewer)
    marker     client-side selection, no fetch
    playback   GET /api/evidence/file?...               (viewer)
    download   GET /api/evidence/file (attachment)      (viewer)
    tracking   navigate -> Identification & Tracking    (**MISSING** view)

The zoom/cluster behaviour (`groupEvents`, 30 min / 2 h / 24 h spans, the
`.range-window` brush on the day axis) is pure client-side over the fetched day's
events. No API change needed beyond the category field.

**MISSING**: `event category` filter, and clip duration -- the prototype hard-codes
"12-second clip" while `SmartRecordConfig` is pre 15 s + post 30 s, and the planned
ROI-entry recording makes length variable. Duration must come from the file.

### Screen 5 — Event History
    on mount     GET /api/events?status=&zone=&from=&to=   (viewer)
    Export CSV   GET /api/events/export/csv               (viewer)
    View clip    navigate -> Timeline Playback with params
    tracking     navigate -> Identification & Tracking    (**MISSING**)

Existing filters: `status` (`all|active|resolved`), free-text search, zone.
**MISSING**: event category, explicit date range, hour, and a filter on the
**Review** column -- the prototype renders "Reviewed" / "To review" badges that
nothing can filter or sort on. `status` (active/acknowledged/resolved) is a
different axis from review state.

### Screens 6 and 7 — Identification & Tracking
Entirely **MISSING**. Not in the `currentView` union, so unreachable.

Required and absent:
- Person ReID. No visual embedding model anywhere in the repo; the only embedding
  code is `backend/app/logs/qdrant_store.py`, sentence-transformers over log text.
- Match-group schema (`P-0042`, `V-0018`), sighting history, similarity scores.
- Plate match groups. `GET /api/lpr/plates` (viewer) exists -- the only piece
  with any backing.
- Operator verdict persistence: the screens show "Needs verification" but there is
  nowhere to record confirm/reject.
- RTO referral. Prototype states "RTO connection is not configured; nothing has
  been submitted."

### Live Monitoring (in the nav, not in the 7 screens)
    on mount  GET /api/cameras            (viewer)
              WS  /ws/alerts                        -> AlertConsumer
              WS  /ws/events                        -> EventConsumer
              WS  /ws/metadata/{camera_id}          -> MetadataConsumer
    ack       PATCH /api/alerts/{id}      (admin)
    clear all POST  /api/alerts/clear     (admin)

Note the role split: a `viewer` can watch alerts but cannot acknowledge them.
If the control-room operator is a `viewer`, they cannot clear their own queue.
Worth confirming against how the client staffs the room.

**Bug (confirmed):** the violation border never renders. `CameraFeed.tsx:185`
applies `border-transparent` as a Tailwind utility, which outranks
`.camera-feed.alert { @apply border-destructive }` in `@layer components`
(`index.css:153`). The blue seen on hover is `box-shadow: var(--glow-primary)`
(`index.css:144`), a raw property no utility overrides.

---

## 4. Detection-to-alert flow (pipeline side, for reference)

    frame -> RF-DETR-Seg (bag/new_waste/person/vehicle)
          -> nvtracker (ByteTrack, maxShadowTrackingAge 50 frames ~2 s)
          -> nvdsanalytics ROI filter
          -> event_fsm: IDLE -> ENTER -> OBSERVE -> VIOLATION -> ALERTING -> ESCALATED -> EXITED
          -> on VIOLATION: smart_record.start_recording (pre-buffer 15 s)
          -> kafka_producer.send_alert
          -> AlertConsumer -> /ws/alerts -> AlertPanel

Planned change (designed, not implemented): recording starts at ROI entry with
per-camera dedup and is discarded when no event occurs. See
`.vibe-wise/plan-roi-entry-recording.md`.

---

## 5. Cross-cutting gaps, ranked by how much they unblock

1. **Event category + actor class on `violation_events`.** Blocks 4 panels on
   Analytics, the category filter on History, and the colour-coded markers on
   Timeline. Decided: stored at detection time, windows configured per zone.
   Still open: real `zones` table vs config keyed on the existing free-text
   `cameras.zone`, and whether a window edit backfills history.
2. **Frontend auth.** Without it the app cannot run with auth enabled, and the
   silent-fallback pattern in `fetchJson` hides that fact.
3. **URL routing.** Blocks all nine Analytics drill-throughs and is the only way
   Identification & Tracking becomes reachable. Decided: real routing with query
   params.
4. **Review state as a queryable queue.** Blocks the unreviewed worklist and any
   improvement to the 42-minute detection-to-review figure.
5. **ReID + LPR match groups.** Blocks Screens 6 and 7 completely. This is a
   subsystem, not a UI task.

---

## 6. Prototype inconsistencies to fix before wiring

1. `type=pedestrian` (Event History links) vs `type=person` (Tracking links).
   `updateTimeline` only tests `type==='vehicle'`, so `pedestrian` falls through
   to the person branch and appears to work.
2. `date=30 Sep` is not parseable; an API needs ISO (`2026-09-30`). Likewise
   `?time=07:48` is matched with `startsWith`, which collides between
   `07:48:32` and `07:48:05`.
3. Actor type derived from category index (`e.cat % 2`). Correct only because the
   legend array happens to be ordered Walk/Vehicle alternating. Actor class is its
   own column in the real schema.
4. Watermark is a CSS overlay (`.footage-watermark`, `.thumb:after`) -- it does not
   survive the Download evidence button. Burn-in at encode is the only version
   that does.
5. The heatmap sample data has no in-window zero-count cells, so the visual
   difference between "in window, zero events" and "outside window" is untested.

---

## 7. Known gap: session and audit capture

Status: NOT a stated client requirement. Noted so it is not rediscovered later.

Authentication is implemented; session *tracking* is not. There is no `sessions`
table, no audit log, and no record of who was signed in when.

### What a session close does today
| Path | Effect | Captured |
|---|---|---|
| `POST /api/auth/logout` | clears both cookies, revokes refresh token at Keycloak | nothing on success; a warning only if the Keycloak revocation call fails |
| `POST /api/auth/refresh` fails | clears cookies, 401 "Session expired" | nothing |
| `get_current_user` invalid token | 401 (`middleware.py`) | nothing |
| WebSocket rejected | `logger.warning` in `auth/ws_auth.py` | warning only |
| Cookie `max_age` elapses (15 min access / 8 h refresh) | browser drops it silently | nothing, either side |

Client side, the only handling is in `lib/api-client.ts` -> `context/AuthContext.tsx`:
a 401 triggers one silent refresh, and on failure dispatches `ww:auth-failed`,
which clears the user and renders the login page. Nothing is reported to the server.

### Also uncaptured
`GET /api/evidence/file` records nothing about who downloaded which clip. In an
enforcement context "who accessed this evidence, and when" is a plausible future
requirement.

### If it becomes a requirement
Cheap first step (no schema): log logout, session expiry and evidence access at
INFO with username and source IP. Logins already log at INFO.

Durable version: an `audit_log` table (actor, action, target, timestamp, ip),
which is also the natural home for evidence views and review verdicts.

Note before building either: Keycloak keeps its own session registry and can emit
login/logout events, so part of this is already available on that side without
duplicating it here.
