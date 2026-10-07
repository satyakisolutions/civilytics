# Sample data — how the tables connect

Date: 2026-10-07. Schema as of migration `009`.

A worked dataset: two customers, five cameras, one night-time vehicle dump,
one daytime dump, one outlier, and the alerts, evidence and logs they
produce. Every foreign key below resolves to a row in this document.

**IDs are abbreviated for reading.** Real columns are `uuid`. `T1` stands
for `11111111-0000-0000-0000-000000000001`, `C3` for
`cccccccc-0000-0000-0000-000000000003`, and so on — the prefix letter
indicates the table, the final digit the row.

---

## 0. Key graph

```
tenants (T)
├── PK id
└──◄ zones.tenant_id
    └──◄ cameras.tenant_id

zones (Z)
├── PK id
├── FK tenant_id ──► tenants.id
└──◄ cameras.zone_id
    └──◄ alerts.zone_id

cameras (C)
├── PK id
├── FK tenant_id ──► tenants.id
├── FK zone_id   ──► zones.id
└──◄ violation_events.camera_id
    ├──◄ track_sessions.camera_id
    ├──◄ plate_reads.camera_id
    └──◄ alerts.camera_id

violation_events (E)
├── PK id
├── FK camera_id ──► cameras.id        ← the ONLY path to a tenant
└──◄ alerts.event_id
    └──◄ evidence_files.event_id

track_sessions (S)
├── PK id
├── UNIQUE (camera_id, track_id, entered_at)   ← not (camera_id, track_id)
├── FK camera_id ──► cameras.id
└──◄ plate_reads.track_session_id

alerts (A)
├── PK id
├── FK event_id  ──► violation_events.id   (NULL for system alerts)
├── FK camera_id ──► cameras.id            (NULL for system-wide)
├── FK zone_id   ──► zones.id
└──◄ alert_deliveries.alert_id

standalone (no FK in or out)
├── scheduler_jobs   PK job      (natural key)
├── system_config    PK key      (natural key)
├── tts_cache        PK id, UNIQUE text_hash
└── log_entries      PK id       (session grouped via metadata->>'session_id')
```

**Tenancy is inherited, never repeated.** Only `zones` and `cameras` carry
`tenant_id`. Every event, alert, track, plate and evidence file reaches its
customer through `camera_id`. Repeating the column would create a second
source of truth that can disagree with the first.

---

## 1. `tenants` — 2 rows

| id | name | is_active | retention_days | created_at |
|---|---|---|---|---|
| **T1** | Adugodi Ward Office | true | 365 | 2026-04-02 09:14+05:30 |
| **T2** | Lakkasandra Ward Office | true | 90 | 2026-07-18 11:02+05:30 |

`T1` keeps evidence a year, `T2` ninety days — a contractual term, which is
why it is a column and not a constant.

`T1.id` is also the value written into the `ww_tenant` Keycloak attribute of
every user belonging to Adugodi. That is the join between authorisation and
data.

---

## 2. `zones` — 6 rows

| id | tenant_id | name | is_speakable | eps_minutes | min_events |
|---|---|---|---|---|---|
| **Z1** | T1 | Adugodi Main Road | **true** | 45 | 8 |
| **Z2** | T1 | Adugodi Market Rear | **true** | 45 | 8 |
| **Z3** | T1 | Unassigned | false | 45 | 6 |
| **Z4** | T2 | Lakkasandra Lake Edge | **true** | 60 | 4 |
| **Z5** | T2 | Lakkasandra Depot Gate | false | 60 | 4 |
| **Z6** | T2 | Behind the blue house | false | 60 | 4 |

Three things this table is doing:

- **`Z6` is why `is_speakable` exists.** "Behind the blue house" is a
  perfectly good operator label and must never be broadcast from a street
  speaker — it points at somebody's home. No regex can tell that; a person
  decided it once, when the zone was created. `Z3` ("Unassigned") is false
  for the same reason: a placeholder name must not be announced.
