# Face-Based Repeat-Violator Subject IDs - Design Spec

Date: 2026-09-29
Status: Draft for review
Branch: `feat/face-subject-id` (from `origin/chore/pending-fixes-batch` @ `71d3fc1`)
Scope: Face capture at violation time, anonymous subject IDs, repeat detection, operator review. Licence-plate linking (LPR) is explicitly out of scope.

## 1. Goal

When a person commits a violation, capture their face at that moment and assign them an anonymous subject ID (`S-001`, `S-002`, ...). When the same person violates again, the new incident is linked to the same ID and flagged as a repeat (`S-001 · Repeat ×2`).

Success looks like:

- Every violation alert shows, within ~8 s of firing (2 s post-roll + processing + the 5 s alert poll), either a subject badge or an explicit `Face not captured`.
- A second violation by the same person, with a usable face, lands on the same subject code automatically, or goes to the review queue if the match is uncertain.
- Operators can correct the system (merge, reject, detach). A wrong automatic match never stays hidden.

### Non-goals

- No live marking or tracking of people who have not violated. Faces are compared only at violation time.
- No names, no identification against external databases, no NAFRS/RTO integration.
- No LPR/vehicle linking, appearance (body/clothing) ReID, time-pattern analytics or "known offender returns" alerts. These are possible follow-ups.
- No dashboard login. Auth is wired to the existing Keycloak dependency, but building the frontend login flow is a separate task.

## 2. Decisions taken during brainstorming

| # | Decision | Chosen |
|---|---|---|
| D1 | What "marking" means | Tag after the fact: subject ID on each violation, Subjects view, review screen. No live marking. |
| D2 | Match autonomy | Auto-attach when confident (≥ 0.55 cosine), review queue for 0.40–0.55, new subject below 0.40. |
| D3 | Where face work runs | Pipeline only captures raw person crops and publishes them. A new CPU `subject-worker` does detection, embedding, matching and storage. |
| D4 | Incident unit | One violation episode per person track = one incident. Only a first-fire critical violation for a person starts a face capture; later alerts on the same person track within 600 s of its last alert reuse that incident id (warnings and reminders carry it if one exists, else none). |
| D5 | Access control | `require_admin` on every subject route now. It is effective once dashboard login exists; under `WW_AUTH_DEV_MODE` it resolves to the mock `dev` user. |
| D6 | How the badge reaches the dashboard | `GET /api/alerts` joins the subject tables on `incident_id`. The alert panel (`MonitoringContext`) and History already refresh that endpoint every 5 s / 30 s, so there is no `wastewatch.subjects` topic, no `alerts.subject_id` column and no WebSocket message. Merge and detach show up on the next refresh automatically. |

## 3. Constraints found in the code

1. **Stored frames already have faces covered.** The capture tee is after `nvdsosd` (`pipeline/app/pipeline.py`, link `nvosd -> tee` ~L2633), and `decorate_for_osd` adds an opaque rectangle over the top 20% of every person bbox. The JPEG caches and recordings are therefore unusable for faces. A raw frame source before `nvosd` is required.
2. **The main dumping path emits no FSM event.** `EventFSMManager.force_violation()` (`pipeline/app/event_fsm.py`) sets VIOLATION without publishing to `wastewatch.events`. Every violation that notifies anyone goes through `_fire_waste_alert_async` (`pipeline/app/pipeline.py` ~L317), which has a 0.8 s per-camera de-duplication. That function is the hook point.
3. **No correlation ID exists.** Alert IDs are minted later in `AlertConsumer` (`uuid4`), so the pipeline cannot reference the alert. The pipeline must mint an `incident_id`.
4. **Pixel-level work in the display chain has crashed or stalled it twice before** (`pipeline.py` comments ~L838-859). Any new branch must be unable to backpressure the main chain.
5. **`/evidence` is a public StaticFiles mount** (`backend/app/main.py`). Face crops must not live under it.
6. **Qdrant runs in `docker-compose.yml` but not in `docker-compose.lite.yml`.**
7. **Auth dependencies exist but no route uses them, and the frontend sends no bearer token.**
8. **Alembic head is `007`.**

## 4. Architecture

