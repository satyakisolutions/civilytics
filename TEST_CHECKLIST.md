# Pre-Handoff Test Checklist — Civilytics

Run everything here green **before** passing the build to a tester. Two parts:
automated unit tests (must pass in CI/local), then manual smoke checks for
things unit tests don't cover (deploy wiring, auth, streaming).

---

## 1. Automated unit tests

### Backend (pytest) — 34 suites
```bash
cd backend && python -m pytest -q
```
Key suites and what they guard:

| Suite | Guards |
|-------|--------|
| test_auth.py | login/refresh/logout, cookie flags, forgot-password endpoint |
| test_users_hierarchy.py | owner/admin/viewer roles, onboarding |
| test_cameras.py / test_routes_crud.py / test_routes_with_db.py | camera + CRUD + DB routes |
| test_roi.py / test_roi_preprocessor.py | ROI geometry + preprocessing |
| test_events.py / test_event_fsm.py / test_incident_buffer.py / test_incident_context.py | dumping-event state machine |
| test_evidence_modules.py / test_evidence_path_guard.py / test_evidence_worker_process.py | evidence capture + path traversal guard |
| test_alerts_dispatcher.py / test_alerts_telegram.py / test_alert_websocket.py / test_public_warning_templates.py | alert dispatch + Telegram + WS push |
| test_analytics_aggregator.py / test_throttle.py | analytics rollups + rate limiting |
| test_lpr_ocr.py / test_tts.py | LPR OCR + Piper TTS |
| test_stream_recorder.py / test_capture_switch.py | recording + capture source switch |
| test_api_integration.py / test_main_lifespan.py / test_health.py / test_monitoring.py | app bootstrap + health + metrics |
| test_client_log_ingest.py / test_logs_modules.py | client log ingest |

### Frontend (vitest)
```bash
cd frontend && npm test
```
Covers: AuthContext top-role resolution, camera ordering, detection metadata
hook, MediaMTX config hook, ROI editor hook, alert-border logic, utils.

### Lint / typecheck (catch breakage before tester)
```bash
cd frontend && npm run build        # tsc + vite build must succeed
cd backend  && ruff check . 2>/dev/null || true
```

---

## 2. Manual smoke checks (not covered by unit tests)

### Auth / login
- [ ] Open dashboard at `https://192.168.1.8/` → login with owner `admin`.
- [ ] Open dashboard at the ngrok domain → login works there too (same-origin API).
- [ ] Password eye toggle shows/hides password.
- [ ] "Forgot password?" → submit username → generic success message (no
      leak of whether account exists). Email only arrives once Gmail App
      Password is set.
- [ ] Wrong password → "Invalid username or password", no crash.
- [ ] Logout clears session, redirects to login.

### Routing / ingress (recent changes)
- [ ] `/api/*` reachable on both IP and domain (login proves it).
- [ ] WebSocket connects (live events/alerts update) — WS pinned to domain.
- [ ] RTSP/stream URLs resolve to the LAN IP (`192.168.1.8`), not the domain.
- [ ] FastAPI docs: `http://192.168.1.8/docs` loads. **Must 404 via the
      ngrok domain** (`https://<domain>/docs`) — IP-only.
- [ ] MinIO console `/storage/` — known broken behind subpath; confirm or
      skip (needs MINIO_BROWSER_REDIRECT_URL fix).

### Stack health
- [ ] All 12 containers healthy:
      `docker compose -p civilytics-prod --profile prod ps`
- [ ] Pipeline healthy; TRT engine persists across restart (no multi-minute
      rebuild each boot) — requires pipeline_engine volume.

### Core flow (end-to-end)
- [ ] Add a camera → appears in grid, live video plays (WebRTC).
- [ ] Draw an ROI → saves, survives reload.
- [ ] Trigger a dumping event (or replay a clip) → event appears, evidence
      captured, alert fires (Telegram + WS), analytics count increments.
- [ ] Detector classes correct: person / vehicle / bag (3-class model).

### Security sanity (don't hand off if any fail)
- [ ] `WW_AUTH_DEV_MODE=0` in prod `.env` (start.sh enforces).
- [ ] No real secrets committed; `.env` + `keycloak/prod-import/` gitignored.
- [ ] Internal worker→API calls use `X-Service-Token`, reject without it.
- [ ] `/docs` not reachable off-LAN (checked above).

---

## 3. Known gaps to tell the tester
- Workers have no unit tests — exercise them via the end-to-end flow.
- Password-reset + owner-activation emails need a valid Gmail App Password
  (current SMTP credential rejected: `535-5.7.8`).
- MinIO console subpath proxy unresolved.
- MediaMTX WebRTC over `http://192.168.1.8:8889` from an `https` domain page
  is mixed-content blocked — video works when accessed via the IP.