- **`eps_minutes` / `min_events` differ by tenant** — 45/8 at Adugodi,
  60/4 at Lakkasandra. A global clustering radius would mis-cluster one of
  them by construction.
- `UNIQUE (tenant_id, name)` means both customers could have a zone called
  "Main Road" without collision.

---

## 3. `cameras` — 5 rows

| id | tenant_id | zone_id | name | status | gps_lat | gps_lng | last_event_at |
|---|---|---|---|---|---|---|---|
| **C1** | T1 | Z1 | Entry | online | 12.938210 | 77.610450 | 2026-10-06 02:14+05:30 |
| **C2** | T1 | Z1 | Main-North | online | 12.938900 | 77.611020 | 2026-10-05 14:51+05:30 |
| **C3** | T1 | Z2 | Market-Rear | **offline** | 12.937100 | 77.609880 | 2026-10-03 23:40+05:30 |
| **C4** | T2 | Z4 | Lake-East | online | 12.942500 | 77.598300 | 2026-10-06 03:02+05:30 |
| **C5** | T2 | Z5 | Depot-Gate | online | *NULL* | *NULL* | *NULL* |

`detection_config` (JSONB), shown separately for width:

| id | detection_config |
|---|---|
| C1 | `{"dwell_violation_threshold_s": 12.0, "waste_proximity_px": 140}` |
| C2 | `{}` → all pipeline defaults |
| C3 | `{"dwell_violation_threshold_s": 25.0}` — busy market, fewer false positives |
| C4 | `{"waste_proximity_alert_window_s": 8.0}` |
| C5 | `{}` |

- **C5 has NULL/NULL coordinates, not 0/0.** The `ck_cameras_gps_pair`
  CHECK enforces both-or-neither, so a camera is either placed or honestly
  unplaced. A half-filled pair would render in the Gulf of Guinea.
- `UNIQUE (tenant_id, name)` — T2 could also have a camera called "Entry".
- **C3 is offline** and its `last_event_at` is three days stale. That pair
  is the camera-loss signal.

---

## 4. `track_sessions` — 6 rows

| id | camera_id | track_id | fsm_state | entered_at | exited_at | dwell_time_s |
|---|---|---|---|---|---|---|
| **S1** | C1 | 42 | exited | 2026-10-06 02:13:41 | 02:14:19 | 38.0 |
| **S2** | C1 | **42** | exited | 2026-10-06 02:16:05 | 02:16:58 | 53.0 |
| **S3** | C1 | 43 | escalated | 2026-10-06 02:14:02 | 02:15:30 | 88.0 |
| **S4** | C2 | 7 | exited | 2026-10-05 14:50:10 | 14:51:44 | 94.0 |
| **S5** | C4 | 11 | violation | 2026-10-06 03:01:20 | 03:03:05 | 105.0 |
| **S6** | C4 | 12 | active | 2026-10-07 08:02:11 | *NULL* | *NULL* |

**S1 and S2 are the reason `entered_at` is in the unique key.** Both are
`camera_id = C1, track_id = 42`, 144 seconds apart — different people.
nvtracker recycles an id roughly two seconds after a track dies
(`maxShadowTrackingAge: 50` at ~25 fps), so `UNIQUE (camera_id, track_id)`
would have rejected S2 or, worse, merged two strangers into one session and
attributed one person's dwell time to the other.

**S6 has `exited_at = NULL`** — still in frame. That is how the FSM's
liveness check finds live tracks, so NULL here is a state, not missing data.

---

## 5. `violation_events` — 7 rows

