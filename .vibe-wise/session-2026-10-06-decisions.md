# Session register — 2026-10-06

Decisions, findings and open items from this session. Detail lives in:

- `.vibe-wise/plan-user-onboarding-tenancy.md` — onboarding + tenancy
- `.vibe-wise/plan-backend-worker.md` — scheduler, 5 job families
- `docs/UI_ACTION_EVENT_TREES.md` — per-action event trees
- `docs/BACKLOG.md` — the standing backlog

---

## 1. Decisions taken

| # | Decision | Chosen |
|---|---|---|
| D1 | Who onboards whom | owner → admin → viewer |
| D2 | Viewer ownership | each admin owns their own viewers |
| D3 | Password delivery | Keycloak emails a set-password link |
| D4 | Tenancy unit | **one admin == one customer** |
| D5 | Cardinality | one admin, many viewers |
| D6 | Phone number | optional, for admin and viewer alike |
| D7 | User management | in-app, hierarchical (closes DEC-7) |
| D8 | Password field on the onboarding form | **no** — see §3.1 |
| D9 | Onboarding fields | name, email, phone (Indian), zone; collapsible multi-row add; edit + delete |
| C1 | Browser console logs → backend | yes, every session |
| C2 | Session lifetime | survives reload, ends when the refresh token expires |
| C3 | Capture level | everything (all console levels), admin and viewer |
| C4 | Transport | WebSocket primary, **POST when the WS is lost** |
| C5 | Encoding | **gzip, not encrypted** — see §3.2 |
| C6 | Sink | **Postgres only** (Qdrant dropped) |
| C7 | Capture on/off | **off by default, API call only**, with a TTL |
| I1 | Incident buffer scope | also snapshot MediaMTX online in/out streams and whitelisted Redis keys |
| R1 | Responsive | mobile + tablet from day one, installable webapp |
| W1 | Scheduler cadence | **DB rows, code defaults as fallback** |
| W2 | CPU-bound work | **asyncio only**, thread executor via `run_in_executor` |
| W5 | "VLM" in the sip family | a small local JSON→text generator; moved to reports/evidence |
| W6 | Never-drop queue persistence | **Kafka** (recommended, awaiting confirmation) |
| F1 | Job families | 7 → **5**: health_monitor + logs + healer → `ops` |
| F2 | `history-publisher` | excluded |
| F3 | `sip` family | **pending / parked** |
| P1 | Relation model placement | pipeline, option A, GPU-resident, metadata in-probe |
| P2 | Relation debug lines | nvosd `NvDsDisplayMeta`, **debug-only**, off by default |

---

## 2. Built and tested this session

| Area | Tests |
|---|---|
| `/api/users` hierarchical onboarding + tenancy | 39 |
| Browser console capture (WS + POST + toggle) | 28 |
| Incident snapshot (MediaMTX + Redis) | 10 |

Suite: **336 passed, 12 pre-existing failures** (unchanged set).

New: `app/auth/kc_admin.py`, `app/api/routes/users.py`,
`app/logs/client_ingest.py`, `app/logs/capture_switch.py`,
`app/logs/incident_context.py`, `app/ws/client_logs.py`,
`keycloak/realm-test.json`, `alembic/versions/008_log_entries_indexes.py`,
mailpit in dev + test.

---

## 3. Reasoning worth keeping

### 3.1 Why no password field on onboarding

The onboarding admin would know their subordinate's password, so every
action that viewer takes becomes deniable. Users are created with **no
credential at all**; Keycloak emails `UPDATE_PASSWORD` + `VERIFY_EMAIL`.
The real need behind the question is sites without SMTP — that is a
*temporary password with forced change at first login*, not a password
field. **Not built; still open.**

### 3.2 Why gzip and not encryption

`wss://` already encrypts transit. Any key the browser holds is readable by
the user and by any XSS, and the backend must read the logs to use them, so
a shared symmetric key is obfuscation rather than a security property. What
a blob actually buys is volume — console text compresses ~10:1. The real
controls are client-side redaction before the blob is built, the existing
subprotocol auth, and at-rest encryption in the sink if wanted.

### 3.3 Why tenancy could not key on `invited_by`

D4 made `ww_invited_by` wrong as an access boundary: replace or delete an
admin and every viewer under it is orphaned, because the replacement admin
is a different `sub`. `ww_tenant` is minted once and inherited, so it
outlives any individual account. `invited_by` is kept as provenance only.

### 3.4 Why "ring buffer" is per-job, not global

A ring overwrites the oldest entry when full. Correct for health ticks,
**data loss for alerts** — dropping an alert is losing a violation. One
shared ring means an alert storm evicts queued evidence writes. So: one
scheduler, per-job policy (never-drop / drop-oldest-and-count / coalesce).

### 3.5 Why asyncio-only still needs an executor

`async def` does not make CPU-bound code yield; there is no await point
inside a DBSCAN call. `await loop.run_in_executor(thread_pool, ...)` is
asyncio API, needs no multiprocessing, pickles nothing. It works because
numpy and sklearn release the GIL in their C inner loops — expect **jitter**
during a clustering run, not a freeze.

