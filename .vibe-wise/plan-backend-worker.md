# Plan — backend worker: one scheduler, seven job families

Date: 2026-10-06
Status: **design. Not implemented.**
Replaces: `backend/services/combined_workers/main.py` (asyncio.gather of 8 mains)

---

## 1. The shape the learner specified

```
scheduler  (unified event pool, asyncio only, one process)
├── alerts
├── sip          ← PARKED. IP Speaker + TTS + IVR calling
├── evidence     ← incident + reId + lpr + backup scheduler (GDrive/Dropbox/S3) + messaging apps
├── ops          ← health_monitor + logs (incident logger) + healer
└── reports      ← categoriser (DBSCAN/HDBSCAN derived categories)
```

**Five** families, one scheduler, one process, **asyncio throughout** -- no
multiprocessing. Sub-modules fold in rather than getting their own
containers.

### Why merging health_monitor + logs + healer is more than tidying

They are one loop: observe -> record -> remediate. Today the healer reads
health state back out of Redis that the health monitor just wrote, in the
same container. Merged, that is in-process state: one less round trip, and
no window where the healer acts on a health value that has already changed.

**One thing the merge changes.** The healer has `/var/run/docker.sock`
mounted (`docker-compose.yml:193`), which is root-equivalent on the host.
Health monitoring and log *ingestion* now share a process with that socket.
It is not a regression -- all eight workers already share that container --
but it sets a rule going forward: **untrusted input must not enter this
process.** Browser console logs are untrusted, which is why
`/ws/client-logs` writes from the API process and this family only consumes
the internal `wastewatch.logs` topic. Keep it that way.

---

## 2. What this replaces, and why it is worth replacing

`combined_workers/main.py` is `asyncio.gather` over eight `main()` functions.
Each one builds its own timers and registers its own SIGTERM handler. Its
own docstring records the consequence:

> each service's own main() still registers its own SIGTERM/SIGINT handlers
> via loop.add_signal_handler on the SAME signal -- only the last-registered
> handler per signal actually fires

So six of eight workers never see a graceful shutdown signal. Nine timed
loops across the process re-fire on their own cadence with no coordination,
no coalescing, and no shared backpressure.

---

## 3. "Ring buffer" cannot be one policy for all seven

A ring buffer is bounded and overwrites the oldest entry when full. That is
correct for some of these families and **data loss for others**:

| Family | Full-queue policy | Why |
|---|---|---|
| alerts | **never drop** | Dropping an alert is losing a violation. Must persist and apply backpressure, not overwrite. |
| evidence | **never drop** | Same — the evidence is the product. |
| sip / IVR | drop oldest | A deterrent announcement that is 30s late is worse than none. |
| ops — health tick | drop oldest | A stale health tick is worthless; only the newest matters. |
| ops — log ingest | drop oldest + count | Already the IncidentBuffer's model: bounded, and the drop count is recorded so the gap is visible. |
| ops — healer | coalesce | Re-running the same remediation concurrently is how you make things worse. |
| reports / categoriser | **coalesce** | Two "recategorise" requests a second apart should be one run, not two. |

Note the three `ops` sub-jobs keep **different** policies even though they
share a family. The policy is per job, not per family -- merging the modules
does not merge their queue semantics.

**So the pool is one scheduler with a per-family queue policy, not one ring.**
If every family shares a single ring, an alert storm evicts queued evidence
writes, and that is unrecoverable.

Coalescing is the biggest single win over the nine current timers: they each
fire unconditionally today, whether or not the previous run finished or
anything changed.

---

## 4. The categoriser under an asyncio-only rule

DECIDED: asyncio throughout, no multiprocessing.

The constraint this has to survive: DBSCAN/HDBSCAN over months of events is
seconds of CPU-bound work. Left on the event loop, **every alert stalls
behind a clustering run**, and TTS/SIP dispatch with it. `async def` does
not make CPU-bound code yield — there is no await point inside a numpy call.

The asyncio-native answer, which stays inside the rule:

```python
# Still asyncio -- a thread executor, not a process pool.
await loop.run_in_executor(self._cpu_pool, run_categoriser, camera_ids)
```

`run_in_executor` is asyncio API. With a small `ThreadPoolExecutor` the loop
stays responsive, nothing is pickled, and DB handles can be passed normally
— the problems a process pool would have introduced do not arise.

**The honest caveat:** this works because numpy and scikit-learn release the
GIL inside their C/Cython inner loops. The pure-Python glue around them does
not, so expect *jitter* on the loop during a clustering run, not a freeze.
If measurement later shows alert latency actually suffering, the options in
order are: chunk the clustering and `await asyncio.sleep(0)` between chunks;
or move reports to its own container. **Do not** reach for
multiprocessing inside this process — that was the thing ruled out.

Affected: **reports/categoriser** only. Everything else in the five
families is I/O-bound (Kafka, Redis, Postgres, HTTP, SIP) and is genuinely
async.

---

## 5. Supervision and shutdown

**One signal handler, owned by the scheduler.** It sets a single shared
`asyncio.Event`; every job observes that event instead of installing its own
handler. This is the direct fix for the documented bug above.

**Per-job crash policy.** `asyncio.gather` today lets one unhandled
exception tear down the group. Each job runs as a supervised task:

- restart with exponential backoff
- a circuit breaker, so a permanently broken job stops hot-looping and is
  reported once instead of every 2 seconds
- a crash in one family never stops another — a broken categoriser must not
  stop alerts

**Drain on shutdown, for the never-drop families only.** Alerts and evidence
finish their queue (bounded by a deadline) before exit; the drop-oldest
families are abandoned immediately. Docker gives ~10s between SIGTERM and
SIGKILL, so the deadline has to fit inside that.

---

## 6. SIP family — PARKED

**DECIDED: the whole sip family is deferred.** Nothing below is built. It is
recorded so the decisions already made are not re-litigated when it is
picked up.

Scope when it resumes: IP Speaker + TTS + IVR calling. `history-publisher`
is **excluded** (its only consumer was the VLM request topic, which is
gone), and the text generator moved out to `reports`/`evidence` — see §6.4.

### 6.1 What already exists

In `app/alerts/sip_gateway.py`: **three outbound backends** —
`pjsua` (SIP), Twilio REST, and Asterisk AMI — plus `EscalationWorker` with
a Redis-backed acknowledgement cascade (`escalation:ack:{camera_id}:{track_id}`).

So outbound calling exists. **IVR is the inbound half: capturing a keypress
so the person called can acknowledge the violation from the phone**, closing
the escalation loop without the dashboard.

### 6.2 OPEN (W3) — how DTMF gets back to us

This is the one that changes the deployment, not just the code.

| Option | How DTMF returns | Cost to the deployment |
|---|---|---|
| **A. Provider IVR** (Twilio `<Gather>`, or Exotel/Knowlarity for India) | Provider POSTs the keypress to a **publicly reachable HTTPS webhook** | Breaks the prod posture: "no port exposed except through nginx" becomes "plus an inbound path from the internet to the API". Also per-minute cost and an internet dependency for alert acknowledgement. |
| **B. On-prem Asterisk/FreeSWITCH** via the AMI backend already wired | DTMF handled in the dialplan; AMI event or AGI callback stays on the LAN | One more container and a SIP trunk to configure. No public ingress, works on an isolated municipal network. |

B fits the deployment that was already chosen. A is less infrastructure but
contradicts a constraint that was set deliberately.

### 6.3 OPEN (W4) — anyone who answers the phone can acknowledge

An IVR ack is far weaker than a dashboard ack: whoever picks up presses 1.
A mis-dialled number, a shared desk phone, or a forwarded line can clear a
violation that a court case later depends on.

Minimum: record the acking phone number and the call id on the alert, and
mark IVR acks as a distinct, weaker ack type than an authenticated dashboard
ack. Better: a per-call PIN read out only to the escalation contact.