| id | camera_id | timestamp | event_type | severity | confidence | track_id | dwell_time_s |
|---|---|---|---|---|---|---|---|
| **E1** | C1 | 2026-10-06 02:14:30 | dumping_detected | critical | 0.91 | 43 | 88.0 |
| **E2** | C1 | 2026-10-06 02:16:40 | suspicious_activity | warning | 0.54 | 42 | 53.0 |
| **E3** | C2 | 2026-10-05 14:51:20 | dumping_detected | critical | 0.88 | 7 | 94.0 |
| **E4** | C3 | 2026-10-03 23:40:12 | dumping_detected | critical | 0.79 | 19 | 61.0 |
| **E5** | C4 | 2026-10-06 03:02:50 | dumping_detected | critical | 0.93 | 11 | 105.0 |
| **E6** | C4 | 2026-09-29 16:22:05 | dumping_detected | warning | 0.62 | 4 | 31.0 |
| **E7** | C1 | 2026-10-04 05:41:33 | dumping_detected | critical | 0.84 | 88 | 77.0 |

Review and categorisation state:

| id | category | actor_class | category_run_id | is_valid | acknowledged_by | status |
|---|---|---|---|---|---|---|
| E1 | midnight-vehicle | vehicle | R7 | **true** | priya.n | resolved |
| E2 | *NULL* | person | R7 | **false** | priya.n | resolved |
| E3 | daytime-work | person | R7 | **true** | priya.n | resolved |
| E4 | midnight-work | person | R7 | *NULL* | *NULL* | active |
| E5 | midnight-vehicle | vehicle | R7 | **true** | ravi.k | acknowledged |
| E6 | *NULL* | vehicle | R7 | *NULL* | *NULL* | active |
| E7 | *NULL* | person | R7 | *NULL* | *NULL* | active |

Three distinctions this table is built to preserve:

- **`category` is NULL on purpose for E2, E6 and E7.** E7 is at 05:41 — an
  odd hour that belongs to no dense cluster. E6 is a lone afternoon event at
  Lakkasandra, below that zone's `min_events = 4`. They are *recorded as
  events* and *not categorised*. A NOT NULL column would have forced each
  into the nearest bucket, inventing a pattern that is not there.
- **`is_valid` is three-state.** E1 true (genuine), E2 false (reviewed, a
  false positive — note it was still `acknowledged_by` priya.n, because
  reviewing and dismissing is work), E4/E6/E7 NULL (nobody has looked).
  Collapsing NULL and false would destroy the training signal: "not yet
  reviewed" and "the model was wrong" are opposite facts.
- **`category_run_id = R7` on every row.** Re-running the categoriser with
  new parameters writes `R8` and may move E6 into a category. Without this
  column nobody could tell which events moved, or which run's logic a
  historical category came from.

`confidence` matters at E2 (0.54) — it was dismissed, and the score is why
that dismissal is defensible.

---

## 6. `alerts` — 8 rows

| id | event_id | camera_id | zone_id | severity | channel | status |
|---|---|---|---|---|---|---|
| **A1** | E1 | C1 | Z1 | critical | dashboard | resolved |
| **A2** | E1 | C1 | Z1 | critical | speaker | resolved |
| **A3** | E1 | C1 | Z1 | critical | telegram | resolved |
| **A4** | E3 | C2 | Z1 | critical | dashboard | resolved |
| **A5** | E4 | C3 | Z2 | critical | dashboard | active |
| **A6** | E5 | C4 | Z4 | critical | dashboard | acknowledged |
| **A7** | E5 | C4 | Z4 | critical | sip | acknowledged |
| **A8** | *NULL* | C3 | Z2 | warning | dashboard | active |

Acknowledgement:

| id | acknowledged_by | acknowledged_at | ack_source |
|---|---|---|---|
| A1 | priya.n | 2026-10-06 02:19:03 | **dashboard** |
| A2 | priya.n | 2026-10-06 02:19:03 | dashboard |
| A3 | priya.n | 2026-10-06 02:19:03 | dashboard |
| A4 | priya.n | 2026-10-05 15:02:40 | dashboard |
| A5 | *NULL* | *NULL* | *NULL* |
| A6 | ravi.k | 2026-10-06 03:09:55 | dashboard |
| A7 | *+919845012345* | 2026-10-06 03:08:12 | **ivr** |
| A8 | *NULL* | *NULL* | *NULL* |