---

## 4. Bugs and gaps found

| Severity | Finding |
|---|---|
| **High** | `keycloak/realm-test.json` **did not exist** while test compose mounted it — test Keycloak started with an empty realm and the mount path became a directory. Created. |
| **High** | Keycloak sends the onboarding email from its **own realm SMTP**, which `WW_SMTP_*` never reached. Without a relay a user is created fine and can never sign in — a failure that looks like success in every log. Fixed dev/test (mailpit); prod is a documented console step. |
| **High** | `app/logs/worker.py` claimed `"→ embed → Qdrant + DB"` but **never wrote to the DB**, and Qdrant is absent from test and prod — every log message was consumed and dropped in both. Now writes `log_entries`. |
| **High** | `GET /api/system/logs` was Qdrant semantic search: worked in dev, 500'd everywhere else. Now filtered SQL. |
| **High** | Public-announcement safety guard **does not work**. `_SAFE_ZONE_NAME_PATTERN = ^[A-Za-z0-9 \-]{1,40}$` passes `"near the man in the blue jacket"` — 31 characters of letters and spaces. A character-class regex cannot detect that a string describes a person. An announcement can currently name an individual. Fix is the zones table, not a better pattern. One of the 12 pre-existing failures. |
| **High** | **Tenancy stops at the user table.** `/api/cameras`, `/api/events`, `/api/analytics` have no tenant filter — an admin from customer A can read customer B's cameras and evidence with a valid token. (T1) |
| Medium | `MapPicker` injects Leaflet from **unpkg at runtime** despite `leaflet` + `react-leaflet` being installed. Offline it fails `if (!L) return` → blank div, no error. Two copies of Leaflet; third-party script in an authenticated admin session. |
| Medium | Camera lat/lng has **no validation**: swapped coordinates accepted silently, out-of-range accepted, half a coordinate accepted. |
| Medium | `MapPicker` number inputs are **one-way** — typing coordinates never moves the marker (`if (mapInstanceRef.current) return`). |
| Medium | Alerts have **two live transports**: WS `/ws/alerts` and a 5 s poll, and the poll drives the notification and panel auto-open. |
| Medium | **Alert panel auto-opens with a full-screen backdrop**, blocking the whole app, from any view, rate-limited only to 1 per 3 s. |
| Medium | `list_managed_users` was **N+1** — one Admin API call per user for roles; 200 users = 201 round trips. Mine. Not yet fixed. |
| Medium | No UI role gating — a viewer sees Camera Management and Settings and discovers permission via 403. |
| Medium | **Zero responsive breakpoints** in all 7 views, layout, sidebar, alert panel, login. `gridLayout` defaults to `3x3` = nine WebRTC streams. |
| Medium | `h-screen` / `100vh` clips content on mobile browsers; needs `h-dvh`. |
| Low | `backend/.venv` was missing the declared `pydantic[email]` extra, breaking collection of `test_ws_metadata.py`. |
| Low | Tile source mismatch: picker uses OSM light, `CameraMapView` uses CartoDB dark. |

Carried from earlier sessions and still open: `mediamtx.yml:45` hardcodes
`192.168.1.8`; `WW_AUTH_DEV_MODE=1` grants owner+admin+viewer and bypasses
REST and WS auth; the HF token pasted in plaintext is unrotated.

---

## 5. Open items

| # | Item | Needs |
|---|---|---|
| W6 | Confirm Kafka for never-drop queues | one word |
| W3 | IVR DTMF transport (provider webhook vs on-prem Asterisk) | parked with sip |
| W4 | IVR ack strength — whoever answers can press 1 | parked with sip |
| D8 | Temporary-password fallback for no-SMTP sites | decision |
| T1 | Tenant filter on cameras / events / analytics | design — bigger than the user work |
| L1 | Log retention / prune, in the event pool not a 6th timer | decision |
| U1 | `UserManagementView` — collapsible add rows, validation, edit, delete | build |
| U2 | Client-log shim `frontend/src/lib/client-logger.ts` | build |
| U3 | Responsive pass + PWA manifest | build |
| U4 | Scheduler core: registry, DB cadence with fallback, one SIGTERM handler, supervised restart | build |
| U5 | Relation runner (option A, GPU-resident, fail-open to dwell FSM) | build |
| U6 | Map picker: drag-pin primary, search, GPS button, validated two-way number fields | decision on primary input |
| U7 | Fix `list_managed_users` N+1 | build |

---

## 6. Standing constraints

- `WW_AUTH_DEV_MODE` must be 0/absent in test and prod; `start.sh` refuses prod if set
- Keycloak realm roles must be **composite** owner→admin→viewer
- Keycloak **Direct Access Grants** must be enabled on the client
- Service account needs `realm-management`: manage-users, view-users, query-users
- Prod `WW_AUTH_COOKIE_SECURE=true` requires HTTPS or the session cookie is dropped silently
- Screen capture cannot be prevented in a browser — do not promise it in writing
- MIVIA IWDD data and paper are CC BY 4.0 (commercial use permitted with attribution)
