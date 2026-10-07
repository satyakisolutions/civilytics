# Civilytics — consolidated backlog

Compiled 6 Oct 2026. Every item below was verified against the code in this
repo; file:line references are real. Nothing here is speculative unless marked
**SPECULATIVE**.

Legend
- **FIX** — something is broken or wrong now
- **IMPL** — does not exist, needs building
- **OPT** — works, could be better
- **DEC** — a decision is blocking the work; see §9

Effort: S = under an hour · M = half a day · L = multiple days · XL = a week+

---

## 1. Already done this session (for reference, not backlog)

| # | Item | Where |
|---|---|---|
| D1 | WebSocket authentication on all three `/ws/*` endpoints | `backend/app/auth/ws_auth.py`, `ws/{alerts,events,metadata}.py` |
| D2 | Silent-401 interceptor + cookie session on every API call | `frontend/src/lib/api-client.ts`, `main.tsx` |
| D3 | Branded login with server-side Keycloak token exchange | `backend/app/api/routes/auth.py`, `frontend/src/pages/Login.tsx`, `context/AuthContext.tsx` |
| D4 | Alert ack -> viewer; bulk clear -> admin; event review -> viewer | `routes/alerts.py`, `routes/events.py` |
| D5 | Static dirs made configurable (unblocked 50 erroring tests) | `config/settings.py`, `main.py` |
| D6 | Tests: 6 WS-auth, 3 WS-metadata rewritten, 2 cookie-session | `backend/tests/` |

Verified: failing-test set byte-identical before/after; pass count 182 -> 240.

---

## 2. P0 — Security

| # | Type | Item | Evidence | Effort |
|---|---|---|---|---|
| S1 | FIX | Six `/api/health/*` sub-endpoints are unauthenticated and leak camera inventory, speaker/device topology and system resources. The same data under `/api/system/health/*` requires `admin`. Keep bare `GET /api/health` public for probes. | `routes/health.py:89,105,116,123,136,143` | S |
| S2 | FIX | No role-gated UI. A `viewer` sees Add Camera, the per-card settings gear, and Clear All, all of which 403. `can('admin')` exists but is unwired. | `AuthContext.tsx`, `CameraManagementView.tsx:148`, `AlertPanel.tsx:63` | M |
| S3 | FIX | `WW_AUTH_DEV_MODE` bypasses REST **and** WebSocket auth entirely, granting owner+admin+viewer. Must be provably off in production; currently nothing asserts that. | `auth/middleware.py`, `auth/ws_auth.py` | S |
| S4 | IMPL | Missing log lines: logout success, session expiry, and evidence access (who downloaded which clip). Logins already log at INFO; the asymmetry is accidental. | `routes/auth.py`, `routes/evidence.py` | S |
| S5 | OPT | Exposed HF token `hf_Xilut...` was pasted in plaintext in an earlier session. Treat as compromised; rotate. | session history | S |
| S6 | DEC | Keycloak realm needs composite roles (owner -> admin -> viewer). Realm roles are flat and `require_role()` matches the exact string, so a super admin is currently denied admin endpoints. | `auth/keycloak.py:has_role` | S |
| S7 | IMPL | **SPECULATIVE** — durable `audit_log` table (actor, action, target, ts, ip). Not a stated client requirement; see `SCREEN_EVENT_FLOWS.md` §7. Keycloak already keeps a session registry. | — | M |

---

## 3. P1 — Blocking foundations

These gate most of §4. Nothing in the screens works properly until they land.

