# UI action → event trees

Date: 2026-10-06. Every path below was read from source, not inferred.
`file:line` references are clickable.

Legend
```
  ↓   synchronous call / await
  ⇢   fire-and-forget (nothing waits for the result)
  ⟲   polling timer
  ⚡  push (WebSocket / Redis pub-sub)
  ✗   NOT WIRED — the step does not exist yet
```

---

## 0. Shell — what wraps every action

```
browser loads /
└── App.tsx
    ├── installApiClient()                       lib/api-client.ts
    │   └── patches window.fetch globally
    │       ├── adds credentials: "include"      (cookies ride every /api call)
    │       └── on 401 (non-auth endpoint)
    │           ├── ↓ POST /api/auth/refresh
    │           ├── retry the original request once
    │           └── still 401 → dispatch "ww:auth-failed"
    ├── AuthProvider                             context/AuthContext.tsx
    │   └── ↓ GET /api/auth/me                   (restore session on load)
    │       ├── 200 → user set, render Index
    │       └── 401 → render Login
    └── RequireAuth
        ├── loading  → spinner
        ├── no user  → pages/Login.tsx
        └── user     → pages/Index.tsx
            └── MonitoringProvider → MainLayout → view switched by currentView
```

One route only. Every view below is `useState`, not a URL.

---

## 1. Login

```
user submits the login form                      pages/Login.tsx
└── AuthContext.login(username, password)
    └── ↓ POST /api/auth/login                   api/routes/auth.py:login
        └── _exchange({grant_type: "password"})
            └── ↓ POST {keycloak}/realms/wastewatch/protocol/openid-connect/token
                ├── non-200
                │   ├── log STATUS ONLY (body echoes the username)
                │   └── 401 "Invalid username or password"   ← identical for
                │                                              unknown user and
                │                                              wrong password
                └── 200
                    └── validate_token(access)   auth/keycloak.py  (JWKS, RS256)
                        ├── invalid → 502, fail closed
                        └── valid
                            ├── Set-Cookie ww_access   httpOnly, 15 min
                            ├── Set-Cookie ww_refresh  httpOnly, 8 h
                            └── return {username, name, email, roles}
                                └── AuthContext sets user → Index renders
```

Tokens never reach JavaScript here. The client secret never reaches the browser.

---

## 2. Live Monitoring

### 2.1 Opening the view

```
sidebar click "Live Monitoring"                  components/layout/Sidebar.tsx
└── setCurrentView('monitoring')
    └── MonitoringView mounts
        ├── useCameras            ↓ GET /api/cameras        ⟲ 15 s
        ├── useAlerts             ⚡ WS /ws/alerts
        ├── MonitoringContext     ⟲ GET /api/alerts?limit=50  every 5 s
        │                            ↑ SECOND transport for the same data
        ├── useDetectionMetadata  ⚡ WS /ws/metadata/{camera_id}
        ├── useROIEditor          ↓ GET /api/cameras/{id}/roi  ⟲ 30 s (idle only)
        └── CameraGrid (default 3x3)
            └── per camera: WebRTCPlayer → MediaMTX WHEP :8889
```

### 2.2 Watching a stream

```
CameraFeed renders
└── WebRTCPlayer
    └── ↓ POST {mediamtx}/{camera_name}/whep      (SDP offer)
        └── MediaMTX relays the RTSP the pipeline published
            pipeline → rtsp://mediamtx:8554/{cam} → WHEP → <video>
```

### 2.3 An alert arrives (push)

```
pipeline FSM reaches VIOLATION           pipeline/app/event_fsm.py
└── ⇢ Kafka wastewatch.alerts
    └── AlertConsumer                    app/services/alert_consumer.py
        ├── ↓ INSERT alerts row
        └── ⚡ ws/alerts.py broadcast
            └── useAlerts receives
                └── MonitoringContext (poll path, :115-130)
                    ├── skip if id already known
                    ├── skip unless severity ∈ {critical, warning}
                    ├── rate limit: 1 per 3 s, max 3 per poll
                    ├── fireNotification()  browser push + audio
                    │   └── ↓ GET {ttsAudioUrl}
                    └── setIsAlertPanelOpen(true)
                        └── MainLayout renders AlertPanel
                            └── + a full-screen backdrop  MainLayout.tsx:28
                                ↑ blocks the entire app until dismissed
```