**Do not let an IVR ack stop the escalation cascade silently** — it should
record the ack and still notify the next level that it was acked by phone.

### 6.4 RESOLVED (W5) — it was never the VLM

The "VLM" in the original family list is a **small local JSON -> text
generator** (BitNet.cpp / llama.cpp class, CPU), not a vision model. So
there was no conflict with removing `vlm`/`vlm-worker`; they are different
things.

**It does not go in `sip`.** It moves to `reports` and `evidence`:

| Use | Generator or template |
|---|---|
| Public address over an IP speaker | **template, always** |
| Report summaries on the dashboard | generator |
| Telegram / messaging-app bodies | generator |
| Incident narratives attached to evidence | generator |

The reason is not style. A sentence played over a speaker in a public place
is the municipality speaking, and `app/tts/public_warning_templates.py`
exists to bound what it can say. It raises `UnsafeZoneNameError` for a
personal descriptor, so an announcement can name a *place* but never a
person. A free-text generator bypasses that guard entirely — there is
nothing left to validate when the whole string is generated.

**Performance note for when this is built:** llama.cpp-class inference is
seconds of CPU and is a worse fit for the asyncio rule than DBSCAN, because
its Python bindings frequently do not release the GIL — so
`run_in_executor` may not protect the loop the way it does for numpy. It
needs its own measurement before sharing a process with alert dispatch, on
a box that is already running DeepStream.

### 6.5 BUG found while deciding W5 — the safety guard does not work

```
E  Failed: DID NOT RAISE UnsafeZoneNameError
FAILED test_unsafe_zone_names_are_rejected[near the man in the blue jacket]
```

`_SAFE_ZONE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9 \-]{1,40}$")`

"near the man in the blue jacket" is 31 characters of letters and spaces, so
it **matches** the whitelist and passes. The other three cases in that
parametrize (`<script>`, 41 chars, empty) pass because those are genuinely
character-level problems. A character-class regex cannot detect that a
string describes a person.

**The fix is not a better regex.** Zone names must not be free text at
announcement time — they should be picked from the zones table (already P1
in `docs/BACKLOG.md`). Then "is this safe to say out loud" is decided once,
by a person, when the zone is created.

Until then, a public announcement can name an individual.

---

## 7. Remaining open decisions

| # | Decision | Options |
|---|---|---|
| ~~W1~~ | ~~Where cadence is declared~~ | **DECIDED: DB rows, with code defaults as fallback.** See §8.1. |
| ~~W2~~ | ~~CPU-bound dispatch~~ | **DECIDED: asyncio only, thread executor via `run_in_executor`** |
| W3 *(parked)* | IVR DTMF transport | A: provider webhook (public ingress) / B: on-prem Asterisk (extra container) |
| W4 *(parked)* | IVR ack strength | phone number recorded / per-call PIN / IVR cannot ack at all, only inform |
| ~~W5~~ | ~~"VLM" in the sip family~~ | **DECIDED: a small local JSON->text generator, moved to reports/evidence. Templates keep the public address.** |
| W6 | Never-drop persistence | Postgres queue table / Kafka (still present) / Redis stream |

W6 matters: "never drop" is only true if the queue survives a process
restart. An in-memory queue marked never-drop still loses everything on a
crash — which is exactly when you most needed it.

---

## 8. The UI — one System / Diagnostics screen

DECIDED: the scheduler gets a simple UI. Cadence therefore lives in DB rows
rather than a code table, since the point is changing it without a deploy.

This screen is also the natural home for two things already asked for and
built or half-built, so they stop being three separate screens:

| Section | Backing API | State |
|---|---|---|
| **Jobs** | `GET/PATCH /api/system/scheduler/jobs` | to build |
| **Browser console capture** | `GET/POST /api/system/logs/client/capture` | **built** |
| **Log level** | the dynamic debug-level API | not built |

### 8.1 Where the interval lives

DECIDED: **DB rows, with a code-level default per job as the fallback.**

