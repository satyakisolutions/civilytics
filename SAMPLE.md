# Sample answers for `./scripts/start.sh prod`

A snapshot of every prompt a fresh `prod` run asks, in order, with an example
answer. Copy the shapes, swap in your own domain. Nothing here is a real
secret — anything shown as `<...>` you paste yourself.

Values already present in `.env` are **not** re-prompted, so a second run is
much shorter than the first.

## The prompts, in order

```
Keycloak admin username.
  KEYCLOAK_ADMIN: admin

ngrok authtoken for the configured reserved domain.          (hidden input)
  NGROK_AUTHTOKEN: <paste from dashboard.ngrok.com/get-started/your-authtoken>

  Set KC_HOSTNAME to the public ngrok domain
  (related-elsewhere-undertook.ngrok-free.dev)? [Y/n]: y

MinIO root access key (evidence + backup object store).
  MINIO_ROOT_USER: admin

Browser-facing API URL — blank for same-origin via nginx (recommended).
  PUBLIC_API_URL: <Enter — leave blank>

Browser-facing WebSocket URL — blank for same-origin wss via nginx.
  PUBLIC_WS_URL: <Enter — leave blank>

Browser-facing MediaMTX WebRTC URL (e.g. http://<LAN-IP>:8889)
  — blank to skip until cameras need live video.
  PUBLIC_MEDIAMTX_URL: <Enter — leave blank>

Keycloak confidential-client secret for the branded login.   (hidden input)
  WW_KEYCLOAK_CLIENT_SECRET: <Keycloak > Clients > wastewatch-api > Credentials>

Shared secret for worker-to-api calls (leave empty to auto-generate).
  WW_INTERNAL_SERVICE_TOKEN: <Enter — auto-generates>

SMTP server host, e.g. smtp.gmail.com (blank to skip email). (hidden input)
  WW_SMTP_HOST: mail.satyaki.co.in

SMTP username (often the full email address).                (hidden input)
  WW_SMTP_USER: neeraj.sj@satyaki.co.in

From address for outgoing mail, e.g. no-reply@yourdomain.
  WW_SMTP_FROM: neeraj.sj@satyaki.co.in

SMTP password / app-password.                                (hidden input)
  WW_SMTP_PASSWORD: <the mailbox password>

Administrator notification settings ...
SMTP password for report email.                              (hidden input)
  WW_SMTP_PASSWORD: <Enter — already set above, so this is skipped>
```

`WW_SMTP_PORT` defaults to 587 (STARTTLS) and is not prompted. The ngrok
**domain** is fixed in the script (`related-elsewhere-undertook.ngrok-free.dev`)
— only the authtoken is asked for, and it must belong to the account that
reserved that domain, or the tunnel fails with `ERR_NGROK_320`.

With `--turn` you also get: `TURN_HOST`, `TURN_EXTERNAL_IP`, `TURN_USER`,
`TURN_PASSWORD`. `TURN_HOST` must be a real DNS name pointing at this server,
never the ngrok domain (ngrok can't carry TURN's UDP).

## Leave the browser-facing URLs blank

Blank is not "unconfigured" — it is the recommended answer. The frontend
resolves the API, WebSocket and WebRTC **same-origin through nginx**
(`frontend/src/lib/api-base.ts`), so one build works at every hostname it's
reached by: the LAN IP *and* the ngrok domain, with no rebuild.

If you do fill them in, the build is pinned to that one host and stops
working everywhere else.

**`PUBLIC_API_URL` must be the ORIGIN only — never append `/api`.** The
client appends `/api` itself, so `https://host/api` becomes
`https://host/api/api/auth/login` → 404 and login breaks. Answer with a bare
host (`related-elsewhere-undertook.ngrok-free.dev`) and the script adds the
scheme; it also strips a trailing `/api` if you type one anyway.

`PUBLIC_WS_URL` is the opposite — it *does* carry the `/ws` path. A bare host
is expanded to `wss://host/ws`.

## What the validators reject

`start.sh` re-prompts instead of failing later:
- `KEYCLOAK_ADMIN`, `MINIO_ROOT_USER` — required, any non-empty value.
- `NGROK_AUTHTOKEN`, `WW_KEYCLOAK_CLIENT_SECRET` — required, hidden.
- `KC_HOSTNAME` — a real dotted hostname. Not a bare word (`admin`,
  `civilytics`), not a URL (`https://...`).
- `PUBLIC_API_URL` — blank, a bare hostname, or an `https://` URL.
- `PUBLIC_WS_URL` — blank, a bare hostname, or a `wss://` URL ending `/ws`
  (`ws://` is rejected; watch for a stray `/` after `wss:`).

## After the prompts

The run then: writes `keycloak/prod-import/civilytics-realm.json` (client
secret + SMTP substituted in), generates a self-signed TLS pair if
`nginx/certs/` has none, validates the detector ONNX, brings up the stack,
waits for health, and runs `alembic upgrade head`.

First owner account (not automatic — run once):
```
./scripts/start.sh prod --seed-owner
```
It creates the owner, sets a **random temporary password**, and emails the
URL + username + that password as plain text via `WW_SMTP_*`. If the mail
fails it prints the password to the terminal instead, so you are never
locked out. Resend (issues a fresh password):
```
docker compose -p civilytics-prod --profile prod exec api \
  python scripts/seed_owner.py --resend --username admin
```

## Gotchas

- **Invalid cert, not missing cert.** start.sh auto-generates certs only when
  `nginx/certs/` is *empty*. A corrupt `privkey.pem` aborts with
  `Invalid TLS private key` — delete both `.pem` files and rerun.
- **The ngrok domain is reserved to one account.** The authtoken must be that
  account's, otherwise `ERR_NGROK_320: This domain is reserved for another
  account` and the tunnel never comes up (the app itself still runs on the
  LAN IP).
- `KC_HOSTNAME` is baked into every token's issuer. Change it and existing
  sessions stop validating until the stack restarts.
