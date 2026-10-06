# DB schema — column rationale, as it needs to be

Date: 2026-10-06. Source: `backend/app/db/models.py`, migrations 001–008.

This is the **target** schema, not the as-built one. Columns that do not exist
yet are marked **NEW**; columns that should go are marked **DROP**; columns
that exist but are wrong are marked **CHANGE**.

Each column answers one question: *what breaks if it is absent?* A column
that cannot answer it does not belong.

---

## 0. Changes at a glance

| # | Change | Driver |
|---|---|---|
| S1 | `tenant_id` on `cameras` + a `tenants` table | one admin == one customer (D4/T1) |
| S2 | `zones` table; `cameras.zone` → FK | public-announcement safety bug; categoriser needs stable zone identity |
| S3 | `scheduler_jobs` table | cadence in DB with code fallback (W1) |
| S4 | `violation_events.category` + `actor_class` | DBSCAN/HDBSCAN derived categories |
| S5 | `alerts.acknowledged_by` / `acknowledged_at` / `ack_source` | **acknowledgement attribution is currently not recorded at all** |
| S6 | DROP `vlm_analyses` | VLM removed in favour of the relation model |
| S7 | `evidence_files.dropbox_path` → `remote_path` + `remote_backend` | backup target is GDrive / Dropbox / S3, not one vendor |
| S8 | DROP the `AppRole` enum | Keycloak is the authority on roles; a second one invites drift |
| S9 | FK indexes throughout | Postgres does not index foreign keys automatically |
| S10 | `log_entries` retention + partitioning | browser capture can add six figures of rows per session |
| S11 | `track_sessions` unique `(camera_id, track_id, entered_at)` | tracker IDs are **reused** after `maxShadowTrackingAge` |
| S12 | `plate_reads.plate_normalised` | `"KA 01 AB 1234"` and `"KA01AB1234"` are the same offender |

---

## 1. `tenants` — **NEW**

One admin == one customer. The tenant id currently lives only as a Keycloak
user attribute (`ww_tenant`), which is correct for *authorisation* but
useless for a `JOIN`. Without a table, no query can filter cameras or events
by customer, and that is the whole of T1.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | The value minted into `ww_tenant` when an owner creates an admin. Must be the same uuid, or the token claim cannot be joined to anything. |
| `name` | String(160) | Customer name, e.g. the ward office. Mirrors `ww_customer` so the owner's list is readable without a Keycloak round trip. |
| `is_active` | Boolean | A customer whose contract lapsed must stop receiving alerts without deleting their evidence. Deleting the row would cascade away a legal record. |
| `retention_days` | Integer | Evidence retention is a contractual term and differs per customer. Hardcoding it makes every customer the same. |
| `created_at` | timestamptz | When the customer was onboarded. Billing and audit. |

**Why not derive tenants from Keycloak alone:** the Admin API cannot be
joined, and a per-request lookup to list one customer's cameras would be an
N+1 on every page load.

---

## 2. `zones` — **NEW**

`cameras.zone` is free text today, duplicated into `alerts.zone`. Two
consequences, one of them a live safety bug:

1. **The public-announcement guard cannot work.** `UnsafeZoneNameError` tries
   to reject a personal descriptor with a character-class regex, so
   `"near the man in the blue jacket"` passes — 31 characters of letters and
   spaces. The fix is that a zone name is *chosen from a registered list*,
   vetted once by a person, never typed at announcement time.
2. Renaming a zone leaves every historical alert holding the old string, so
   zone analytics silently splits one zone into two.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | Stable identity, so renaming the display name does not fork history. |
| `tenant_id` | UUID FK → tenants | A zone belongs to one customer. Without it, zone names collide across customers. |
| `name` | String(120) | Display name, and what TTS speaks. |
| `is_speakable` | Boolean | **The safety gate.** Set by a human when the zone is created: may this name be broadcast over a public speaker? Replaces guessing from the string at runtime. |
| `eps_minutes` | Integer | DBSCAN time radius, **per zone** — the learner's own figures differ by site (Adugodi 45, Lakkasandra 60, Hongasandra 45). A global value mis-clusters every site but one. |
| `min_events` | Integer | DBSCAN `min_samples`, also per zone (8 / 4 / 6). This is "count is less" — below it, an event is recorded but not categorised. |
| `created_at` | timestamptz | Audit. |

---

## 3. `cameras`

