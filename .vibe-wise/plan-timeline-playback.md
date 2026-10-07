# Plan — Timeline Playback

Date: 2026-10-07. Status: **designed, not built.**
Related: `docs/UI_ACTION_EVENT_TREES.md` §6, `docs/BACKLOG.md`

---

## 1. Decisions

| # | Decision | Chosen |
|---|---|---|
| TL1 | Recording model | **Clip-only.** `mediamtx.yml:53 record: no` stays. |
| TL2 | Interaction | Video-editor timeline: zoom in/out, drag to scrub |
| TL3 | Scroll | **Continuous drag** (land mid-day) **+ prev-day / next-day buttons** |
| TL4 | Default view | Today, 24 hours |
| TL5 | Time format | **24-hour throughout** (`02:14`, never `2:14 AM`) |
| TL6 | Future | Nothing shown after now; right bound is now |
| TL7 | Date label | Decided by the **right-edge marker** |
| TL8 | Current time | Highlighted on the timeline |
| TL9 | Future region | **Greyed, not truncated** |
| TL10 | How far back | **3 days.** Beyond that, a CTA to the customer's own Drive/backup |
| TL11 | Lanes | **Multi-lane (all cameras with events in view), single viewing panel** |

Separate and deliberately different: **Analytics** uses a circular
time-of-day dial (a distribution that wraps at midnight). Timeline Playback
is linear absolute time, where wrapping means nothing. Two controls, two
axes.

---

## 2. Clip-only is the constraint everything else follows from

```
mediamtx.yml:53              record: no
stream_recorder.py:37        max_duration_s = 120.0   # hard cap per clip
priority_alert_router.py:142 smart_record_mgr = None  # Smart Record is dead code
```

Footage is **~2-minute FFmpeg clips pulled from MediaMTX around events**.
There is no continuous recording, so the timeline is a sparse event ribbon,
not a scrubbable day.

Accepted consequence: the timeline **cannot answer "what happened twenty
minutes before the alert"**. That matters for the waste-aging question
("how long has this been here"), which will have to be answered from event
history and dwell times rather than from footage.

### The rendering problem this creates

A 2-minute clip inside a 24-hour view is **0.14% of the width — about 2
pixels** on a 1400px timeline. Rendered true to scale, a day with eight
dumping events looks completely empty.

So **marker width is decoupled from clip duration**:

| Zoom | Span | Marker rendering | Data fetched |
|---|---|---|---|
| Day | 24 h (default) | Fixed ~8px, min 4px hit target. NOT to scale. | event rows + clip index |
| Mid | ~4 h | Fixed; overlaps begin to separate | event rows |
| Hour | ~1 h | **Transition** — clip extent becomes visible | event rows |
| Fine | ≤15 min | True to scale; real clip edges matter | clip media |

Above ~1 hour you are *finding* events, so visibility wins. Below it you are
scrubbing *inside* a clip, so duration must be honest.

### Clustering

Overlapping markers collapse into one marker with a count badge, splitting
as you zoom in. Without it, markers smear together exactly where activity is
densest — the part most worth reading.

### Three states, not two

```
├──────── no footage ────────┤███ clip ███├──── no footage ────┤██ clip ██├
                              02:13–02:15                       03:01–03:03
```

"No footage" must be visually distinct from "not loaded yet". If they look
the same, an operator drags to an empty spot, sees black, and reports the
system as broken. This is the biggest UX risk in the feature.

---

## 3. Bounds and the right edge

**Right bound is now.** No overscroll into the future.

**The future is greyed, not truncated** (proposed default — flag if you want
a hard clip instead). The full 24-hour axis renders; everything after now is
greyed and non-interactive.

Reason: truncating at now makes an hour's width change as the day passes, so
the control rescales under the operator's cursor. At 00:15 a truncated day
view would be fifteen minutes wide — a different control from the one they
used yesterday evening. Greying shows nothing after now while keeping the
scale fixed.

