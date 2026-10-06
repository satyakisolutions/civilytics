# Plan: Analytics drill-through and URL routing

Status: designed. Learner decisions taken. Nothing implemented.
Scope: outbound navigation from the Analytics page, and the routing mechanism it needs.

## Problem
Analytics is currently a dead end. Its most actionable lines ("Midnight Walk: 64 events
(34.4%)", "Events by zone: Adugodi 74") cannot be clicked through to the events behind
them, so an operator reads the insight and then manually reconstructs the equivalent
filter somewhere else.

## Learner decisions

### Navigation mechanism: real routing (Option B)
Filters live in the URL, not in React context.

    /history?category=midnight_walk&date=2026-09-27

Rejected alternative: a filter payload on `MonitoringContext`. That was the smaller
change but kept filters in memory only -- no shareable links, no refresh survival,
browser back exits the app.

Chosen because links are shareable (a supervisor can be sent the events behind a
number), refresh preserves state, back works, and the breadcrumbs already drawn in the
screen spec become real rather than decorative.

### Heatmap cell target: Timeline Playback, camera + date only
Clicking an hourly heatmap cell opens Timeline Playback for that camera and date.
It does NOT pre-select an event. All of that day's events render as time markers
spread across the 24-hour timeline; the operator clicks a marker and the event is
displayed below.

This is Screen 4 as already specced (24-hour axis, four colour-coded series, the
`Events (10)` list, selected event rendered below). Not a new screen.

## Current state (verified)
- `App.tsx` has a single route `/`. All view switching is
  `currentView: 'monitoring' | 'analytics' | 'history' | 'cameras' | 'settings' | 'map' | 'timeline'`
  in `frontend/src/context/MonitoringContext.tsx:19-20`.
- Views own their filters privately, e.g. `HistoryView.tsx:43` initialises
  `selectedFilter` to `'all'` on every mount, so an incoming filter would be lost.
- `setCurrentView` callers: `Sidebar.tsx`, `AlertPanel.tsx`, `CameraMapView.tsx`,
  `CameraManagementView.tsx`. Five files total including the context.
- The screen spec's nav lists "Identification & Tracking", which is absent from the
  `currentView` union. Screens 6 and 7 currently have no way to be reached.

## Work required
1. Add a route per view in `App.tsx`; retire `currentView`/`setCurrentView` from
   `MonitoringContext` in favour of router navigation. Touches the four caller files.
2. Add an `identification & tracking` route. Screens 6/7 are otherwise unreachable.
3. Convert each view's filter `useState` to read from `useSearchParams()`.
4. Make Analytics panels navigate with filter params.

## Proposed drill targets (Claude's proposal; targets are the learner's to set)
| Panel | Click | Lands on |
|---|---|---|
| Events today: 28 | card | History, today |
| Events this week: 186 | card | History, the period |
| Repeat match groups: 12 | card | Identification & Tracking (view does not exist yet) |
| Avg. review time: 42 min | card | unreviewed worklist (does not exist) |
| Daily Peak Pattern | one bar segment | History, that date + that category |
| Violation categories | one donut slice | History, that category, period |
| When dumping happens | one heatmap cell | Timeline Playback, that camera + date (DECIDED) |
| Events by zone | one bar | History, that zone, period |
| System health | online / warning / offline | Camera Management, filtered by health |

Seven of nine land on History, which makes History's filter contract the real design
object rather than Analytics. History currently filters on status, free-text search and
zone only -- it has no category, date-range or hour filter.

## Dependency
Three of these targets need the event category taxonomy
(Midnight/Daytime x Walk/Vehicle), which does not exist in the schema. See the open
decision in `plan-analytics-data.md`. Drill-through to a category filter cannot work
before that lands.

## Open
- What the Timeline detail pane shows on arrival, before any marker is clicked:
  an empty prompt, or the day's first event auto-selected. The spec shows a selected
  event (07:02) but does not say whether that is the arrival state.
- Whether History and Timeline Playback stay separate screens. The learner referred to
  the Timeline target as "camera based History"; the spec keeps them separate
  (Screen 5 cross-camera searchable list, Screen 4 single-camera playback). Worth
  settling deliberately rather than letting two overlapping screens emerge.