The root of almost every join. Its `id` is what the pipeline, MediaMTX paths,
ROI polygons and evidence all key on.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | Referenced by six tables and by `ww:camera:{id}:*` Redis keys. UUID not serial, because the pipeline and backend both mint references without a shared sequence. |
| `tenant_id` | UUID FK → tenants | **NEW, S1.** The single column that makes T1 possible. Without it `/api/cameras` cannot be scoped and an admin from customer A reads customer B's cameras with a valid token. |
| `name` | String(255) | Operator-facing label, and the MediaMTX path segment. |
| `zone_id` | UUID FK → zones | **CHANGE** from `zone: String(255)`. See §2. |
| `location` | String(255) | Human address ("opposite the bus stand"). Distinct from GPS: an operator reads this, a map reads the coordinates. |
| `gps_lat` / `gps_lng` | Float, nullable | Map placement, and the only way to answer "which cameras cover this complaint". Nullable because a camera is useful before anyone surveys it. **Needs `CHECK (gps_lat BETWEEN -90 AND 90)` and `(gps_lng BETWEEN -180 AND 180)`**, plus a both-or-neither constraint — today a swapped pair puts the camera in the Arabian Sea and half a coordinate is accepted. |
| `status` | Enum CameraStatus | online / offline / warning / alert. Drives the dashboard tile and the camera-loss alert. Written by `CameraStatusConsumer`, which currently updates this and **broadcasts nothing**. |
| `stream_url` | Text | The RTSP source. Changing it is what triggers a pipeline `add_camera` / `update` command. Text not String because RTSP URLs carry credentials and query strings and routinely exceed 255. |
| `source_type` | String(10) | rtsp / file / usb. The pipeline builds a different source bin per type; without it every source is assumed RTSP and a file replay fails at link time. |
| `thumbnail_url` | Text | Last known frame, so the grid shows something for an offline camera instead of a black tile. |
| `roi_polygons` | JSONB | The detection region. JSONB because it is a variable-length list of polygons of variable vertex count — a relational shape would be three tables for data only ever read whole. Indexed access is never needed. |
| `recording_config` | JSONB | Per-camera Smart Record settings (pre/post duration, bitrate). JSONB because the set of knobs tracks DeepStream's, not ours. |
| `detection_config` | JSONB | Per-camera threshold overrides: `dwell_violation_threshold_s`, `waste_proximity_alert_window_s`, `waste_proximity_px`. Any key omitted falls back to the pipeline global. This is what makes a busy junction and a quiet lane tunable separately without a deploy. |
| `is_pinned` | Boolean | Operator's own tile ordering on the dashboard. Cosmetic, but the alternative is operators re-finding their cameras every shift. |
| `last_event_at` | timestamptz | Denormalised from `violation_events`, deliberately: the dashboard sorts twelve tiles by recency on every render, and doing that as a correlated subquery per tile is the query you regret. |
| `created_at` / `updated_at` | timestamptz | `updated_at` has `onupdate`, so "who changed this camera and when" is answerable without an audit table. |

**Index needs:** `(tenant_id, status)` for the dashboard, `(tenant_id, zone_id)`
for zone rollups, and a unique `(tenant_id, name)` — two cameras with the same
name in one customer makes the MediaMTX path ambiguous.

---

## 4. `violation_events`

