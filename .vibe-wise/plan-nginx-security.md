# Plan — nginx ingress hardening

Date: 2026-10-07. Status: **designed, not applied.**
Current config: `nginx/nginx.conf` (169 lines), `nginx/Dockerfile`, `nginx/entrypoint.sh`

---

## 0. First, the premise

> "encrypt the API call endpoints to the actual endpoints of the backend and
> frontend"

Two different things are tangled here, and only one is achievable.

**Achievable — encryption in transit.** TLS already does this. Between the
browser and nginx, the URL path, headers, cookies and body are all inside
the encrypted record. A passive observer on the network sees the SNI
hostname and nothing else. Between nginx and `api:8080` traffic is plaintext
HTTP, but that hop is inside the Docker network and never crosses a wire.

**Not achievable — hiding endpoints from the client.** The browser must know
which URL to call, so every path is visible in devtools, in the JS bundle
and in the network tab. Any scheme that "encrypts" the path has to ship the
key to the browser to work, which means an attacker has it too. Obscured
paths raise the effort of casual probing and nothing more. They are not a
control, and treating them as one is how real controls get skipped.

**What nginx genuinely buys**, and where the work should go:

1. TLS termination with a modern policy (this *is* the encryption)
2. A single ingress, so nothing else is reachable — mostly done
3. **Authenticating the paths that currently have no auth at all**
4. Rate limiting, so brute force and floods die at the edge
5. Per-location body caps, so an upload cap is not an API cap
6. Response headers that constrain what a compromised page can do
7. Not trusting client-supplied forwarding headers
8. Logs that do not themselves leak

Items 3 and 7 are real holes today. Everything else is hardening.

---

## 1. Findings in the current config, ranked

### F1 — `/webrtc/` is unauthenticated. CRITICAL.

```yaml
# mediamtx.yml
authMethod: internal
authInternalUsers:
  - user: any
    pass:            # empty -- anonymous
```

nginx proxies `/webrtc/` straight to `mediamtx:8889` with no auth check, and
MediaMTX accepts anonymous reads. **Anyone who can reach the host can watch
any camera without logging in.** Every control built into the app —
Keycloak, roles, tenancy — is bypassed by one URL.

Worse on the publish side: `stream { listen 554 }` forwards to RTSP, and
`user: any` with an empty password permits **ANNOUNCE**. An attacker can
publish to a path, which in an evidence product means injecting video that
later appears as footage of a violation.

### F2 — forwarding headers are client-controlled. HIGH.

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

`$proxy_add_x_forwarded_for` **appends** to whatever the client sent. A
request with a forged `X-Forwarded-For: 10.0.0.1` produces
`10.0.0.1, <real ip>`, so anything reading the first entry — logs, future
IP rate limiting, audit records — can be poisoned by the caller. At the
outermost proxy the header must be *replaced*, not extended.

### F3 — no rate limiting anywhere. HIGH.

`POST /api/auth/login` can be hit as fast as the network allows. Keycloak's
`bruteForceProtected` eventually locks the account, which is itself the
denial of service: an attacker locks out every known username. Shedding at
the edge is what stops that.

`/ws/client-logs` has app-level token buckets, but nothing stops a client
opening sockets in a loop.

### F4 — one body cap for everything. MEDIUM.

`client_max_body_size 256M` is set at server level, so it applies to
`/api/auth/login` and `/api/system/logs/client` equally. The log ingest caps
at 256 KB *in application code*, but nginx will buffer up to 256 MB before
the app ever sees it. The cap belongs per location.

### F5 — MinIO console is published. MEDIUM.

`/storage/` proxies the MinIO **console**, an admin UI with its own
credentials, onto the public ingress. That is a second authentication
surface with a separate credential lifecycle, reachable by anyone who finds
the path.

### F6 — no Content-Security-Policy. MEDIUM.

`GET /auth/ws-token` deliberately hands a live bearer token to JavaScript —
the one place a token is reachable from the page. That makes XSS
materially more valuable here than in a typical dashboard, and CSP is the
control that limits where a successful injection can send it.

`X-Frame-Options: SAMEORIGIN` should be `DENY`: nothing legitimately frames
this app, and the acknowledge and clear-all controls are clickjacking
targets.

### F7 — TLS policy is incomplete. LOW.

`ssl_protocols TLSv1.2 TLSv1.3` is right, but there is no `ssl_ciphers`,
no OCSP stapling, and session tickets are left on (they weaken forward
secrecy across restarts unless keys are rotated).

### F8 — access log can record sensitive paths. LOW.

`$request` is logged whole, and `/api/evidence/file?path=...` puts an
evidence path into the log line. Tokens are no longer in query strings (they
moved to the WebSocket subprotocol precisely for this reason), but paths
still are.

### F9 — no request correlation. LOW.

No request id is generated, so an nginx log line cannot be tied to the
backend log or to an IncidentBuffer flush covering the same moment.

---

## 2. The plan, in order

### Step 1 — close F1. Nothing else matters as much.

Two parts, and **both** are needed:

**a. Give MediaMTX real credentials.** Replace the anonymous `any` user with
a publish user (used by the pipeline's `rtspclientsink`) and a read user
(used by nginx). Publish and read must be *separate* accounts: the pipeline
needs publish, the viewer path never does.

**b. Authenticate `/webrtc/` at the edge** with `auth_request` against the
API, so a WHEP negotiation requires the same session as the dashboard:

```nginx
location /webrtc/ {
    auth_request /_authz;
    proxy_pass http://webrtc_backend/;
    ...
}
location = /_authz {
    internal;
    proxy_pass http://api_backend/api/auth/me;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
}
```

`/api/auth/me` already returns 200 for a valid cookie and 401 otherwise,
so it works as an authorisation endpoint unchanged.

**Open question (N1):** should `auth_request` also check *which camera* is
being requested, against the caller's tenant? A plain 200/401 proves the
user is signed in, not that this camera is theirs. That needs a dedicated
endpoint taking the path, and it depends on T1.

**RTSP 554:** decide whether it is published at all. It exists so external
tools can pull streams, but with credentials in place it should at minimum
require them, and ideally be dropped from the public listener.

### Step 2 — close F2.

```nginx
proxy_set_header X-Forwarded-For $remote_addr;   # replace, never append
```

At the outermost proxy there is no upstream chain to preserve. If a CDN or
load balancer is ever placed in front, this becomes `realip_module` with an
explicit `set_real_ip_from` allowlist — never blanket trust.

### Step 3 — rate limiting (F3).

```nginx
limit_req_zone $binary_remote_addr zone=login:10m  rate=5r/m;
limit_req_zone $binary_remote_addr zone=api:10m    rate=20r/s;
limit_conn_zone $binary_remote_addr zone=wsconn:10m;

location = /api/auth/login { limit_req zone=login burst=3 nodelay; ... }
location /api/             { limit_req zone=api burst=40 nodelay; ... }
location /ws/              { limit_conn wsconn 10; ... }
```

Login at 5/min with a burst of 3 is generous for a human and useless for a
dictionary. The API limit must sit above the dashboard's own polling —
five timers across seven views, so measure before fixing the number.

**Note:** `$binary_remote_addr` is only meaningful once F2 is fixed.

### Step 4 — per-location body caps (F4).

```nginx
client_max_body_size 1m;                                   # server default
location = /api/auth/login            { client_max_body_size 4k;   }
location = /api/system/logs/client    { client_max_body_size 512k; }  # app caps 256k
location /api/evidence/               { client_max_body_size 256m; }
```

Default deny-ish, widen only where an upload genuinely happens.

### Step 5 — MinIO console (F5).

Remove `/storage/` from the public server, or restrict it:

```nginx
location /storage/ {
    allow 10.0.0.0/8;  allow 192.168.0.0/16;  deny all;
    ...
}
```

Evidence that the *app* serves goes through `/api/evidence/file`, which is
already authenticated. The console is an operator tool, not a user feature.

### Step 6 — headers (F6).

```nginx
add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' wss://$host; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
add_header X-Frame-Options "DENY" always;
add_header Permissions-Policy "geolocation=(self), camera=(), microphone=()" always;
```

`geolocation=(self)` because the camera onboarding GPS button needs it.
`camera=()` and `microphone=()` because this app plays video, never captures
it.

**CSP will break things on first application.** Leaflet tiles, Google Fonts
and any inline style will need explicit entries, and the map picker
currently loads Leaflet from `unpkg` at runtime (a separate defect). Ship it
as `Content-Security-Policy-Report-Only` first, read the violations, then
enforce.

### Step 7 — TLS polish (F7), logs (F8), request id (F9).

```nginx
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305;
ssl_session_tickets off;
ssl_stapling on;              # public CA only; a self-signed cert cannot staple
ssl_stapling_verify on;
```

Log the path without the query string, and add a request id that the
backend echoes:

```nginx
log_format civilytics '$remote_addr - [$time_local] "$request_method $uri" '
                      '$status $body_bytes_sent $request_time "$http_user_agent" '
                      'rid=$request_id';
proxy_set_header X-Request-ID $request_id;
```

`$uri` rather than `$request` drops the query string, so evidence paths stop
landing in the log.

---

## 3. What this does NOT fix

- **Nothing here authenticates the pipeline→MediaMTX publish hop** beyond
  credentials. mTLS between internal services is a separate decision.
- **nginx→api is plaintext.** Acceptable while both are on one Docker
  network on one host. It stops being acceptable the moment they are on
  different hosts.
- **`WW_AUTH_DEV_MODE=1` bypasses everything**, including `auth_request`,
  because `/api/auth/me` would return a mock user. `start.sh` refuses prod
  if it is set; that refusal is now load-bearing for F1 as well.

---

## 4. Decisions needed

| # | Decision |
|---|---|
| N1 | Does `auth_request` check only "signed in", or "signed in AND this camera is in your tenant"? The latter needs a new endpoint and depends on T1. |
| N2 | Is RTSP 554 published at all? If yes, credentialed only. |
| N3 | MinIO console: remove from ingress, or IP-restrict? |
| N4 | CSP: report-only for how long before enforcing? |
| N5 | Separate publish and read credentials for MediaMTX — confirm the pipeline can be given the publish account without a rebuild. |