| # | Type | Item | Evidence | Effort |
|---|---|---|---|---|
| F1 | IMPL | **Event category** (Midnight/Daytime x Walk/Vehicle). Needs two things that are not persisted: **actor class** on `violation_events` (schema has only `person_id TEXT`) and **peak window config** (exists nowhere). Decided: stored at detection time, windows per zone. Blocks 4 Analytics panels, History's category filter, and Timeline's colour-coded markers. | `migrations/...sql:31-47`, `event_type` enum line 11 | L |
| F2 | IMPL | **Zones as a managed entity.** `cameras.zone` is bare `TEXT NOT NULL`; `alerts.zone` duplicates it. No zones table, so per-zone window config has nowhere to live, and a typo silently creates a zone. | `migrations/...sql:17,60` | M |
| F3 | IMPL | **URL routing** (decided: real routing, query params). `App.tsx` has one route; view switching is a 7-string union in context. Blocks all 9 Analytics drill-throughs; Identification & Tracking is unreachable because it is not even in the union. | `App.tsx`, `context/MonitoringContext.tsx:19-20` | M |
| F4 | IMPL | **Review state as a queryable queue.** History filters `all/active/resolved` (a different axis); the prototype renders Reviewed / To review badges nothing can filter or sort. This is what the 42-minute detection-to-review metric measures. | `HistoryView.tsx:43` | M |
| F5 | IMPL | Multi-series analytics aggregation. `DailyPoint{date,count}` and `HourlyPoint{hour,count}` need a category breakdown. No new endpoints required. | `hooks/useAnalytics.ts:17-28`, `analytics/aggregator.py` | M |
| F6 | IMPL | Explicit date range on analytics. Endpoints take `days`/`hours` (trailing windows only); the spec header reads "24-30 Sep 2026". | `routes/analytics.py:15,19` | S |

---

## 4. P2 — Screen features

### 4.1 Camera onboarding (plan: `.vibe-wise/plan-camera-onboarding.md`)
| # | Type | Item | Effort |
|---|---|---|---|
| C1 | IMPL | Empty-state CTA on the landing page routing to Camera Management | S |
| C2 | IMPL | On add success, open the settings modal instead of closing (state change only) | S |
| C3 | IMPL | Stream connection test. ROI is drawn on a live feed, so a syntactically valid RTSP URL that fails to stream blocks setup. Mandatory ROI needs a real connection result to gate on. | M |
| C4 | DEC | What "mandatory" means when the feed is down: (a) blocked at creation, (b) saved "setup incomplete", (c) saved but flagged unusable with the pipeline skipping it | — |
| C5 | IMPL | Migrate or exempt existing cameras with no ROI (three were parked earlier) | S |
| C6 | FIX | A camera without GPS silently vanishes from the Camera Map (`filter(c => c.gps)`), and the map picker is currently skippable | S |

Dependency chain for this flow: `stream URL -> live feed -> ROI -> thresholds`.

### 4.2 Analytics (plans: `plan-analytics-data.md`, `plan-analytics-drillthrough.md`)
| # | Type | Item | Effort |
|---|---|---|---|
| A1 | IMPL | Drill-through on 9 panels. Seven of nine target History, which makes History's filter contract the real design object. Blocked by F1 + F3. | M |
| A2 | IMPL | Heatmap: colour non-zero by category, blank zero cells, shade out-of-window. Blocked by F1. | S |
| A3 | IMPL | Violation categories donut. Blocked by F1. | S |
| A4 | IMPL | Daily Peak Pattern, 4 series x 7 days. Blocked by F1 + F5. | S |
| A5 | IMPL | System health card: stream-warning count, offline count, evidence-available ratio (`evidence_files` supports the last) | S |
| A6 | IMPL | Wire Export report to `reports.generate_report` | S |
| A7 | IMPL | Repeat match groups split person/vehicle. Blocked by §6 entirely — only a flat `repeat_offenders` int exists. | — |

### 4.3 Timeline Playback
| # | Type | Item | Effort |
|---|---|---|---|
| T1 | IMPL | Cluster + zoom: 24 h overview axis with a brush, detail axis at 30 min / 2 h / 24 h, cluster markers with a count that expand on click. Fully specified by the prototype. | M |
| T2 | IMPL | Category-coloured markers. Blocked by F1. | S |
| T3 | FIX | Clip duration must come from the file. The prototype hard-codes "12-second clip"; `SmartRecordConfig` is pre 15 s + post 30 s, and ROI-entry recording (P1 below) makes length variable. | S |
| T4 | DEC | What the detail pane shows on arrival before any marker is clicked: empty prompt, or auto-select the day's first event | — |
| T5 | DEC | Do History and Timeline stay separate screens? The learner called the Timeline target "camera based History"; the spec keeps them separate. | — |