The product. Everything else exists to produce or explain a row here.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | Referenced by alerts and evidence. |
| `camera_id` | UUID FK → cameras | Where. Also the tenancy path — events inherit the camera's tenant rather than carrying their own, so a camera cannot be moved between customers and leave its history behind. |
| `timestamp` | timestamptz | **When the violation happened**, as distinct from `created_at` (when the row was written). They differ by the FSM's confirmation delay, and for backfill they differ by days. Analytics must use this one. |
| `event_type` | Enum EventType | dumping_detected / suspicious_activity / repeat_offender. Separates a confirmed dump from the fast-track proximity signal, which fires earlier and is less certain. |
| `severity` | Enum AlertSeverity | critical / warning / info. Decides whether a public announcement fires, so it is not cosmetic. |
| `confidence` | Float | Model score. The column that lets a disputed event be re-examined — "the system was 0.52 sure" is a defensible answer, "the system said so" is not. |
| `track_id` | Integer, nullable | The nvtracker id. **Only unique within its shadow-tracking window** — `maxShadowTrackingAge: 50` means ids are reused within ~2 s of a track dying. Never a cross-time identifier. Nullable for backfilled events with no tracker. |
| `dwell_time_s` | Float | How long the actor stayed. The threshold this is compared against is the single most-tuned number in the system. Stored, not recomputed, because the threshold changes and historical events must keep the value they were judged on. |
| `relation_result` | JSONB | **CHANGE** from `vlm_response`. The relation model's output (person↔bag, carrying/releasing). Renamed because the VLM is gone and keeping the old name would make the column lie. |
| `person_id` | String(255), nullable | ReID identity, for repeat-offender detection. **Always null today** — ReID is deferred. Kept because `repeat_count` is meaningless without it, and dropping and re-adding a column costs a migration either way. |
| `repeat_count` | Integer | How many prior events this identity has. Denormalised so a "repeat offender" badge does not need a self-join at render time. Currently always 0, for the reason above. |
| `category` | String(40), nullable | **NEW, S4.** The derived category — midnight-work, midnight-vehicle, daytime-work, daytime-vehicle. Nullable **on purpose**: an event in an odd time slot or below `min_events` is recorded and *not* categorised. A NOT NULL here would force every outlier into a bucket, which is exactly what the learner ruled out. |
| `actor_class` | String(20), nullable | **NEW, S4.** person / vehicle. The second clustering axis — "vehicle-based vs work-based dumping" — and not derivable from `event_type`. |
| `category_run_id` | UUID, nullable | **NEW.** Which categoriser run assigned the category. Without it, re-clustering silently rewrites history and nobody can tell which events moved or why. |
| `snapshot_url` | Text | The frame. Evidence. |
| `video_clip_url` | Text | The clip. Evidence. Separate from snapshot because they are produced by different paths at different times, and a snapshot exists even when recording failed. |
| `status` | Enum AlertStatus | active / acknowledged / resolved. The review workflow. |
| `acknowledged_by` | String(255) | Username from the validated token. For an enforcement product this is the chain of custody. String not FK, because the user may be deleted from Keycloak and the attribution must survive them. |
| `acknowledged_at` | timestamptz | When. Together with the above, answers "who cleared this and when" after the fact. |
| `notes` | Text | Reviewer's free text. The place a human explains why the model was wrong. |
| `is_valid` | Boolean, nullable | **Three-state on purpose.** null = not reviewed, true = genuine, false = false positive. A Boolean NOT NULL would conflate "nobody looked" with "it was fine", and this column is the training signal for every future model. |
| `created_at` | timestamptz | Row write time. See `timestamp`. |

**Index needs:** `(camera_id, timestamp DESC)` is the history query;
`(status)` partial on `active` for the review queue; `(timestamp)` alone for
the categoriser's time-of-day clustering.

---

## 5. `alerts`

A *delivery* of an event to people. One event can raise several alerts
(dashboard, Telegram, speaker), which is why it is not a column on the event.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `event_id` | UUID FK, nullable | The violation this announces. Nullable for system alerts (camera offline, disk full) that have no violation behind them. |
| `camera_id` | UUID FK, nullable | Denormalised from the event so the dashboard can filter by camera without a join, and so a camera-offline alert has a camera at all. |
| `severity` | Enum AlertSeverity | Decides notification, sound, and whether the panel auto-opens. |
| `title` | String(500) | What the operator reads first. |
| `message` | Text | Body. |
| `zone_id` | UUID FK → zones | **CHANGE** from `zone: String(255)`. The denormalised string forks zone analytics on every rename. |
| `channel` | String(50) | dashboard / telegram / sip / speaker. Which route this instance went out by. |
| `tts_audio_url` | Text | Generated audio, cached so a repeated announcement is not re-synthesised. |
| `snapshot_url` | Text | The frame this fired on, served under `/evidence`. Set by `alert_consumer.py` from the pipeline payload. |
| `delivered_at` | timestamptz | When it actually left. Distinct from `timestamp` (when it was raised) — the gap is the delivery latency, and a null here with an old timestamp is a stuck channel. |
| `timestamp` | timestamptz | When raised. |
| `status` | Enum AlertStatus | active / acknowledged / resolved. |
| `acknowledged_by` | String(255) | **NEW, S5.** |
| `acknowledged_at` | timestamptz | **NEW, S5.** |
| `ack_source` | String(20) | **NEW, S5.** dashboard / ivr / api. **Not equivalent trust.** A dashboard ack is an authenticated session; an IVR ack is whoever answered the phone and pressed 1. Recording them identically would let a mis-dialled number clear a violation a case depends on, indistinguishably from an operator doing it. |

**S5 is the serious one.** `alerts` has `status` but no attribution columns,
and the UI acknowledges *alerts* (`PATCH /api/alerts/{id}`). So right now
**who acknowledged an alert is not recorded anywhere.** `violation_events`
has the columns; the table the UI writes does not.

