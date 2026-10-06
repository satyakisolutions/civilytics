# Plan: event categorisation (derived, clustered)

Status: DESIGNED. All blocking decisions taken. Not implemented.
Supersedes the earlier "stored vs derived category" open decision.

---

## What the categories are

Four labels drive Analytics, Event History and Timeline Playback:

    Midnight Walk / Midnight Vehicle / Daytime Walk / Daytime Vehicle

The schema's existing enum is a different axis entirely and is NOT this:

    CREATE TYPE event_type AS ENUM ('dumping_detected','suspicious_activity','repeat_offender');

Theirs is kind-of-event; this is (actor class) x (when it typically happens).

## Learner's design: categories are discovered, not configured

Peak times are NOT an admin setting. They are clustered from observed event
timestamps, computed PER ACTOR CLASS:

    walk events    -> cluster timestamps -> night cluster -> "Midnight Walk"
                                         -> day cluster   -> "Daytime Walk"
    vehicle events -> cluster timestamps -> night cluster -> "Midnight Vehicle"
                                         -> day cluster   -> "Daytime Vehicle"

The midnight/daytime word DESCRIBES where that class's cluster landed. It is not
a preset window the events are tested against.

### Density clustering, so "uncategorised" is a native outcome
Learner's requirement: a low-count or odd-time event "should be considered as an
event but not categorized."

That rules out k-means, which forces every point into a cluster. DBSCAN/HDBSCAN
labels sparse points as noise natively, and its two parameters ARE the learner's
two stated conditions:

| Parameter | Meaning | Learner's words |
|---|---|---|
| `min_samples` | events needed for a cluster to count | "dumping count is less" |
| `eps` | how close in time events must be to join a cluster | (time radius) |

So the rule is the algorithm's output, not post-processing.

### Time is circular -- the one implementation trap
23:30 and 00:30 are ONE hour apart but 23 apart as raw hour values. Clustering on
raw hours splits the midnight cluster in two at the date boundary, and that is the
cluster carrying the most events. Represent each timestamp as an angle on a
24-hour circle and use angular distance.

### Category identity is a stable pair
Identity is `(actor_class, day|night)` -- a fixed 4-element space -- NOT an
arbitrary cluster id. So names and colours stay stable across recomputes even when
a cluster is absent.

## Learner decisions taken
1. Category is **stored** on the event row at detection time (fast reads).
2. A threshold change **backfills** that zone's history, so one taxonomy holds
   across Analytics, History and Timeline.
3. Peak times are **derived by clustering**, not configured.
4. Clustering runs **per actor class**.
5. Outliers stay events but carry **no category**.
6. If a cluster is absent, only the categories that exist are shown. The
   high-frequency cluster is then the only category for that class.
7. `eps` and `min_samples` are **admin-configurable from the UI**.
8. Those thresholds are **per zone**, e.g.
       Adugodi      eps 45 min  min events 8
       Lakkasandra  eps 60 min  min events 4
       Hongasandra  eps 45 min  min events 6

## Consequences (Claude's analysis, follows from the above)

### Per-zone thresholds settle DEC-1: a real `zones` table is needed
Per-zone config needs a keyed home. `cameras.zone` is bare `TEXT NOT NULL`
(`migrations/...sql:17`) and `alerts.zone` duplicates it, so threshold rows hung
off a free-text name orphan as soon as a zone is spelled differently.

### Backfill becomes a recurring job, not only an admin action
With configured windows, backfill ran on save. With derived windows it also runs
whenever a recompute moves a cluster boundary. Same mechanism, different cadence.
Needs a recompute cadence and ideally a minimum movement before rewriting history,
or week-over-week trends shift for reasons unrelated to dumping behaviour.

### No circularity
Frequency is computed from raw timestamps, which do not depend on category, so
windows -> categories flows one way only.

### A `min_samples` slider rewrites the dataset
Since category is stored and backfilled, moving that slider reclassifies every
event. Lower it and spurious categories appear; raise it and most events go
uncategorised. The admin cannot see that coming.

**Required: preview before apply.** "With these values, 171 of 186 events
categorise into 4 groups; 15 uncategorised; clusters at 22:40-00:50 and
05:20-08:10." Cheap to compute and turns a blind setting into a visible one.
Build it as part of the config UI, not after.

### Sparsity is the real risk on per-zone derivation
Splitting by actor class halves every histogram. Lakkasandra at ~49 events/week
becomes ~25 walk and ~24 vehicle over 24 hourly buckets -- about one per bucket.
Mitigated by the preview (tuning becomes empirical, not guesswork) and by a long
trailing window. Still worth a citywide fallback until a zone passes a minimum
event count.

### UI must render N categories, not 4
The prototype hardcodes four everywhere: legend, four heatmap rows, four donut
slices, four bar series per day. Colour must be keyed on `(actor_class, half)`,
never on array position -- otherwise a month with no Daytime Vehicle cluster
shifts every colour one slot and charts change colour week to week.

This also kills prototype bug B10: `e.cat % 2` derives actor type from legend
index and cannot survive a variable-length category list.

### Totals no longer equal the sum of categories
The prototype donut reads 64+32+54+36 = 186 against "Total verified events 186".
With uncategorised events the four categories sum to LESS than the total. The
donut needs a fifth "Uncategorised" slice or a separate line, or the page silently
under-reports. Same for the heatmap: uncategorised events fall in hours currently
rendered shaded/empty, so they would be invisible rather than merely uncategorised.

## Data the pipeline must start persisting
| Field | Why | Status |
|---|---|---|
| actor class (person/vehicle) on `violation_events` | the clustering splits on it; schema has only `person_id TEXT` | MISSING |
| category (nullable -- null = uncategorised) | stored at detection, backfilled on recompute | MISSING |
| `zones` table + per-zone `eps`/`min_samples` | per-zone thresholds need a keyed home | MISSING |

The FSM already distinguishes the classes (`class_name == "vehicle"`, plus
`vehicle_stop_threshold_s` added this session); nothing writes it to the row.

## Still open
- Recompute cadence, and the minimum cluster movement that justifies a backfill.
- Trailing history length the clustering runs over (30 / 90 days / all).
- `eps` and `min_samples` defaults. Deferred deliberately: fit against the real
  Civil data rather than guessing, which requires actor class to be persisted
  first. ~20 source clips of real events are available.
- Citywide fallback threshold -- the minimum zone event count below which a zone
  uses citywide clustering.