The auto-open is driven by the **poll**, not the WebSocket.

### 2.4 Acknowledging an alert

```
click "Acknowledge"                      components/alerts/AlertPanel.tsx
└── MonitoringContext.acknowledgeAlert(id)        :191
    └── ↓ PATCH /api/alerts/{alert_id}            api/routes/alerts.py
        ├── require_viewer                        ← a viewer may acknowledge
        ├── ↓ UPDATE alerts SET status
        └── 200
            └── local state updated
                ✗ no broadcast — other operators' dashboards still show it
                  active until their next 5 s poll
```

### 2.5 Jumping from an alert

```
click the alert body                     AlertPanel.tsx
├── setSelectedCamera(id) + setGridLayout(...) + setCurrentView('monitoring')
└── setIsAlertPanelOpen(false)

click "View history"                     AlertPanel.tsx:112
└── setCurrentView('history') + panel closed
    ✗ no filter is carried across — HistoryView opens unfiltered
```

---

## 3. ROI editing

```
enter edit mode (CameraFeed, or CameraSettingsDialog.tsx:103)
└── useROIEditor
    ├── polling suspended while isEditing      useROIEditor.ts:103
    ├── user drags polygon vertices on the canvas overlay
    └── click Save
        └── ↓ PUT /api/cameras/{camera_id}/roi      api/routes/roi.py
            ├── require_admin
            ├── ↓ UPDATE cameras.roi_polygons
            └── ⇢ PipelineNotifier._publish({action: "update_roi"})
                └── Redis PUBLISH ww:pipeline:commands
                    └── pipeline CommandReceiver      pipeline/app/command_receiver.py
                        └── subscriber thread applies new polygon to live state
                            ✗ no acknowledgement path back
                              the UI says "saved" on the DB write, not on the
                              pipeline actually adopting it
```

---

## 4. Camera management

### 4.1 Add a camera

```
click "Add camera"                       components/views/CameraManagementView.tsx
└── AddCameraDialog                      components/camera/AddCameraDialog.tsx
    ├── MapPicker (click to set lat/lng)             :21
    │   └── injects leaflet from unpkg CDN at runtime
    │       └── if unreachable: `if (!L) return` → blank div, SILENT
    ├── lat/lng number inputs                        :237-247
    │   ├── ✗ no range check  (lat 200 accepted)
    │   ├── ✗ no swap check   (77.59, 12.97 → Arabian Sea)
    │   └── ✗ no both-or-neither check
    └── submit → useCameras.addCamera()
        └── ↓ POST /api/cameras                      api/routes/cameras.py:38
            ├── require_admin
            ├── ↓ INSERT cameras
            └── if stream_url:
                └── ⇢ _notifier._publish({action: "add_camera"})
                    └── Redis PUBLISH ww:pipeline:commands
                        └── pipeline adds the RTSP source live
```

### 4.2 Edit / delete

```
edit   → ↓ PATCH  /api/cameras/{id}   require_admin
         └── ⇢ pipeline command only if stream_url or zone changed  (:75)
delete → ↓ DELETE /api/cameras/{id}
```

### 4.3 Camera goes offline

```
pipeline loses the RTSP source
└── ⇢ Kafka camera status
    └── CameraStatusConsumer
        ├── ↓ UPDATE cameras.status = 'offline'
        └── ✗ NO BROADCAST
            └── dashboard learns only on the next 15 s useCameras poll,
                and nothing alerts anyone
```

---

## 5. Event History

