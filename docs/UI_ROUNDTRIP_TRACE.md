# UI round-trip trace — div → store → producer module → render

Date: 2026-10-07. Pages: Analytics, History, Settings.
Companion to `docs/UI_ACTION_EVENT_TREES.md` (which traces actions) — this
traces **data**: where every rendered value comes from and who put it there.

---

## 0. The one fact that decides every trace

**The API container never calls the pipeline or a worker synchronously.** It
is REST + WebSocket over two stores: **Postgres** and **Redis**. The pipeline
and the worker families fill those stores asynchronously; the UI reads them
later. So "which pipeline/worker module does this API hit" is almost always
**none, live** — it reads a store a worker filled earlier.

Three producer paths, and every trace below is labelled with which one it is:

```
A. pipeline ──Kafka──> worker ──> Postgres        reads: events, alerts,
                                                   plates, evidence rows, analytics
B. pipeline / worker ──> Redis keys               reads: health, fps, heartbeat,
                                                   camera status, capture switch
C. API ──Redis PUBLISH ww:pipeline:commands──>    writes: ROI, add/remove camera,
   pipeline CommandReceiver                        (and SHOULD: config changes)
```

Consequence for the UI: a value's **freshness** is "how stale the producing
worker's last write was", not "live". Only path-B reads (health, fps) are
near-real-time. Path-A reads are as fresh as the last DB commit.

---

## 1. Analytics — all reads, no pipeline contact

Every card is path A (DB) or path B (Redis). Nothing Analytics does touches
the pipeline.

```
AnalyticsView
│
├── "Events Today" / "This Week"        [A, read]
│   GET /api/analytics/summary
│     → analytics route → aggregator.get_summary(db)      SQL only
│       → reads: violation_events
│         written by: event-fsm worker (GROUP_EVENT_FSM)
│           ← Kafka wastewatch.events
│             ← pipeline/app/event_fsm.py on VIOLATION
│   render: card number
│
├── "Repeat Offenders"                  [A, read]  ⚠ always 0
│   GET /api/analytics/summary .repeat_offenders
│     → counts violation_events.repeat_count > 0
│       but person_id is never set (ReID deferred), so repeat_count stays 0
│   render: card shows 0 regardless of reality
│
├── "Avg Response"                      [A, read]  ✗ NO STORE
│   no aggregator function, no worker computes it → card has no backing query
│
├── "Daily Trend (7 days)"  AreaChart   [A, read]
│   GET /api/analytics/daily → get_daily_trend(db)
│     → reads violation_events grouped by day
│   render: recharts AreaChart
│
├── "Last 24 Hours"  BarChart           [A, read]
│   GET /api/analytics/hourly → get_hourly_stats(db)
│   render: recharts BarChart
│
├── "Top Violation Zones"               [A, read]
│   GET /api/analytics/by-zone → get_by_zone(db)
│     → violation_events JOIN cameras JOIN zones (post-009; was free-text before)
│   render: ranked list
│
├── "Resolution Status"                 [A, read]  ✗ NO STORE
│   no endpoint; needs status counts from violation_events.status
│
└── "System Health"                     [B, read]  (admin-only → viewer sees it break)
    GET /api/health/services, /api/health/models
      → reads Redis ww:health:*
        written by: ops worker (health_monitor) + pipeline heartbeat.py
    render: status badges   near-real-time, unlike every card above it
```

**Missing entirely:** the derived-category feature (midnight-vehicle /
daytime-work …, `plan-analytics-data.md`). It needs `violation_events.category`,
which the **categoriser** (reports family, `plan-backend-worker.md`) writes.
Categoriser not built → column is NULL → no card can render it yet. This is
backend work, not UI wiring.

GET /api/analytics/by-camera **exists but no card renders it** (⚠ orphan
endpoint).

---

## 2. History — reads, plus terminal writes

Store is healthy. The writes are terminal on purpose: a reviewer's verdict
does not change detection, so nothing propagates back to the pipeline.

```
HistoryView
│
├── event table                         [A, read]
│   GET /api/alerts?limit=100
│     → alerts route → SQL → alerts table
│       written by: alerts worker (GROUP_ALERT_DISPATCHER)
│         ← Kafka wastewatch.alerts ← pipeline
│   render: table rows
│
├── snapshot thumbnail                  [A, read — file]
│   GET /api/evidence/file?path=
│     → evidence route serves a file from /app/evidence
│       file written by: pipeline/app/stream_recorder.py
│         (FFmpeg pulls the clip back from MediaMTX around the event)
│   render: <img> / evidence viewer
│
├── "Mark as Valid" / "False Positive"  [A, write — TERMINAL]
│   PATCH /api/events/{id}  is_valid = true|false
│     → SQL UPDATE violation_events.is_valid
│       nothing downstream — the verdict is the training signal, read later,
│       not fed back to the live pipeline
│   render: row badge flips
│
└── "Export Report"                     [A, read]
    GET /api/events/export/csv → SQL → streamed CSV      no worker
    render: browser download
```

**The break is the filters, not the round-trip.** Search, By Zone, By
Severity, the date range and Repeat-Offenders-Only never enter the SQL — the
query is a flat `limit=100`. The store holds everything; the query ignores
the operator. Fixing History is a query change (accept zone_id, severity,
date range, status — all of which exist post-009), not a pipeline change.

---

## 3. Settings — the page that must write to the pipeline, and doesn't

Settings is the only one of the three whose job is **path C** — change
something the pipeline acts on. Today the entire view is a mockup (9
switches, 0 onChange, 0 fetch), and even the backend half of the most
important control is broken.

