# Container architecture — target state

Decided 6 Oct 2026. Current: 17 unique containers across the compose files.
Target: 10. Nothing implemented yet.

## Target containers

| # | Container | Role | Change |
|---|---|---|---|
| 1 | `postgres` | primary store: cameras, zones, ROI, events, alerts, evidence, config | keep |
| 2 | `kafka` | event bus, KRaft mode (no ZooKeeper), single broker, RF=1 | keep |
| 3 | `redis` | TTL throttle counters, pipeline command pub/sub, health cache, healer state | keep |
| 4 | `keycloak` | identity provider; realm needs composite roles owner->admin->viewer | keep |
| 5 | `mediamtx` | RTSP ingest + WebRTC/WHEP to the browser | keep SEPARATE (see below) |
| 6 | `api` | FastAPI. Routes, fetches and triggers ONLY -- no domain logic | keep |
| 7 | `frontend` | Vite/React dashboard | keep; note it is currently absent from `docker-compose.yml` and only defined in `lite`/`override` |
| 8 | `pipeline` | DeepStream detection + **evidence capture (Smart Record)** + engine init | absorbs `onnx-to-engine` as an init step |
| 9 | `backend-workers` | alerts dispatch, telegram, SIP, IP speaker, LPR, logs, reports, health_monitor, healer + **categoriser**, **scheduler**, **incident** | absorbs `tts-worker`, categoriser, scheduler, incident |
| 10 | `tts` | Piper TTS for the EN/KN deterrent audio | keep, behind a feature flag |

Later: `reid` as its own container when ReID lands. GPU-bound, so it must NOT
join `backend-workers`. That would make 11.

If the deterrent audio is a fixed phrase set, pre-rendering it to files at build
time removes `tts` as well -> 9.

## Removed (7)

| Container | Reason |
|---|---|
| `qdrant` | ReID not required now; also drops log vector search |
| `rtsp-streamer` | simulator only, not part of the solution |
| `onnx-to-engine` | becomes a pipeline init step, not a container |
| `vlm` | replaced by the relation model |
| `vlm-worker` | replaced by the relation model |
| `history-publisher` | dev harness that fed `wastewatch.vlm.requests`; sampled 6 frames from `./sample_videos` with a hardcoded camera UUID and `restart: "no"`. Goes with VLM. |
| `tts-worker` | merge into `backend-workers` |

## Feature flags, not containers

TTS, ReID, Telegram, SIP, IP speaker and incident become `WW_<FEATURE>_ENABLED`
settings, checked at worker startup so a disabled feature's consumer never
subscribes. Follows the existing `smtp_enabled` pattern.

## `onnx-to-engine` as a pipeline init step

On container start, before the GStreamer pipeline builds:
1. Check the engine file exists in the mounted volume.
2. Rebuild if missing.
3. Rebuild if present but corrupt (deserialisation fails).
4. Otherwise continue straight to pipeline start.

Replaces a separate one-shot container and removes the ordering dependency.

## Evidence capture moves into the pipeline

### This is an activation, not a relocation
`pipeline/app/smart_record.py` exists but is **dead code**: `create_sr_bin` has
zero call sites, `SmartRecordManager` is never instantiated in `pipeline.py`, and
every consumer defaults to `smart_record_mgr=None`.

What actually records today is `backend/app/evidence/stream_recorder.py` -- an
FFmpeg subprocess pulling the inference RTSP stream back from MediaMTX. So:
- no pre-buffer; capture starts at the trigger, the approach is already gone
- overlays are burned in, because that stream is downstream of `nvosd`
- an RTSP round-trip plus a CPU re-encode

`smart_record.py`'s own docstring claims to have replaced exactly this. It never
was activated.

### Valve semantics (learner's requirement: must not block streaming)
A `tee` pushes to every branch synchronously, so a branch that stops consuming
backpressures the tee and stalls the whole pipeline, live dashboard included.

Therefore: **create the SR bin once at pipeline construction, link it, never
unlink it.** Start/stop touches `NvDsSRStart`/`NvDsSRStop` only -- the branch
keeps flowing into the circular GPU buffer either way, which is also what makes
retroactive pre-buffer capture possible.

Do NOT dynamically add/remove the branch: that needs pad blocking and a flush on
every start and stop, and the tee stalls during the block.

### Residual risk
`NvDsSRCreate` builds `queue -> nvvideoconvert -> capsfilter -> nvv4l2h264enc ->
h264parse -> splitmuxsink`. If that head queue is not leaky and the encoder stalls
(disk full, NVENC sessions exhausted -- hit at 8 sources earlier this session),
the queue fills and backpressures the tee.

Set `leaky=downstream` with bounded `max-size-*` on it: a disk problem then drops
evidence frames instead of taking down the operator's live view. Losing some
evidence is recoverable; stalling the live stream is not.