```
sidebar click "Event History"
└── HistoryView mounts
    ├── ↓ GET /api/alerts?{filters}
    └── useViolationEvents
        ├── ↓ GET /api/alerts?limit=100              ⟲ 30 s
        ├── ↓ GET /api/evidence/files?limit=200
        └── per match: ↓ GET /api/evidence/file?path=...  (keyframe + video)

resolve an event
└── ↓ PATCH /api/events/{event_id}     require_viewer   useViolationEvents.ts:119

export
└── ↓ GET /api/events/export/csv?{params}  → browser download
```

---

## 6. Analytics

```
sidebar click "Analytics"
└── AnalyticsView → useAnalytics
    └── ↓ GET /api/analytics/{summary|hourly|daily|by-zone|by-camera}
        └── charts render
            ✗ no outbound navigation from any chart —
              clicking a bar leads nowhere
```

Same for **Timeline Playback** and **Camera Map**: `MapView` → `CameraMapView`
has exactly one jump, `setCurrentView('monitoring')`.

---

## 7. Settings

```
sidebar click "Settings"
└── SettingsView
    ├── HealthPanel
    │   ├── ↓ GET /api/health/services     require_admin
    │   └── ↓ GET /api/health/models       require_admin
    └── MediaMTXSettings
```

A viewer sees this entry and receives 403s — no role gate on the sidebar.

---

## 8. User onboarding — backend built, UI not

```
✗ UserManagementView does not exist
   currentView has no 'users' member   context/MonitoringContext.tsx:19,177

owner onboards an admin
└── ↓ POST /api/users {role: "admin", customer: "..."}
    ├── require_admin
    ├── _grantable(actor) → owner may grant {admin, viewer}
    │   └── role not in set → 403 BEFORE any Keycloak call
    ├── customer name required (422 if blank)
    ├── tenant = uuid4()                  ← a new admin opens a new customer
    ├── ↓ POST {kc}/admin/realms/wastewatch/users
    │   ├── via client_credentials service-account token
    │   ├── attributes: ww_tenant, ww_customer, ww_invited_by, ww_phone, ww_zone
    │   └── NO credentials key — no password exists
    ├── ↓ POST .../users/{id}/role-mappings/realm   (admin)
    └── ↓ PUT  .../users/{id}/execute-actions-email
        ├── [UPDATE_PASSWORD, VERIFY_EMAIL]
        ├── sent by KEYCLOAK from its REALM smtp config
        │   └── dev/test → mailpit → web inbox :8025 / :9025
        │       prod → real relay, configured in the console (manual step)
        └── on failure: still 201, account exists, logged for a resend

admin onboards a viewer
└── ↓ POST /api/users {role: "viewer"}
    ├── _grantable(actor) → admin may grant {viewer} only
    │   └── "admin" or "owner" → 403, nothing created
    └── tenant = _tenant_of_actor(claims)
        ├── from the ww_tenant token claim (realm protocol mapper)
        ├── fallback: ↓ GET .../users/{own sub}  + WARNING
        └── NEVER from the request body
            ↑ otherwise an admin could inject a viewer into another customer

manage an existing user
└── _assert_may_manage(actor, target)
    ├── target == self              → 403  (last owner cannot lock itself out)
    ├── owner                       → allowed
    └── admin
        ├── target.ww_tenant ≠ mine → 404  (not 403: no cross-tenant probing)
        └── target direct role ⊄ {viewer} → 403
    ├── PATCH  /api/users/{id}           name, email, phone, zone
    │   └── role is absent from UpdateUserRequest  ← closes escalation-by-edit
    ├── PATCH  /api/users/{id}/enabled
    ├── POST   /api/users/{id}/setup-email   (also the password-reset path)
    └── DELETE /api/users/{id}
```

---

## 9. Browser console capture — backend built, shim not