---

## 6. `alert_deliveries`

Per-attempt delivery log. `alerts.delivered_at` says whether it arrived;
this says what happened on each try.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `alert_id` | UUID FK → alerts | |
| `channel` | String(50) | Which transport. |
| `status` | String(50) | pending / sent / failed. **Should be an Enum** — a free string invites `"Failed"` and `"failure"` to coexist. |
| `payload` | JSONB | What was actually sent. The only way to answer "what did the Telegram message say" after a template change. |
| `delivered_at` | timestamptz | |
| `error_message` | Text | Why it failed. Without it a failed delivery is unactionable. |
| `retry_count` | Integer | Drives backoff and the give-up decision. |
| `created_at` | timestamptz | |

---

## 7. `track_sessions`

One actor's pass through a camera's ROI. The FSM's durable state.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `camera_id` | UUID FK | |
| `track_id` | Integer | nvtracker id. **Reused** — see `violation_events.track_id`. |
| `fsm_state` | Enum EventFSMState | enter / observe / violation / alerting / escalated / exited. Persisted so a pipeline restart does not re-alert on actors already escalated. |
| `entered_at` | timestamptz | ROI entry. With `track_id` and `camera_id`, this is what actually makes a session unique. |
| `exited_at` | timestamptz, nullable | Null = still in frame. The FSM's liveness check reads this. |
| `dwell_time_s` | Float | Final dwell. Stored rather than computed from the timestamps because the FSM's notion of dwell excludes occlusion gaps. |
| `bbox_history` | JSONB | Track path. Feeds the relation model and lets a disputed event be replayed without the video. **Needs a cap** — an actor loitering for an hour at 15 fps is 54,000 boxes in one JSONB value. |
| `created_at` | timestamptz | |

**S11:** add `UNIQUE (camera_id, track_id, entered_at)`. Without `entered_at`
in the key, the id reuse guaranteed by `maxShadowTrackingAge: 50` collides
two different people onto one session.

---

## 8. `plate_reads`

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `camera_id` | UUID FK | |
| `track_session_id` | UUID FK, nullable | Ties the plate to the actor. Nullable because a plate can be read with no confirmed session. |
| `plate_number` | String(20) | As read, verbatim. Kept raw because it is evidence. |
| `plate_normalised` | String(16) | **NEW, S12.** Uppercased, whitespace and hyphens stripped. Repeat-offender matching on the raw string treats `"KA 01 AB 1234"` and `"KA01AB1234"` as two vehicles — so the one thing this table exists for silently fails. |
| `confidence` | Float | OCR score. A plate read at 0.4 should not convict anyone. |
| `plate_image_url` | Text | The crop. Evidence — a plate number without the image it came from is an assertion. |
| `vehicle_bbox` | JSONB | Where in frame, so the crop can be re-derived. |
| `timestamp` | timestamptz | |
| `created_at` | timestamptz | |

---

## 9. `evidence_files`

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `event_id` | UUID FK, nullable | |
| `file_type` | String(50) | snapshot / video_clip / annotated_snapshot / metadata_json. **`annotated_snapshot` must stay distinct from `snapshot`** — the learner's "no bbox on the person" means clean evidence and annotated review copy are two artefacts, and conflating them puts burned-in boxes on a court exhibit. |
| `local_path` | Text | On-disk path. |
| `remote_path` | Text | **CHANGE** from `dropbox_path`. The backup target is GDrive / Dropbox / S3, so a vendor name in a column name forces a migration per provider. |
| `remote_backend` | String(20) | **NEW, S7.** Which provider this copy went to. Needed to verify a backup and to support more than one simultaneously. |
| `sync_status` | String(20) | pending / uploading / synced / failed. The backup scheduler's work queue. **Should be an Enum.** |
| `synced_at` | timestamptz | |
| `checksum` | String(64) | **NEW.** SHA-256 at capture. Evidence whose integrity cannot be demonstrated is weak evidence, and it is also the only way to prove a remote copy matches the local one. |
| `created_at` | timestamptz | |

---

## 10. `system_config`

| Column | Type | Significance |
|---|---|---|
| `key` | String(255) PK | Config name. Natural key — config is read by name, never enumerated by id. |
| `value` | JSONB | Any shape. JSONB because the values are heterogeneous by nature. |
| `updated_by` | String(255) | Who changed it. A changed threshold explains a change in detection behaviour, and that question is always asked later. |
| `updated_at` | timestamptz | `onupdate`. |