### 4.4 Event History
| # | Type | Item | Effort |
|---|---|---|---|
H1 | IMPL | Category, date-range and hour filters. Blocked by F1. | M |
H2 | IMPL | Filter/sort on review state. Blocked by F4. | S |
H3 | IMPL | Wire Export CSV (endpoint exists: `GET /api/events/export/csv`) | S |

### 4.5 Live Monitoring
| # | Type | Item | Effort |
|---|---|---|---|
| M1 | FIX | **Violation border never renders.** `CameraFeed.tsx:185` applies `border-transparent` as a Tailwind utility, which outranks `.camera-feed.alert { @apply border-destructive }` in `@layer components` (`index.css:153`). The blue on hover is `box-shadow: var(--glow-primary)` (`index.css:144`), a raw property no utility overrides. | S |
| M2 | FIX | Hover rule guards on the wrong class: `:not(.animate-alert-border)` (`index.css:142`) while the red state is `.alert`. Both are applied together so it works by accident. | S |
| M3 | DEC | Hover vs alert precedence on an actively-violating panel; and whether a just-ended violation holds the highlight briefly (`alertHeldUntil` exists at `CameraFeed.tsx:180`) | — |
| M4 | IMPL | Push notification. The seven screens are all pull; `AlertPanel` is a panel you open. This is the 42-minute bottleneck. | M |

### 4.6 Identification & Tracking (Screens 6-7)
Entirely missing — see §6.

### 4.7 Auth / onboarding pages
| # | Type | Item | Effort |
|---|---|---|---|
| U1 | IMPL | Super admin onboarding: workspace name, zones, global config keys | M |
| U2 | IMPL | Admin onboarding = the camera onboarding flow in 4.1 | — |
| U3 | IMPL | User onboarding: orientation only (alert colours, where evidence lives) | S |
| U4 | DEC | User management in-app, or defer to the Keycloak admin console? Deferring is far less work; in-app means proxying the Keycloak Admin REST API behind an owner-only service account. | — |

---

## 5. P3 — Pipeline and detection

| # | Type | Item | Evidence | Effort |
|---|---|---|---|---|
| P1 | IMPL | **ROI-entry recording** with per-camera dedup and discard-on-no-event. Designed and confirmed, not built. Re-keys `_active` from `(camera_id, track_id)` to `camera_id`, adds an occupancy refcount and `latch_culprit()`. | `.vibe-wise/plan-roi-entry-recording.md`, `smart_record.py:103 vs 427` | M |
| P2 | FIX | Threshold defaults disagree with the pipeline. Dialog uses 1.5 s / 3.0 s; `event_fsm.py` uses 3.0 enter / 15.0 violation / 10.0 person / 20.0 vehicle-stop. The dialog PATCHes only changed keys, so an operator who opens it and saves anything silently overwrites the pipeline defaults for that camera. Making the dialog mandatory on every add turns this from an edge case into the default path. | `CameraSettingsDialog.tsx:22-23`, `event_fsm.py:41-45` | S |
| P3 | FIX | Stale hint text: "Violation threshold" is described as applying to a "non-person track (e.g. vehicle)", but vehicles now use `vehicle_stop_threshold_s: 20.0`, which the dialog does not expose. | `CameraSettingsDialog.tsx:37-38` | S |
| P4 | IMPL | IWDD scoring runner: play each MIVIA clip through the pipeline, log alert timestamps, score TP/FP/FN at dt=5 s / T_max=10 s, report P/R/F1 + D_norm split by static/dynamic and day/night. Baseline to beat: F1 0.79. | `docs/IWDD_VALIDATION.md` | L |
| P5 | OPT | Re-tune FSM thresholds against the 10 s acceptance window once P4 can measure. Current 15-20 s thresholds fall at or outside it, so correct escalations would score as false negatives. | `event_fsm.py` | M |
| P6 | IMPL | Watermark burn-in at encode. The prototype's `.footage-watermark` is CSS and disappears the moment a clip is downloaded. Only burn-in survives. | `index.css` (prototype) | M |
| P7 | DEC | Watermark: burn-in, playback overlay, or both? | — |
| P8 | IMPL | **Waste temporal FSM** NEW -> OLD -> RETIRED. Parked by the learner. Blocked on identity (see §9 DEC-6). Purpose beyond reporting: a pile that stays alert-eligible remains a candidate for relation pairing with any later passer-by, risking attribution of existing garbage to an innocent person. | `.vibe-wise/waste-identity-problem.md` | XL |
| P9 | OPT | `max_duration_s: 120` truncates a long loiter under ROI-entry recording. Raising it costs GPU memory; measure real clip lengths first. | `smart_record.py` | S |