**Left bound** is the earliest retained clip. Before that: empty axis with
"no recordings before DD/MM", not infinite blank.

**Now marker** moves, so it gets its own cheap repaint — one `requestAnimationFrame`
loop updating a single line, never a re-render of the timeline. At the live
edge, "now" means the **live WebRTC stream**, so the playhead reaching now
hands over to the player rather than running off the end of a file.

**Jump-to-now** = scroll to the right bound. Needed because getting back
from last Tuesday is otherwise a long drag.

---

## 4. Dates

**Label comes from the right-edge marker** (TL7), plus a tick at **every
local midnight** crossed, since a multi-day window crosses several and one
label cannot orient the operator.

**Local midnight, not UTC.** Every timestamp is `timestamptz`; a day
boundary computed in UTC lands at 05:30 IST, and every night cluster then
appears to straddle two days. Same trap as circular time, different coat.

---

## 5. Fetching

**Level of detail per zoom.** At 24-hour zoom across 12 cameras, shipping
individual rows is the request that takes eight seconds and renders a
million points. Coarse zoom returns aggregates; rows only below the 1-hour
threshold. One endpoint, a `bucket` parameter.

**On drag:** debounce, and prefetch one screen-width either side so scroll
does not stutter. Cache by `(zoom level, time bucket)` so scrolling back is
instant.

**Rendering:** canvas, not a DOM node per event. At hour zoom with 12 camera
lanes it is thousands of marks repainted per scroll frame.

---

## 6. Mobile

The densest control in the app, and the whole app currently has **zero
responsive breakpoints**.

- Pinch-to-zoom maps directly onto TL2.
- 12 camera lanes do not fit 375px → mobile shows **one lane** (the selected
  camera) with a camera picker above it.
- Prev-day / next-day buttons matter more on touch than on desktop, since
  precise dragging is harder.

---

## 7. Open

All resolved. See §8 and §9 below.

---

## 8. TL10 — three days, then hand off

The timeline scrolls back **3 days**. Past that the left bound shows a CTA
pointing at the customer's own Drive / backup target rather than an empty
axis.

Why this is the right shape, not just a cheap one:

- Clip-only at ~2 min per event is small, so 3 days of hot storage is
  trivial. The limit is about **keeping the scrubber fast**, not about disk.
- It makes the backup target a *product surface* rather than a silent
  background job. The operator learns where older evidence lives by being
  sent there.
- `tenants.retention_days` (365 for Adugodi, 90 for Lakkasandra) governs how
  long the backup keeps it. The 3-day window is the local cache in front of
  that, and the two numbers are independent on purpose.

The CTA must name the destination ("Open Drive backup"), not say "no data".
"No data" at the edge of a 3-day window reads as data loss.

---

## 9. TL11 — multi-lane, one viewing panel

**Recommended and taken: all cameras that have events in the visible window
render as stacked lanes; one viewing panel above plays the selected clip.**

The reason is the investigation itself. A dumping event is rarely one
camera: a vehicle enters at the Entry camera, stops at Main-North, leaves
past Market-Rear. On a single-camera timeline the operator has to remember
timings and switch cameras by hand to reconstruct that. Stacked lanes make
the route **visible as vertical alignment** — three markers at roughly the
same x across three lanes is the pattern, and no amount of camera-switching
shows it as well.

What stops it becoming unreadable:

- **Only lanes with events in view are shown.** Twelve empty lanes is worse
  than one. A collapsed "N cameras with no events" row expands on demand.
- **One viewing panel, not one per lane.** Only one clip plays at a time —
  which also keeps this to a single video decoder, the thing that matters on
  a phone.
- **Lane height shrinks as lane count grows**, with a floor; past the floor
  the list scrolls vertically while the time axis stays pinned.
- **Mobile collapses to one lane** plus a camera picker (§6), since vertical
  alignment is unreadable at 375px anyway.

Rejected alternative — single camera with a picker — is simpler to build and
loses the one thing the timeline is uniquely good at.