The UI needs to edit the interval, and a screen cannot edit a hardcoded
constant or an env var -- so the live value is a row. But the DB must not
become load-bearing for *scheduling*: if Postgres is unreachable at startup
and the interval only existed as a row, the scheduler would come up with no
cadence and nothing would run. A DB blip would be a total outage of every
background job, which is worse than a stale interval.

So each job carries its own default in the registry, and resolution is:

```
interval = db_row.interval_s  if the row loaded and is >= min_interval_s
           else JOB_DEFAULTS[job].interval_s
```

Falling back is logged at WARNING once per job, not per tick -- otherwise a
DB outage writes a log line per job per interval, in the process that
ingests logs.

```
scheduler_jobs
  job                 interval_s  enabled  updated_by  updated_at
  ───────────────────────────────────────────────────────────────
  alerts                   5         t      admin1     ...
  ops/health              10         t      -          ...
  ops/healer              60         t      -          ...
  ops/logs                 5         t      -          ...
  evidence/backup       3600         t      admin1     ...
  reports/categorise     900         f      admin1     ...
```

`min_interval_s` is deliberately **not** a column. It is a floor that exists
to stop someone stalling the loop (see control rule 5), so it belongs in
code where the UI cannot raise it. A row below the floor is rejected on
write and ignored on read.

`updated_by` / `updated_at` cover control rule 2 -- who changed a cadence is
a question asked after an incident.

### Jobs table — one row per job

| Column | Why it is on screen |
|---|---|
| Job | family + sub-job (`ops / healer`) |
| State | idle / running / backoff / **circuit open** — circuit-open is the one that needs to be visible, because a permanently broken job is otherwise silent |
| Last run / Last result | ok, or the error, with duration |
| Next run | absolute time, not "in 30s" — a countdown that is wrong is worse than a timestamp |
| Queue depth | the number that tells you a never-drop family is backing up |
| Dropped | cumulative, for the drop-oldest jobs. A rising count is the signal the cadence is wrong |
| Interval | editable → writes the DB row |
| Actions | Enable/disable, **Run now** |

### Rules for the controls

1. **Admin-gated**, same as the rest of `/api/system/*`. Disabling the
   alerts job stops violation delivery, so it belongs behind the same wall
   as the health detail endpoints.
2. **Audit every change.** Who disabled the alerts job, and when, is a
   question that gets asked after an incident, not before it.
3. **Confirm on disable for the never-drop families.** Pausing alerts or
   evidence is a decision to stop collecting the product; it should not be a
   stray click on a toggle.
4. **"Run now" must coalesce, not stack.** Pressing it five times queues one
   run. The queue policy already says so for the coalescing jobs; the UI
   must not be a way around it.
5. **Interval has a floor per job.** A UI that lets someone set the
   categoriser to 1s turns the thread-executor jitter into a permanent
   stall. The floor comes from the job registry, and the input enforces it.
6. **Read-only when the scheduler is unreachable** — show the last known
   state and say it is stale, rather than rendering empty rows that look
   like "no jobs configured".

Mobile: this is a table, which is the worst case for 375px. It should be
cards on mobile, not a horizontally scrolling table.

---

## 9. Build order once decided

1. Scheduler core + job registry + per-job queue policy + one SIGTERM
   handler + supervised restart. No behaviour change yet.
2. Move the existing workers onto it unchanged, one at a time, so a
   regression is attributable. **sip is parked** -- it stays as it is,
   running under the scheduler but unchanged in behaviour.
3. Collapse health_monitor + logs + healer into the `ops` family, dropping
   the Redis hop between the health writer and the healer reader.
4. Fold in the sub-modules (reId, lpr, backup, messaging, incident).
5. Categoriser onto the thread executor.
6. ~~IVR~~ — parked with the rest of the sip family.

Step 1 and 2 are the ones that pay off immediately: they fix the SIGTERM
bug, the gather-teardown risk, and the nine uncoordinated timers.
