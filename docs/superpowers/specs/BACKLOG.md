# Spec Backlog

Running list of future work identified during development, not yet
brainstormed/spec'd. Each entry: what, why, and enough context to pick up
the thread later without re-deriving it.

---

## Real-time DB push instead of REST polling (frontend cameras/analytics)

**What**: Frontend currently polls REST endpoints (`GET /api/cameras`,
`/api/analytics/*`, `/api/alerts`, etc.) instead of getting pushed updates.
Postgres doesn't have "webhooks" natively, but `LISTEN`/`NOTIFY` (in-DB
pub/sub) or WAL-based logical replication (what Supabase Realtime/Debezium
build on) can provide webhook-like push behavior.

**Why**: Raised while debugging why newly-added cameras didn't show up
live and health status was stale — polling-based UI updates lag behind
actual state changes.

**Recommended direction**: Don't build a new Postgres-webhook layer from
scratch. This repo already has WebSocket infrastructure
(`ws_events`/`ws_alerts` routers in `backend/app/api/routes/`) that isn't
fully wired to camera/analytics changes. Use `LISTEN/NOTIFY` (or simpler:
trigger a push from the existing write paths like `POST /cameras`,
`PATCH /cameras/{id}`) to push over those existing WebSocket channels
instead of introducing a separate real-time layer.

**Status**: Not brainstormed yet. Needs its own design pass (which writes
trigger pushes, what the frontend subscribes to, how it falls back if the
WebSocket drops) before implementation.