```
pipeline (DeepStream, GPU)
  streammux → nvinfer → nvtracker → action_sgie → nvdsanalytics → [wastewatchmeta]
      → tee_raw ─┬─(src_0, FIRST)→ nvvideoconvert → caps(RGBA) → queue_raw(leaky, 1 buf) → fakesink
                 │                     ↑ throttle probe on tee_raw src_0     ↑ raw_face_probe (fakesink sink pad)
                 └─(src_1)→ nvosd → tee → demux / meta / capture          (unchanged)
  throttle probe: DROP unless ≥ 0.5 s since last pass AND ≥1 person in batch

  _fire_waste_alert_async (after dedup):
      incident_id = uuid4()
      person_track = dumped_by_track_id if not None else track_id
      FaceCropBuffer.begin_incident(incident_id, source_id, person_track, now)
      alert payload += {incident_id, person_track_id}
  background flusher thread (every 0.5 s):
      for each incident whose 2 s post-roll ended:
          write crops → /app/faces_private/incoming/{incident_id}/NN.jpg
          publish wastewatch.faces.requests

subject-worker (new, CPU, backend image)
  wastewatch.faces.requests → idempotency check → SCRFD → quality gate → align 112×112
      → ArcFace 512-d → Qdrant offender_faces search → decide (auto / review / new / no_face)
      → Postgres transaction → Qdrant upsert
  daily: retention sweep

api
  AlertConsumer: stores incident_id on the alert row
  GET /api/alerts: LEFT JOIN face_incidents / subject_incidents / subjects on incident_id
  /api/subjects/* router (require_admin, access-logged)

frontend
  AlertPanel / HistoryView subject badge; new "Subjects" view (Subjects tab + Review tab)
```

## 5. Pipeline changes

### 5.1 Raw branch

- A new `tee_raw` is inserted immediately before `nvosd`, in both link paths (native `wastewatchmeta -> nvosd` and fallback `nvdsanalytics -> nvosd`).
- **Why this order.** `tee` does not copy buffers, and `nvdsosd` rasterises **in place** on the shared surface. A clean copy must therefore exist before nvosd sees the buffer.
- The raw branch is requested as `tee_raw`'s **first** src pad; tee pushes to src pads in order, on the streaming thread. It is `nvvideoconvert` → RGBA capsfilter with **no queue before the converter**, so the conversion (which writes into a new buffer) finishes before nvosd receives the buffer.
- Only after conversion comes `queue_raw` (`leaky=2`, `max-size-buffers=1`, bytes/time 0) → `fakesink` (`sync=false`, `async=false`). The Python crop work therefore runs on the queue's own thread and cannot backpressure the chain.
- **Throttle.** A probe on `tee_raw`'s first src pad returns `Gst.PadProbeReturn.DROP` unless ≥ 0.5 s has passed since the last passed buffer and the batch contains at least one `person` object. The synchronous conversion therefore runs at ≤ 2 fps, and only when people are present.
- **Raw probe.** It reads batch meta (it is the same buffer, so tracker IDs are present). For each `person` object on each frame it:
  - crops the head region: the top 50% of the person bbox, padded 10% horizontally, clamped to the frame;
  - skips the crop if it is smaller than 48×48;
  - encodes a JPEG (quality 90);
  - calls `FaceCropBuffer.add(source_id, track_id, ts, jpeg)`.
- **Kill switch.** When `WW_FACE_CAPTURE_ENABLED` is not `1`, the raw branch is not built and the hook is a no-op: alert payloads carry neither `incident_id` nor `person_track_id`, so they are byte-for-byte today's payloads. The code default is off; compose sets it on.
- **Scope of coverage.** `_fire_waste_alert_async` is only called on the Python metadata path. When the native `wastewatchmeta` C plugin path is active, no Python alerts fire, so no faces are captured. This is recorded as a known limitation.

### 5.2 `pipeline/app/face_capture.py` (new, pure, unit-tested)

`FaceCropBuffer`, thread-safe (a single `threading.Lock`):

- `add(source_id, track_id, ts, jpeg)`
  - Appends to a per-`(source_id, track_id)` `deque(maxlen=6)`, which is about 3 s at 2 fps.
  - Also appends to any open incident for that key.
  - Open incidents are capped at 12 crops.
- `begin_incident(incident_id, source_id, track_id, now) -> None`
  - Opens an incident seeded with a copy of the current ring for that key.
  - Its post-roll closes at `now + 2.0`.
  - If the same `(source_id, track_id)` already has an open incident, the new one is recorded as an alias sharing the same crops.
    This is defensive only: the §5.3 hook reuses a track's incident id through `TrackIncidents`, so it never begins a second incident for a track within the 600 s TTL.