---

## 6. P4 — Identity subsystem (ReID / LPR)

Blocks all of Screens 6-7 and item A7. This is a subsystem, not a UI task.

| # | Type | Item | Evidence | Effort |
|---|---|---|---|---|
| R1 | IMPL | Person ReID. **Nothing visual exists in the repo** — the only embedding code is `backend/app/logs/qdrant_store.py`, sentence-transformers over log *text*. | verified by grep | XL |
| R2 | IMPL | Match-group schema (`P-0042`, `V-0018`), sighting history, similarity scores | — | L |
| R3 | IMPL | Operator verdict persistence. Screens show "Needs verification" with nowhere to record confirm/reject, so the next operator re-reviews from scratch. | — | M |
| R4 | OPT | Plate match groups. `GET /api/lpr/plates` exists — the only piece with any backing. | `routes/lpr.py:12` | M |
| R5 | IMPL | RTO referral. Prototype states "RTO connection is not configured; nothing has been submitted." | — | L |
| R6 | DEC | Footage protection. Screen capture **cannot** be prevented in a browser; `nodownload` and right-click blocking are cosmetic. Enforceable: short-TTL signed URLs, stricter authz on `evidence/file`, access logging, per-viewer forensic watermark. DRM (Widevine/FairPlay) is high cost for partial coverage. Do not promise capture prevention in writing. | — | — |

---

## 7. Confirmed bugs (consolidated)

| # | Bug | Location | In §|
|---|---|---|---|
| B1 | Violation border can never render (Tailwind utility outranks component layer) | `CameraFeed.tsx:185` / `index.css:153` | M1 |
| B2 | Hover guard uses the wrong class name | `index.css:142` | M2 |
| B3 | Threshold defaults overwrite pipeline values on any save | `CameraSettingsDialog.tsx:22-23` | P2 |
| B4 | Stale "non-person track" hint; `vehicle_stop_threshold_s` unexposed | `CameraSettingsDialog.tsx:37` | P3 |
| B5 | Cameras without GPS silently vanish from the map | `CameraMapView.tsx:66,81` | C6 |
| B6 | Six `/api/health/*` endpoints unauthenticated | `routes/health.py` | S1 |
| B7 | `smart_record` docstring claims per-source; code keys per-track | `smart_record.py:103` vs `427` | P1 |

### Prototype inconsistencies to fix before wiring
| # | Issue |
|---|---|
| B8 | `type=pedestrian` (History links) vs `type=person` (Tracking links). `updateTimeline` only tests `=== 'vehicle'`, so `pedestrian` falls through and *appears* to work. |
| B9 | `date=30 Sep` is unparseable; needs ISO. `?time=07:48` matched with `startsWith` collides between `07:48:32` and `07:48:05`. |
| B10 | Actor type derived from category index (`e.cat % 2`) — correct only because the legend array happens to alternate Walk/Vehicle. |
| B11 | Watermark is CSS; does not survive download. |
| B12 | Heatmap sample data has no in-window zero cells, so "in window, zero events" vs "outside window" is visually untested. |