Keep for genuinely global settings. **Do not** put scheduler cadence here —
§11 needs per-job floors and enable flags, which a single JSONB blob cannot
be constrained on.

---

## 11. `scheduler_jobs` — **NEW, S3**

| Column | Type | Significance |
|---|---|---|
| `job` | String(64) PK | `ops/healer`, `reports/categorise`. Natural key; the registry looks up by name. |
| `interval_s` | Integer | The live cadence, editable from the UI. The reason this table exists at all — a screen cannot edit a Python constant. |
| `enabled` | Boolean | Pause a job without a deploy. |
| `updated_by` | String(255) | Who disabled the alerts job is asked after an incident, not before. |
| `updated_at` | timestamptz | |

`min_interval_s` is deliberately **not** a column. It is the floor that stops
someone setting the categoriser to 1 s and stalling the loop, so it lives in
code where the UI cannot raise it. A row below the floor is rejected on write
*and* ignored on read.

Resolution is `db_row.interval_s if loaded and >= floor else
JOB_DEFAULTS[job]`. The DB must not become load-bearing for scheduling: if
Postgres is unreachable at startup and the interval existed only as a row,
nothing would run at all, so a DB blip would be a total outage of every
background job.

---

## 12. `log_entries`

Server logs and, when capture is on, browser console logs.

| Column | Type | Significance |
|---|---|---|
| `id` | UUID PK | |
| `service` | String(100) | Producer. `"browser"` marks a client row, which is how client noise is separated from server logs with an indexed equality check. |
| `level` | String(20) | debug / info / log / warn / error. Coerced to a known set on ingest — browser input is attacker-controlled. |
| `message` | Text | Redacted at capture, and again server side. |
| `meta` | JSONB (column name `metadata`) | session_id, username, roles, tenant, url, user_agent, client_ts, stack. JSONB because the shape differs per producer. Aliased because `metadata` collides with SQLAlchemy's `Base.metadata`. |
| `timestamp` | timestamptz | Event time. A client clock more than a day off is replaced with server time, with the original kept in `meta.client_ts` — otherwise a browser with a wrong clock files its logs in 1970 and vanishes from every time-ranged query. |
| `created_at` | timestamptz | Server write time. |

**S10.** Browser capture can add six figures of rows per operator per
session. Needed:
- the two indexes from migration 008 (`(service, timestamp)`, and an
  expression index on `metadata->>'session_id'`)
- a retention rule, and it belongs in the event pool, not a sixth timer
- monthly `RANGE` partitioning on `timestamp` if browser capture is ever
  left on routinely — dropping a partition is O(1) where `DELETE` is not

---

## 13. DROP

### `vlm_analyses` — **S6**
Columns `frames_analyzed`, `prompt_used`, `response`, `inference_time_ms`,
`model_version` describe a vision-language model that was removed in favour
of the relation model. The relation model's output belongs on the event
(`violation_events.relation_result`) because it is one structured verdict per
event, not a separate analysis document.

### `AppRole` enum — **S8**
`owner / admin / viewer` duplicated in the database while Keycloak is the
authority and `require_role()` matches the token claim. Two definitions of
the same hierarchy will drift, and the DB copy has no enforcement path. The
hierarchy belongs in Keycloak composite roles and in `_GRANTABLE`.

### `tts_cache` — **keep, conditionally**
Still correct while TTS is a configurable feature. `text_hash` is unique and
is the whole point: the same announcement is not re-synthesised. Revisit only
if TTS is removed.

---

## 14. Cross-cutting

**S9 — foreign keys are not indexed automatically in Postgres.** Every
`camera_id`, `event_id`, `alert_id`, `track_session_id` and `tenant_id`
needs one, or each becomes a sequential scan as the tables grow. This is the
single cheapest performance change in the schema.

**Enums as Postgres types, not strings.** The existing enums are declared
`create_type=False`, so the types are created by migration rather than by
SQLAlchemy — correct, but it means adding a value requires `ALTER TYPE` in a
migration and cannot be done by editing Python alone. `alert_deliveries.status`
and `evidence_files.sync_status` are still free strings and should join them.

**Tenancy is inherited, not repeated.** Only `cameras` and `zones` carry
`tenant_id`; events, alerts, tracks, plates and evidence reach it through
`camera_id`. Repeating the column everywhere would create a second source of
truth that can disagree with the first, and the join is one hop.

**Timestamps are always `timestamptz`.** Already true throughout. It matters
for the categoriser: circular time-of-day clustering (23:30 and 00:30 are one
hour apart, not twenty-three) is already hard enough without a timezone
ambiguity underneath it.