- **One event, three alerts.** E1 raised A1/A2/A3 on three channels. This is
  why delivery is not a column on the event.
- **A8 has `event_id = NULL`.** It is the camera-offline alert for C3 —
  a real alert with no violation behind it, which is why the FK is nullable.
- **A7 is the case `ack_source` exists for.** It was acknowledged by phone
  at 03:08, a minute *before* ravi.k acknowledged A6 at the dashboard.
  `acknowledged_by` holds a phone number, not a username, because nobody
  authenticated — whoever answered pressed a key. Recorded identically to
  A6, it would be indistinguishable from an authenticated act on a record a
  prosecution may rely on.
- `acknowledged_by` is a string, not an FK: if priya.n is later removed from
  Keycloak, the attribution must survive her account.

---

## 7. `alert_deliveries` — 7 rows

| id | alert_id | channel | status | retry_count | delivered_at | error_message |
|---|---|---|---|---|---|---|
| **D1** | A2 | speaker | sent | 0 | 2026-10-06 02:14:36 | *NULL* |
| **D2** | A3 | telegram | **failed** | 3 | *NULL* | `HTTP 429 Too Many Requests` |
| **D3** | A3 | telegram | sent | 4 | 2026-10-06 02:17:55 | *NULL* |
| **D4** | A4 | dashboard | sent | 0 | 2026-10-05 14:51:25 | *NULL* |
| **D5** | A7 | sip | **failed** | 1 | *NULL* | `no answer after 30s` |
| **D6** | A7 | sip | sent | 2 | 2026-10-06 03:07:40 | *NULL* |
| **D7** | A5 | dashboard | **pending** | 0 | *NULL* | *NULL* |

D2 → D3 is one Telegram message that took four tries over a rate limit.
D5 → D6 is the escalation cascade: first contact did not answer, second did,
and that second call is the one whose keypress acknowledged A7.

`status` is an enum (`delivery_status`), so `"failed"` / `"Failed"` /
`"failure"` cannot coexist and quietly break the retry query.

---

## 8. `plate_reads` — 5 rows

| id | camera_id | track_session_id | plate_number | plate_normalised | confidence |
|---|---|---|---|---|---|
| **P1** | C1 | S3 | `KA 01 AB 1234` | `KA01AB1234` | 0.88 |
| **P2** | C4 | S5 | `KA-01-AB-1234` | `KA01AB1234` | 0.81 |
| **P3** | C4 | S5 | `KA01AB1234` | `KA01AB1234` | 0.93 |
| **P4** | C2 | S4 | `KA 05 MJ 8871` | `KA05MJ8871` | 0.76 |
| **P5** | C1 | *NULL* | `KA 41 C 0 09` | `KA41C009` | **0.42** |

- **P1, P2 and P3 are the same vehicle**, read three times in three
  formats across two cameras and two tenants. On `plate_number` alone they
  are three vehicles and repeat-offender detection finds nothing. On
  `plate_normalised` they are one — which is the only thing this table is
  for.
- **P5 at 0.42 should not convict anyone.** It is stored because a weak read
  is still a lead, and `confidence` is what stops it being treated as fact.
  Its `track_session_id` is NULL — a plate seen with no confirmed session.
- `plate_number` keeps the raw string because it is evidence; the normalised
  form is derived and indexed.

---

## 9. `evidence_files` — 7 rows

| id | event_id | file_type | sync_status | remote_backend | checksum (head) |
|---|---|---|---|---|---|
| **F1** | E1 | snapshot | synced | s3 | `9f2a4c…` |
| **F2** | E1 | **annotated_snapshot** | synced | s3 | `71bb0e…` |
| **F3** | E1 | video_clip | synced | s3 | `c40d13…` |
| **F4** | E1 | metadata_json | synced | s3 | `aa19f7…` |
| **F5** | E3 | snapshot | **failed** | gdrive | `3e8c22…` |
| **F6** | E5 | snapshot | **uploading** | dropbox | `0b7741…` |
| **F7** | E4 | snapshot | **pending** | *NULL* | `d5e690…` |