```
admin turns capture ON
└── ↓ POST /api/system/logs/client/capture {enabled: true, ttl_s}
    ├── require_admin
    └── Redis SET ww:logs:client_capture = who, EX = ttl
        ├── floor 60 s, cap 24 h   ← a forgotten "on" expires by itself
        └── logged at WARNING on both edges

✗ frontend/src/lib/client-logger.ts does not exist
   planned: console patch → redact → gzip → batch

a browser ships a batch
└── ⚡ WS /ws/client-logs                     app/ws/client_logs.py
    ├── authenticate_ws(role="viewer")        subprotocol ["bearer", token]
    ├── server sends {type: "capture", enabled} on connect
    │   └── shim stays dormant until enabled
    ├── binary frame only (a text frame would bypass the size cap)
    ├── token bucket 20 envelopes / 10 s, 30 strikes → close
    ├── capture off mid-session → discard + tell the client to go dormant
    │   ↑ enforced server side: a stale tab cannot keep writing
    └── ingest_blob()                         app/logs/client_ingest.py
        ├── compressed > 256 KB        → reject before decompressing
        ├── bounded decompressobj      → zip bomb rejected, never allocated
        ├── records > 500              → reject
        ├── redact() per message, stack and url
        ├── identity from TOKEN not envelope   (username, roles, tenant)
        ├── absurd client clock → replaced with server time
        ├── dropped count → one extra warn row  (gap stays visible)
        └── ↓ INSERT log_entries (service='browser')

WS down
└── ↓ POST /api/system/logs/client            same gzip body
    ├── get_current_user                      any role, auth still required
    ├── capture off → {stored: 0}, no write
    └── shares client_ingest → caps cannot drift between transports
```

---

## 10. Incident flush

```
something triggers an incident
└── flush_with_snapshot()                     app/logs/incident_context.py
    ├── snapshot taken FIRST (incident time, not post-recovery)
    │   ├── ↓ GET {mediamtx}/v3/paths/list    3 s timeout
    │   │   ├── per path: ready, input type, tracks, bytes, reader count
    │   │   ├── ready_without_readers[]        ← publishing, nobody watching
    │   │   └── unreachable → recorded as the finding, not raised
    │   └── Redis SCAN (9 whitelisted globs, 400 key cap)
    │       ├── ww:pipeline:* ww:health:* ww:camera:*:fps ...
    │       ├── SCAN not KEYS  (KEYS blocks the server)
    │       ├── values redacted (the file leaves the host)
    │       └── unreachable → recorded, not raised
    └── IncidentBuffer.flush_incident()        app/logs/incident_buffer.py
        ├── DEBUG ring, bounded by age AND count
        ├── redacted at CAPTURE, not flush
        ├── write-then-rename
        ├── prune oldest beyond max_incidents_on_disk
        └── ring NOT cleared
```

---

## 11. Backend endpoints with no UI at all

```
/api/users/*                  built this session, no view
/api/system/logs/*            built this session, no view
/api/devices/speakers         no view
/api/devices/contacts         no view
/api/devices/operators        no view
/api/reports/recipients       no view
/api/reports/history          no view
/api/lpr/plates               no view
/api/config/{key}             no view
/api/system/health/{gpu,containers,kafka,models,storage}   no view
/api/health/{full,cameras,speakers,system}                 no view
/api/events/tracks/active     no view
```

Only `/api/health/services` and `/api/health/models` of the health family are
consumed, by `HealthPanel`.

---

## 12. Cross-cutting gaps visible in the trees

1. **Alerts have two transports.** `⚡ WS /ws/alerts` and `⟲ 5 s poll`, both
   live, and the poll is what drives the notification and the panel auto-open.
2. **Five uncoordinated timers**, none view-aware: alerts 5 s, cameras 15 s,
   ROI 30 s, events 30 s, camera health. Settings polls violation events.
3. **Acknowledge does not broadcast.** Two operators both work the same alert.
4. **Camera offline does not broadcast.** `CameraStatusConsumer` writes the DB
   and tells nobody.
5. **ROI save is acknowledged on the DB write**, not on the pipeline adopting
   the polygon. A dropped Redis publish looks like success.
6. **Three dead-end views** — Analytics, History, Timeline have zero outbound
   navigation. Five cross-view jumps exist in the whole app.
7. **No URLs.** One route, so no action in any tree above can be linked,
   bookmarked, or shared.
8. **Zero responsive breakpoints** in all seven views, the layout, the sidebar,
   the alert panel and login.