- `pop_due(now) -> list[Incident]`: returns and removes incidents whose post-roll has ended.
- `evict_stale(now)`: drops rings not updated for 5 s.
- Crops exist only in process memory until an incident closes. Rings of people who never violate are overwritten or evicted within seconds and never touch disk.

`TrackIncidents` (thread-safe): `get_or_create(camera_id, track_id, now) -> (incident_id, is_new)` and `lookup(camera_id, track_id, now) -> incident_id | None`, see §5.3.

### 5.3 Hook and flusher

- `_fire_waste_alert_async` takes `capture_face: bool = False`. Only call sites that fire a first-time critical violation for a person pass `True` (waste dumping confirmed after the person highlight, the dwell-confirmed waste fallback, and the generic FSM `VIOLATION` alert when the violating track is a person). The OBSERVE "Suspicious activity" warning and the periodic "Reminder: … still not cleared" alert pass `False`.
- After the dedup check, when face capture is enabled, the hook (`alert_incident_fields` in `face_capture.py`) works on `person_track_id = dumped_by_track_id or track_id` and a `TrackIncidents` registry keyed by `(camera_id, person_track_id)` (600 s TTL, refreshed on every use, expired entries evicted lazily):
  - `capture_face=True`: reuse the track's incident id, or mint a new uuid4; only a newly minted id calls `begin_incident`, so a person is enrolled once per violation episode;
  - `capture_face=False`: look up the track's existing incident id without creating one (may be none);
  - adds `incident_id` and `person_track_id` to the `send_alert` kwargs only when there is an incident id.

  It runs on the pad-probe thread, so it does O(1) work only (plus lazy eviction over the small registry).
- A daemon flusher thread handles closed incidents. Every 0.5 s it calls `pop_due`, then for each incident:
  - writes the crops to `/app/faces_private/incoming/{incident_id}/{NN}.jpg`;
  - publishes `wastewatch.faces.requests`.

  An incident with zero crops is still published with `crops: []`, so the dashboard learns "Face not captured".
- If `WW_ALERTS_DISABLED=1`, `_fire_waste_alert_async` already returns early, so no face capture happens.

### 5.4 Message: `wastewatch.faces.requests`

```json
{
  "incident_id": "uuid",
  "camera_id": "pipeline camera id",
  "source_id": 0,
  "person_track_id": 42,
  "detected_at": 1790000000.123,
  "crops": ["/app/faces_private/incoming/<incident_id>/00.jpg", "..."]
}
```

## 6. subject-worker

- **Location:** `backend/services/subjects/main.py` (entrypoint) and `backend/app/subjects/` (logic).
- **Container:** its own compose service `subject-worker`, built from the backend image. It is not merged into `backend-workers`, so the ~500 MB of loaded models don't sit in the combined process.
- **New backend dependencies:** `onnxruntime` (CPU) and `opencv-python-headless`.

### 6.1 Modules

| Module | Responsibility |
|---|---|
| `app/subjects/detector.py` | SCRFD ONNX: returns boxes, scores and 5-point landmarks |
| `app/subjects/embedder.py` | Similarity-transform alignment to the ArcFace 112×112 template, ArcFace ONNX → L2-normalised 512-d vector |
| `app/subjects/quality.py` | Quality gate and score (pure) |
| `app/subjects/matcher.py` | Per-subject scoring and the auto/review/new decision (pure) |
| `app/subjects/vector_store.py` | Qdrant `offender_faces` wrapper: ensure collection, upsert, search, set subject, delete |
| `app/subjects/service.py` | Orchestrates one request inside a DB transaction; merge, reject, detach and delete operations used by both the worker and the API |
| `app/subjects/retention.py` | Retention sweep |

### 6.2 Processing one request

1. **Idempotency.** If `face_incidents` already has this `incident_id` (every processed incident, matched or `no_face`, gets a row), the worker acks and stops.
2. **Detect.** SCRFD runs on each crop, keeping the highest-scoring face per crop.
3. **Quality gate.** A face passes only if all of these hold:
   - `det_score ≥ WW_FACE_MIN_DET` (0.6);
   - face width ≥ `WW_FACE_MIN_PX` (40);
   - Laplacian variance of the aligned face ≥ `WW_FACE_MIN_SHARPNESS` (60);
   - `|yaw_proxy| ≤ 0.45`, where `yaw_proxy = (nose_x − eye_mid_x) / inter_eye_dist`, which is roughly ≤ 45°.

   Quality score = `det_score × min(1, width/112) × min(1, sharpness/200) × (1 − |yaw_proxy|)`. The top 3 faces by quality are kept.