**F1 and F2 are the distinction that must never collapse.** F1 is the clean
frame; F2 is the same frame with detection boxes drawn. "No bbox on the
person" means the court exhibit is F1 and the review copy is F2. One
`file_type` for both would eventually put burned-in boxes on an exhibit.

`remote_backend` differs per row because customers choose different targets,
and F7 is NULL because nothing has been uploaded yet. `checksum` is taken at
capture, so a remote copy can be proven to still match the local one.

The partial index on `sync_status <> 'synced'` means the backup scheduler's
work queue (F5, F6, F7) stays cheap to find however large the table grows.

---

## 10. `log_entries` — 8 rows

| id | service | level | message | timestamp |
|---|---|---|---|---|
| **L1** | pipeline | info | `FSM C1/43 OBSERVE -> VIOLATION dwell=88.0s` | 02:14:30 |
| **L2** | alerts | info | `dispatched 3 channels for E1` | 02:14:33 |
| **L3** | alerts | error | `telegram HTTP 429, retry 3` | 02:15:02 |
| **L4** | **browser** | error | `WebRTC negotiation failed for C3` | 02:15:40 |
| **L5** | **browser** | warn | `alert socket reconnect attempt 2` | 02:15:44 |
| **L6** | **browser** | log | `Authorization: ***REDACTED***` | 02:15:45 |
| **L7** | **browser** | warn | `client dropped 118 console lines to stay under its rate cap` | 02:16:00 |
| **L8** | ops | warn | `camera C3 offline for 180s` | 02:17:10 |

`meta` for the browser rows:

| id | meta |
|---|---|
| L4 | `{"session_id":"sess-7f21","username":"priya.n","roles":["viewer"],"tenant":"T1","url":"https://…/","user_agent":"Mozilla/5.0 (Linux; Android 13)","transport":"ws"}` |
| L6 | `{"session_id":"sess-7f21","username":"priya.n",…,"transport":"ws"}` |
| L7 | `{"session_id":"sess-7f21","username":"priya.n","dropped":118}` |

- **L6 shows redaction working.** The browser logged a failed fetch whose
  headers carried a live bearer token from `/auth/ws-token`. Redacted in the
  shim before the gzip blob was built, and again on ingest.
- **L7 is a dropped-line marker, not a log line.** The client hit its rate
  cap and dropped 118 lines; recording the count makes the gap visible
  instead of silent.
- `username`, `roles` and `tenant` come from the **validated token**, never
  from the envelope — a browser claiming to be someone else is not believed.
- All four browser rows share `session_id = sess-7f21`, which is what the
  expression index on `metadata->>'session_id'` exists to follow.

---

## 11. `scheduler_jobs` — 4 rows

| job | interval_s | enabled | updated_by | updated_at |
|---|---|---|---|---|
| `reports/categorise` | 1800 | true | ravi.k | 2026-10-06 10:12+05:30 |
| `ops/healer` | 120 | true | ravi.k | 2026-10-06 10:13+05:30 |
| `evidence/backup` | 7200 | true | priya.n | 2026-10-05 19:40+05:30 |
| `alerts` | 5 | **false** | ravi.k | 2026-10-07 09:01+05:30 |

**Only four rows, though there are more jobs than that.** A row means
"someone changed this"; every other job runs on its code default. That is
also why `updated_by` is never NULL in practice here.

`alerts` is currently **disabled** — violation delivery is paused. That is
exactly the change you want attributable, and `updated_by` + `updated_at`
answer "who turned off alerting, and when" after the fact.

There is no `min_interval_s` column: the floor lives in code so the UI
cannot raise it. `reports/categorise` at 1800 s is above its floor; a write
of 1 s would be rejected.