### 3.1 THE BUG — config writes never reach the pipeline

```
Detection → "Confidence Threshold" (and inference interval, batch size)
  intended path C:
  PUT /api/config/{key}  → config_routes.set_config
    → writes system_config DB row                         ✓ happens
    → PUBLISH ww:pipeline:commands / set ww:config:*_override   ✗ MISSING
  pipeline actually reads:  ww:config:*_override   (Redis)
  who writes those keys TODAY:  ONLY services/healer/main.py
                                (auto-remediation), never the API
```

So a human changing a detection threshold in Settings writes a DB row **the
pipeline never reads**. The only thing that retunes the live pipeline is the
healer, autonomously. `set_config` is missing its path-C publish. This is a
real bug independent of the UI being a mockup: a fully wired Settings page
would still silently fail to change detection.

Ticket: **WW-SET-BUG-001** (below).

### 3.2 Where each row would land once wired

```
Settings
│
├── Alerts & Notifications
│   ├── Email Notifications   → config/{key} DB
│   │     read by: alerts worker when dispatching            [A]
│   ├── SMS Notifications     → ✗ no endpoint, no worker
│   ├── WhatsApp (Telegram)   → config/{key} DB
│   │     read by: alerts worker (telegram.py)               [A]
│   └── VoIP Calls            → /api/devices/* DB
│         read by: sip worker (PARKED)                        [A]
│
├── Detection Settings
│   ├── Confidence Threshold  → config/{key}  ⚠ + path C (see 3.1)
│   │     read by: pipeline (ww:config:*_override)            [C]
│   ├── Repeat Offender       → ✗ ReID deferred
│   └── Auto-capture Evidence → config/{key}
│         read by: pipeline / evidence worker                 [C or A]
│
├── Storage & Backup
│   ├── Dropbox Integration   → evidence_files.remote_backend + a secret
│   │     read by: evidence/backup worker (in 5-family scheduler, not built) [A]
│   ├── Local Storage Path    → ✗ shows "/var/wastewatch/evidence":
│   │     WRONG path (real is /app/evidence) AND the retired name
│   └── Retention Period      → tenants.retention_days (post-009)
│         read by: backup worker                              [A]
│
└── Access Control
    ├── User Management       → /api/users/*  → Keycloak Admin API
    │     THE ONE route that calls an external service LIVE (not a store)
    │     this is the in-app "signup" surface; there is no public signup
    ├── Two-Factor Auth       → ✗ Direct Access Grant has no MFA path
    └── Audit Logging         → /api/system/logs/client/capture
          → Redis ww:logs:client_capture
            read by: client-log WS + ops worker              [B]
```

### 3.3 The shape of Settings

Most rows have a backend already (`config/{key}`, `devices/*`,
`reports/recipients`, `users/*`, the capture switch). The work is **wiring,
not building** — except: fix 3.1, delete the 3 fantasy controls (SMS, 2FA,
Repeat-Offender), and correct the wrong storage-path string.

---

## 4. Summary — the three pages by round-trip type

| Page | Dominant path | Hits pipeline? | What's broken |
|---|---|---|---|
| Analytics | A-read, one B-read | no | 2 cards have no store; category cards need the categoriser to run first |
| History | A-read + terminal A-write | no | store is fine; filters never reach the SQL |
| Settings | **C-write** | **should, doesn't** | DB written, pipeline never notified; UI is a mockup on top |

**Freshness note for the UI layer:** Analytics and History render path-A data,
so they are as fresh as the last worker commit, not live — their loading
states should say "as of <commit time>" rather than implying real-time.
Only the System Health card (path B) is near-live.

---

## 5. Tickets this trace raises

```
WW-SET-BUG-001  P0  Bug  Settings
  set_config writes the DB row but never notifies the pipeline.
  config_routes.set_config must, after commit, set ww:config:{key}_override
  (which the pipeline already honours) or PUBLISH ww:pipeline:commands.
  Without it, every detection setting a human changes is silently ignored
  by the live pipeline; only the healer can retune it.
  Accept: changing Confidence Threshold in Settings changes pipeline
  behaviour within one poll cycle, verified by a pipeline log line.

WW-ANL-BUG-002  P1  Gap  Analytics
  "Avg Response" and "Resolution Status" cards render values no endpoint
  computes. Either add aggregator functions (status counts exist in
  violation_events; response time needs acknowledged_at - timestamp) or
  remove the cards. Do not ship a card wired to nothing.

WW-ANL-003  P1  Gap  Analytics
  GET /api/analytics/by-camera has no card. Add a per-camera breakdown or
  retire the endpoint.

WW-ANL-004  P2  Feature  Analytics
  Derived-category cards (midnight-vehicle etc.) depend on the categoriser
  writing violation_events.category. Blocked on the reports worker.

WW-HIS-BUG-005  P0  Bug  History
  Filters (search, zone, severity, date range, repeat-offender) do not enter
  the SQL; the query is a flat limit=100. Pass zone_id, severity, date range
  and status through to GET /api/alerts (all exist post-009). Without this,
  the evidence archive cannot be queried.

WW-HIS-006  P1  Gap  History
  No server pagination; limit=100 is hardcoded and "Previous" does nothing.
  Add keyset pagination on (timestamp, id).

WW-SET-007  P1  Chore  Settings
  Entire view is a mockup (0 fetch, 0 onChange). Wire each row to its
  existing endpoint; delete SMS, Two-Factor and Repeat-Offender (no backend);
  fix the storage-path default string (wrong path + retired name).
```