4. **No usable face.** The worker records `face_incidents(incident_id, status='no_face')`, deletes the incoming crops and stops.
5. **Embed** the kept faces.
6. **Search.** For each kept face, Qdrant returns the top 20 (cosine). Only active subjects own faces, because merge moves faces and delete removes them, so no status filter is needed. Subject score = the maximum similarity over all (kept face × that subject's stored face) pairs.
7. **Decide** (pure `matcher.decide`), using the best subject `s*` with score `σ`:
   - `σ ≥ WW_FACE_MATCH_AUTO` (0.55): **auto**. The incident is attached to `s*`.
   - `WW_FACE_MATCH_REVIEW ≤ σ < AUTO` (0.40): **review**. A new subject is created and a `pending` review item `(new_subject, s*, σ)` is added.
   - `σ < 0.40`, or no stored faces: **new** subject.
8. **Persist** in one Postgres transaction:
   - the subject row (created or updated: `incident_count`, `last_seen_at`);
   - `subject_incidents`;
   - `subject_faces`, keeping at most `WW_SUBJECT_MAX_FACES` (10) per subject by quality and deleting the evicted ones from Qdrant and disk; `cover_face_id` is recomputed to the highest-quality remaining face;
   - the review item, if any;
   - `face_incidents(status='matched')`.

   Qdrant upserts happen after the DB commit, keyed by `subject_faces.id`. A failure there is retried, and the row keeps `qdrant_synced=false` until it succeeds. Crop files move from `incoming/{incident_id}/` to `subjects/{subject_id}/{face_id}.jpg`; unused crops are deleted.

   Every post-commit step (crop writes, Qdrant upsert and the `qdrant_synced` update, evicted-face cleanup, `incoming/` removal) is best-effort: a failure is logged and the result is still returned; the same applies after merge, detach and delete. The maintenance loop runs, each step isolated, `resync_index()` then `reconcile()` (delete Qdrant points whose id is not a `subject_faces.id`, and `subjects/**/*.jpg` files that no `crop_path` references, then remove the folders this emptied) every 10 min, and the retention sweep daily.
9. **Commit the Kafka offset** after step 8. Redelivery is safe because of step 1, so a failed commit (for example after an outage longer than `max_poll_interval_ms`) is logged and ignored. Nothing is published; the dashboard reads the result through `GET /api/alerts` (D6).
10. **Failures.** Only `OperationalError`, `InterfaceError` and `ConnectionError` count as infrastructure errors and are retried until the dependency is back (exponential backoff, capped); any other error (including `IntegrityError`/`DataError`) is retried 3 times, then the message is skipped. If the consume or maintenance task dies, the process exits with code 1 and compose restarts it.

## 7. Data model (Alembic `008_subjects`)

```
subjects
  id uuid pk
  code_num int unique not null         -- from sequence subject_code_seq; never reused. Display code = f"S-{code_num:03d}"
  status text not null default 'active' -- active | merged | deleted
  merged_into_id uuid null fk subjects.id
  incident_count int not null default 0
  first_seen_at timestamptz not null
  last_seen_at timestamptz not null
  cover_face_id uuid null               -- highest-quality face
  created_at timestamptz default now()

subject_faces
  id uuid pk                            -- also the Qdrant point id
  subject_id uuid fk subjects.id on delete cascade
  incident_id uuid not null
  crop_path text not null               -- under /app/faces_private/subjects/
  quality real not null
  embedding bytea not null              -- 512 float32; Postgres is the source of truth, Qdrant is a rebuildable index
  qdrant_synced bool not null default false
  created_at timestamptz default now()

subject_incidents
  incident_id uuid pk
  subject_id uuid fk subjects.id
  camera_id text not null
  detected_at timestamptz not null
  match_type text not null              -- new | auto | review_merged
  similarity real null
  created_at timestamptz default now()

face_incidents                          -- one row per processed incident, incl. no_face
  incident_id uuid pk
  status text not null                  -- matched | no_face
  camera_id text not null
  detected_at timestamptz not null
  created_at timestamptz default now()

subject_match_reviews
  id uuid pk
  incident_id uuid not null
  new_subject_id uuid fk subjects.id on delete cascade
  candidate_subject_id uuid fk subjects.id on delete cascade
  similarity real not null
  status text not null default 'pending' -- pending | merged | rejected | obsolete
  decided_by text null
  decided_at timestamptz null
  created_at timestamptz default now()

subject_access_log
  id bigserial pk
  subject_id uuid null                  -- kept after subject deletion (no FK)
  action text not null                  -- list | view | face_view | merge | reject | detach | delete
  actor text not null                   -- JWT preferred_username / sub
  client_ip text null
  detail jsonb null
  at timestamptz default now()

alerts (existing)
  + incident_id uuid null (indexed)
```

Qdrant collection `offender_faces`: size 512, distance Cosine. Payload: `subject_id`, `incident_id`, `camera_id`, `quality`, `created_at`. It is created on worker startup if missing.

## 8. Operator actions (in `app/subjects/service.py`)

All actions run in one transaction, then sync to Qdrant, then write an access-log row.

- **Merge** (review → "Same person"): the new subject folds into the candidate.
  - Faces and incidents are repointed.
  - Qdrant `subject_id` payloads are updated.
  - The face cap is re-applied.
  - `incident_count`, `first_seen_at`, `last_seen_at` and the cover face are recomputed.
  - The new subject becomes `status='merged'`, `merged_into_id=candidate`.
  - `match_type` becomes `review_merged`.
  - Other pending reviews that reference the merged subject are repointed; any that become self-pairs are marked `obsolete`.
- **Reject** ("Different"): the review becomes `rejected`. Reviews are only created at incident time against a freshly created subject, so the same pair is never suggested again.
- **Detach incident:**
  - The incident and its faces move to a newly created subject (new code).
  - Counts on the old subject are recomputed; if it has no incidents left, it is deleted.
- **Delete subject:** DB rows, Qdrant points and crop files are removed. The access-log row remains. The alert's badge then shows nothing, because the join finds no subject.

**Plan-time refinements (2026-09-29):** detaching a subject's *only* incident returns 409 instead of creating an empty subject;
if Qdrant search fails, matching falls back to an exact search over the embeddings stored in Postgres; the sidebar's pending-review
count uses `GET /api/subjects/reviews/count`, which is not access-logged because it carries no personal data.
Merge and detach reassign faces without moving their crop files — `subject_faces.crop_path` stays authoritative, and deleting a subject removes only its own faces' files. Infrastructure errors (database/connection) in the subject-worker are retried until the database is back instead of being skipped.

## 9. API (`backend/app/api/routes/subjects.py`)

Every route depends on `require_admin` and writes `subject_access_log` with actor and `request.client.host`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/subjects?repeat_only=bool&sort=incidents\|last_seen&limit&offset` | Active subjects: code, cover face id, incident count, first/last seen, camera ids |
| GET | `/api/subjects/{id}` | Faces and incident timeline (joined to the earliest `alerts` row per incident, since several alerts can share one incident id, for snapshot URL, camera name/zone, time) |
| GET | `/api/subjects/faces/{face_id}` | `FileResponse` of the private crop, `Cache-Control: no-store` |
| GET | `/api/subjects/reviews?status=pending` | Review items with face ids for both sides |
| POST | `/api/subjects/reviews/{id}/merge` | |
| POST | `/api/subjects/reviews/{id}/reject` | |
| POST | `/api/subjects/{id}/incidents/{incident_id}/detach` | |
| DELETE | `/api/subjects/{id}` | |

### AlertConsumer changes (`backend/app/services/alert_consumer.py`)

- It stores `payload["incident_id"]` on the alert row and includes `incident_id` in the WS payload. Nothing else changes.
- `GET /api/alerts` LEFT JOINs `face_incidents`, `subject_incidents` and `subjects` on `alerts.incident_id` and adds these fields:
  - `face_status`: `null` while pending, otherwise `matched` / `no_face`;
  - `subject_id` and `subject_code`;
  - `subject_incident_count`;
  - `subject_review_pending`: whether a pending review exists with `new_subject_id` equal to this subject.

New constants in `backend/app/config/kafka.py`: `TOPIC_FACES_REQUESTS = "wastewatch.faces.requests"` and `GROUP_SUBJECT_WORKER = "wastewatch.group.subject-worker"`.

## 10. Frontend

- **Types** (`frontend/src/types/monitoring.ts`): alerts gain `incidentId`, `subjectId`, `subjectCode`, `subjectIncidentCount`, `faceStatus`, `subjectReviewPending`.
- **`MonitoringContext.fetchAlerts` and `HistoryView`** map the new fields from `GET /api/alerts`. The unused `useAlerts` hook is left alone.
- **`SubjectBadge` component**, used in `AlertPanel` and `HistoryView`:
  - `S-007 · New`
  - `S-003 · Repeat ×3` (emphasised)
  - `S-011 · Match under review`
  - `Face not captured` (muted)
  - Nothing while the result is pending.

  Clicking it opens the subject.
- **New sidebar view `SubjectsView`:**
  - **Subjects tab:** a grid of cards (cover face, code, incident count, last seen), sorted repeat-first, with a "Repeat only" toggle.
  - **Subject detail:** face strip, incident timeline (snapshot, camera/zone, time, match type, similarity) with a **Detach** action per incident, and **Delete subject**.
  - **Review tab:** each pending item side by side (new faces | candidate faces | similarity) with **Same person** and **Different**. The sidebar item shows the pending count.
- **Face images** are loaded through a small `useAuthedImage` helper (fetch → blob URL) instead of a bare `<img src>`, so it keeps working once a bearer token is sent.
- **Wording** stays neutral: "Subject", "Repeat", "incidents". Never "offender" or "culprit".

## 11. Guardrails

| Guardrail | Mechanism |
|---|---|
| Only violators' faces stored | Crops live in pipeline memory only; nothing is written without `begin_incident`. Non-violator rings are evicted within ~5 s. |
| No comparison outside violations | Matching runs only in `subject-worker` on `faces.requests`. |
| Retention | Daily sweep. Subjects with `last_seen_at` older than `WW_SUBJECT_RETENTION_DAYS` (90) are fully deleted. `incoming/` folders and `no_face` data older than 7 days are deleted. `merged` tombstones are deleted after the same retention period. |
| Private storage | New volume `faces_private` mounted at `/app/faces_private`: pipeline (rw, writes only `incoming/`), subject-worker (rw), api (rw, because merge and delete remove files). It is never under `/evidence`. Both the pipeline and the backend images run as a non-root `appuser` (their uids may differ) and pre-create `/app/faces_private/{incoming,subjects}` with mode 0777, so Docker seeds a new, empty volume with world-writable folders. The pipeline's `ensure_faces_dirs` is best-effort (a `chmod` on a folder owned by the other uid is ignored); if the volume cannot be prepared, face capture is disabled with an error log and the pipeline keeps running. |
| Human in the loop | Uncertain matches never auto-link. Every automatic link can be undone with detach. |
| Audit | `subject_access_log` covers every read and every change. |
| No public exposure | Subject data is never included in TTS text, Telegram or SIP messages. |
| Access control | `require_admin` (D5). This is not real protection until dashboard login exists; recorded as a known gap. |

## 12. Configuration

| Env var | Default | Where |
|---|---|---|
| `WW_FACE_CAPTURE_ENABLED` | `0` in code, `1` in compose | pipeline |
| `WW_FACE_CAPTURE_INTERVAL_S` | `0.5` | pipeline |
| `WW_FACE_POSTROLL_S` | `2.0` | pipeline |
| `WW_FACE_MIN_DET` | `0.6` | subject-worker |
| `WW_FACE_MIN_PX` | `40` | subject-worker |
| `WW_FACE_MIN_SHARPNESS` | `60` | subject-worker |
| `WW_FACE_MATCH_AUTO` | `0.55` | subject-worker |
| `WW_FACE_MATCH_REVIEW` | `0.40` | subject-worker |
| `WW_SUBJECT_MAX_FACES` | `10` | subject-worker |
| `WW_SUBJECT_RETENTION_DAYS` | `90` | subject-worker |
| `WW_FACE_DET_MODEL` / `WW_FACE_REC_MODEL` | `/app/models/face/det_10g.onnx` / `/app/models/face/w600k_r50.onnx` | subject-worker |

## 13. Models and licensing

- **Detector:** SCRFD-10G with keypoints (`det_10g.onnx`, ~17 MB). **Recognizer:** ArcFace R50 trained on WebFace600K (`w600k_r50.onnx`, ~170 MB). Both come from InsightFace's `buffalo_l` pack (`https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip`).
- They live in `backend/models/face/` under Git LFS, and `scripts/download_models.py` fetches them if absent.
- **Licence:** InsightFace pretrained weights are for **non-commercial research use only**. A paid municipal deployment needs a commercial licence from InsightFace or a replacement model. The embedder accepts any ONNX model with a 112×112 aligned RGB input and a 512-d output, so swapping only changes a path. Thresholds must be re-calibrated after a swap.

## 14. Deployment

- `docker-compose.yml`:
  - add a `subject-worker` service (backend image, command `python -m services.subjects.main`, depends on kafka/postgres/qdrant);
  - add the `faces_private` volume on pipeline, subject-worker and api;
  - set `WW_FACE_CAPTURE_ENABLED=1` on the pipeline.
- `docker-compose.lite.yml`: add the same, plus a `qdrant` service (`qdrant/qdrant:v1.13.6`, ~100 MB RAM), because lite currently has no Qdrant.
- Migration `008` runs with the existing Alembic step.

## 15. Testing

Testing follows TDD for every pure unit.

- **Pipeline (`pipeline/tests/test_face_capture.py`):**
  - ring capacity and eviction;
  - an incident seeded from the ring plus post-roll crops;
  - the 12-crop cap;
  - `pop_due` timing;
  - an alias incident on the same track;
  - no disk writes without an incident;
  - head-crop geometry (padding, clamping, min size) as a pure function.
- **Backend unit (`backend/tests/subjects/`):**
  - quality gate and score;
  - `matcher.decide` at the boundaries (0.3999 / 0.40 / 0.5499 / 0.55);
  - per-subject max aggregation;
  - face cap eviction;
  - merge/reject/detach/delete effects (DB state + fake vector store);
  - retention sweep selection.
- **Backend integration (`@pytest.mark.face_models`, skipped when weights are absent):**
  - Real SCRFD and ArcFace on a fixture set (2 identities × 3 images, permissively licensed, `backend/tests/fixtures/faces/`), with an in-memory Qdrant (`QdrantClient(":memory:")`) and the test Postgres. It must show:
    - the same identity twice gives `auto` + repeat;
    - a different identity gives `new`;
    - the same `incident_id` redelivered gives no change;
    - a faceless image gives `no_face`.
- **API:** route tests for list/detail/face/merge/reject/detach/delete, access-log rows, and a 403 without the admin role when dev mode is off.
- **AlertConsumer:** `incident_id` is stored on insert. **`GET /api/alerts`:** the join fields for pending, `no_face`, new, repeat and review-pending cases.
- **Manual live check:**
  1. Run the pipeline on the sample stream with the kill switch on and off.
  2. Confirm there is no FPS drop or stall.
  3. Confirm `faces_private/incoming` only fills on violations.
  4. Confirm the badge appears on the alert card.

## 16. Rollout

1. Ship with `WW_FACE_CAPTURE_ENABLED=1`, and have compose set `WW_FACE_MATCH_AUTO=0.60` (stricter than the 0.55 code default) for the first week. Log every decision with its similarity.
2. Review the logged scores against operator decisions, then lower `AUTO` towards 0.55 once false auto-matches are ruled out.

## 17. Known risks

- **Night, masks and head angle** will produce many `no_face` incidents. This is expected and shown honestly rather than guessed.
- **CCTV resolution:** a face under ~40 px is rejected. Cameras far from the dump spot may rarely yield subjects.
- **Tracker ID switches:** if the tracker reassigns the person's ID near the violation, the ring for the new ID is short. Post-roll partly compensates.
- **Auth gap:** the dashboard sends no token today. With `WW_AUTH_DEV_MODE` off, subject routes return 401 and the Subjects view cannot load. With it on, the routes are effectively open and only the IP access log applies. Real protection needs the separate dashboard-login task.

## 18. Known limitations / follow-ups

Deferred on purpose; none of these loses data, but each is worth a follow-up:

- **No row locks for concurrent operator actions.** Two admins merging/detaching the same subjects at the same moment (or an action racing the worker attaching a new incident) are not serialised with `SELECT … FOR UPDATE`; the review status check catches most double actions, not all.
- **Qdrant payload staleness.** After merge/detach, a point's `subject_id` payload is stale until the post-commit upsert or the next `resync_index()` (faces are flagged `qdrant_synced=false`); a search in that window can score against the old subject, which `_score_subjects` then filters to active subjects only.
- **Subjects grid shows only the first 50** (`limit=50`); there is no pagination UI yet.
- **Detach does not create a negative constraint.** A detached incident's person can be auto-matched back to the old subject by their next violation; there is no "not the same person" record between the two subjects.