### Clean footage requires a second tee
`pipeline.py:2707` links `nvosd -> tee`, so every existing branch carries
burned-in overlays. For clean evidence the SR bin must tap BEFORE `nvosd`:

    nvdsanalytics -> wastewatchmeta -> tee_clean -> nvosd -> tee -> dashboard/RTSP
                                           \-------------------> SR bin -> evidence file

Dashboard keeps its boxes; the evidence file has none. Also the better evidential
position -- a burned-in box around a bystander in a clip that reaches an
enforcement officer cannot be undone.

**OPEN**: whether "no bbox on the person" means clean evidence only (above), or
also removing the person highlight from live monitoring (which would undo the
sticky-red work and leave the operator no cue for who was flagged).

## Why MediaMTX stays separate

Considered merging it into `pipeline` via a multi-stage build. Rejected.

- **Restart coupling.** Pipeline restarts are frequent -- this session alone: an
  NVENC session crash, cascading EOS from dead cameras, config reloads, engine
  rebuilds. Merged, each one drops every browser WebRTC session and rebuilds
  MediaMTX's path state. Today MediaMTX survives all of it and keeps serving.
- MediaMTX sits on **both sides** of the pipeline in test topologies
  (`WW_SOURCES=cam-01=rtsp://mediamtx:8554/cam-01`), so merged, the pipeline's own
  source vanishes while it restarts.
- The code already decouples them deliberately (`pipeline.py:2618`):
  `leaky=downstream` so a MediaMTX stall drops old buffers instead of blocking.
- Merging needs a supervisor (s6/supervisord), which muddles `docker restart`,
  health checks and exit codes.
- `bluenviron/mediamtx:latest` is a maintained third-party image (~30 MB);
  vendoring the binary means owning its releases and CVEs.

Gain would have been one container and loopback RTSP instead of the Docker
bridge -- both marginal on a single host.

If removing a component (not hiding it) ever becomes the goal, the real option is
serving WebRTC directly from the pipeline via `webrtcsink`, which removes the RTSP
round-trip entirely. Bigger change, separate decision.

## Kafka: assessed, kept

Not needed at this scale -- one broker, RF=1, single-voter KRaft quorum, 12
cameras. Postgres + Redis could cover all four things it provides:

| Need | Replacement |
|---|---|
| fan-out (alert -> WS, dispatcher, evidence, TTS, speaker) | one transaction writing alert + job rows, then `LISTEN/NOTIFY` |
| durable work queues | `SELECT ... FOR UPDATE SKIP LOCKED` |
| high-rate disposable metadata (~180 msg/s) | Redis pub/sub |
| buffering a slow worker | rows sit in the queue table |

The transactional version is arguably better for alerts: today an alert can commit
while its TTS job is lost, because they are separate systems.

**The one real loss:** Kafka decouples the pipeline from Postgres availability.
Today detections keep publishing if the DB is down. Writing straight to Postgres
means a DB restart stalls the detection path. Solvable with a local spool or Redis
as a write-ahead buffer, but it is deliberate work.

**Decision: keep for now.** It touches the pipeline producer, 4 consumers, 8
workers and 9 topics -- about a week with a real regression surface -- and changes
nothing the client sees, while 12 confirmed bugs and 7 security items do. Revisit
on memory pressure, actual Kafka failures, or a deployment where one fewer
container matters. For a NEW deployment: start with Postgres + Redis and add Kafka
only when something demands it.

## Why Redis is not a removal candidate

Each use exercises a property Postgres lacks:

| Job | Keys | Property used |
|---|---|---|
| Alert throttle | `throttle:camera:{id}`, `throttle:hourly:{id}` | native TTL expiry -- no sweeper job |
| Backend -> pipeline commands | `ww:pipeline:commands`, `ww:config:*_override` | pub/sub into a GStreamer process with no HTTP server |
| Health cache | `ww:health:*`, `ww:camera:{id}:fps`, `:last_frame_ts` | cheap overwrite of data worthless after 10 s |
| Healer state | `ww:healing:last`, `ww:healing:log`, `ww:misses:log` | scratch state |
| Live scratch | `ww:alerts:active`, `ww:cameras:list`, `ww:lpr:last_active` | scratch state |

Moving these to Postgres means a cleanup job for expired throttles, making the
pipeline's control path depend on the DB, and ~6 UPDATEs per 10 s of WAL churn for
data nobody queries. It is also the cheapest container here -- a few MB RSS, no JVM.

If Kafka is ever removed, Redis becomes MORE important: it is the natural home for
the metadata firehose.

## Noted gaps

- `docker-compose.lite.yml` has no `keycloak`. Now that auth is wired into the
  frontend and all three WebSockets, lite can only run with `WW_AUTH_DEV_MODE=1`,
  which grants owner+admin+viewer and bypasses WebSocket auth. So the lite stack
  cannot exercise login or role gating at all. Either add `keycloak` to lite or
  document lite as explicitly dev-mode-only -- "it works on lite" no longer
  implies auth works.
- `frontend` is not in `docker-compose.yml`, so the production stack has no
  container serving the dashboard.