---

## 8. Research / model work in flight

| # | Item | State |
|---|---|---|
| X1 | SAM3 masking of MIVIA IWDD | **running** — ~3.4k/21,747 frames, ~10 h total |
| X2 | SAM3 masking of Civil footage | done — 20,698 frames |
| X3 | Relation model v2 | trained, best val 0.3136; tail epochs overfit, best checkpoint is mid-run |
| X4 | Triplet review | 16/608 reviewed by the learner |
| X5 | Merge accepted triplets into relation training | pending X4; pool all 608, re-split, keep Littering frames out of validation |
| X6 | MIVIA negatives (250 clips of actors near waste *not* dumping) | extracted; this is the class the relation model most lacks |
| X7 | Low-resolution MIVIA clips (31 with a side under 360 px) | included; decide whether to drop once zero-detection counts are known |

---

## 9. Blocking decisions

| # | Decision | Blocks |
|---|---|---|
| DEC-1 | Zones: real table + FK, or config keyed on the existing free-text `cameras.zone`? | F1, F2 |
| DEC-2 | On a peak-window edit: backfill that zone's history, or leave as-written and caption each period? | F1 |
| DEC-3 | What "mandatory ROI" does when the feed is down (a/b/c) | C3, C4 |
| DEC-4 | Hover vs alert outline precedence; highlight hold after a violation ends | M1, M3 |
| DEC-5 | Watermark: burn-in, overlay, or both | P6, P7 |
| DEC-6 | Waste identity key surviving track churn and restarts; NEW/OLD/RETIRED thresholds; clearance detection | P8 |
| DEC-7 | User management in-app vs Keycloak admin console | U4 |
| DEC-8 | Timeline arrival state; History vs Timeline as one screen or two | T4, T5 |
| DEC-9 | Fan-out commit point for CRITICAL — dynamic dumping needs instant commit, the retrieval negative needs delayed commit, and irreversible side effects (messaging, enforcement record) cannot be retracted | pipeline alerting |
| DEC-10 | Is footage protection a client commitment or an exploration? If "no downloads" has been promised, that needs correcting before contract. | R6 |

---

## 10. Suggested order

Rationale: security first because it is small and currently exploitable; then the
four foundations, because 15+ feature items are blocked on them; then screens.

**Phase 0 — security (about a day)**
S1, S2, S3, S4, S6. All small, no decisions needed except DEC-nothing. S5 (token
rotation) is yours to do, not mine.

**Phase 1 — quick bug sweep (half a day)**
B1, B2, B3, B4, B5, P3, T3. All small, all independent, all user-visible. Doing
these before the foundations means the app stops lying about its state while the
bigger work proceeds.

**Phase 2 — foundations (needs DEC-1, DEC-2 first)**
F2 (zones) -> F1 (category + actor class) -> F5 (multi-series) -> F6 (date range).
F3 (routing) can run in parallel, it shares no code. F4 (review queue) after F1.

**Phase 3 — screens**
Analytics A2-A6 and the drill-throughs A1 become small once F1/F3/F5 land.
History H1-H3. Timeline T1-T2. Camera onboarding C1-C6 (needs DEC-3).

**Phase 4 — pipeline**
P1 (ROI-entry recording, already designed), then P4 (IWDD runner) which is what
makes P5 (threshold re-tuning) measurable rather than guesswork. P6 watermark
burn-in needs DEC-5.

**Phase 5 — identity**
R1-R5. Largest block, gates Screens 6-7 and A7. Worth deciding whether the client
needs it before committing a week-plus. P8 (waste FSM) shares the identity problem
and is parked pending DEC-6.

**Continuous**
X1 finishes on its own; X4-X5 is learner-paced review work that feeds model
quality rather than the product.