---

## 12. `tts_cache` — 5 rows

| id | text_hash (head) | text | duration_ms |
|---|---|---|---|
| **V1** | `4c9a1f…` | "Attention. Waste dumping is prohibited at Adugodi Main Road." | 4120 |
| **V2** | `b721de…` | "ಗಮನಿಸಿ. ಅಡುಗೋಡಿ ಮುಖ್ಯ ರಸ್ತೆಯಲ್ಲಿ ಕಸ ಎಸೆಯುವುದು ನಿಷೇಧಿಸಲಾಗಿದೆ." | 5340 |
| **V3** | `1f03aa…` | "Attention. Waste dumping is prohibited at Lakkasandra Lake Edge." | 4480 |
| **V4** | `8e55b0…` | "This area is under CCTV surveillance." | 2650 |
| **V5** | `d2c7e4…` | "ಈ ಪ್ರದೇಶವು ಸಿಸಿಟಿವಿ ಕಣ್ಗಾವಲಿನಲ್ಲಿದೆ." | 3210 |

Keyed on `text_hash` (UNIQUE), so the same announcement is synthesised once
and replayed thereafter. English and Kannada are separate rows because they
are different audio.

**No row exists for Z5, Z6 or Z3** — those zones have `is_speakable = false`,
so no announcement naming them is ever generated. The absence is the
enforcement.

---

## 13. `system_config` — 6 rows

| key | value | updated_by |
|---|---|---|
| `inference_interval` | `{"frames": 2}` | ravi.k |
| `batch_size` | `{"value": 4}` | ravi.k |
| `alert_throttle_s` | `{"value": 30}` | priya.n |
| `evidence_pre_record_s` | `{"value": 10}` | priya.n |
| `telegram_enabled` | `{"value": true}` | ravi.k |
| `ip_speaker_enabled` | `{"value": false}` | ravi.k |

Natural key on `key` — config is read by name, never enumerated by id.
`value` is JSONB because the shapes are genuinely heterogeneous.

---

## 14. Reading the graph end to end

One critical event, traced through every table:

```
T1  Adugodi Ward Office
└── Z1  "Adugodi Main Road"  is_speakable=true, eps=45, min_events=8
    └── C1  "Entry"  online, 12.938210/77.610450
        ├── S3  track 43, entered 02:14:02, escalated, dwell 88.0s
        │   └── P1  "KA 01 AB 1234" → KA01AB1234  conf 0.88
        └── E1  02:14:30  dumping_detected  critical  conf 0.91
            │   category=midnight-vehicle  actor=vehicle  run=R7
            │   is_valid=true  acknowledged_by=priya.n
            ├── A1  dashboard  resolved   ack dashboard  priya.n
            ├── A2  speaker    resolved   └── D1  sent
            ├── A3  telegram   resolved   ├── D2  failed ×3  HTTP 429
            │                             └── D3  sent on the 4th
            ├── F1  snapshot            synced  s3  9f2a4c…   ← exhibit
            ├── F2  annotated_snapshot  synced  s3  71bb0e…   ← review copy
            ├── F3  video_clip          synced  s3  c40d13…
            └── F4  metadata_json       synced  s3  aa19f7…
```

And the tenancy boundary, which is one hop and has no shortcut:

```
a request from an admin whose token carries ww_tenant = T2
└── may read C4, C5          (cameras.tenant_id = T2)
    └── therefore E5, E6     (via camera_id)
        └── therefore A6, A7, F6
└── may NOT read C1, C2, C3  (tenant_id = T1)
    └── and therefore cannot reach E1–E4, A1–A5, A8, F1–F5, S1–S4, P1, P4, P5

NOTE: as of migration 009 the COLUMN exists and is populated, but
api/routes/cameras.py does not yet filter on it. The boundary above is
what the schema now makes possible, not what the API currently enforces.
```
