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

MinIO root access key (evidence + backup object store).
  MINIO_ROOT_USER: admin

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

`WW_SMTP_PORT` defaults to 587 (STARTTLS) and is not prompted.

## What is no longer asked

These are fixed or derived, so the script sets them and moves on:

| Value | Where it comes from |
| --- | --- |
| `NGROK_DOMAIN` | hardcoded: `related-elsewhere-undertook.ngrok-free.dev` |
| `KC_HOSTNAME` | set to `NGROK_DOMAIN` automatically (they must match, so there was nothing to decide) |
| `PUBLIC_API_URL`, `PUBLIC_WS_URL`, `PUBLIC_MEDIAMTX_URL` | blank — same-origin through nginx |
| `NGROK_AUTHTOKEN` | `.env`, or the process environment — **never prompted**, because it is a credential and this file is in git |

The authtoken goes in once:

```
echo 'NGROK_AUTHTOKEN=<token>' >> .env       # or edit the existing empty line
# or, for a one-off run:
NGROK_AUTHTOKEN=<token> ./scripts/start.sh prod
```

It must belong to the ngrok account that **reserved** that domain. A token
from any other account fails with `ERR_NGROK_320` and the tunnel stays down
(the stack itself still comes up on the LAN IP). If it is empty, start.sh
stops with the exact command to fix it rather than launching a tunnel that
cannot authenticate.

With `--turn` you also get: `TURN_HOST`, `TURN_EXTERNAL_IP`, `TURN_USER`,
`TURN_PASSWORD`. `TURN_HOST` must be a real DNS name pointing at this server,
never the ngrok domain (ngrok can't carry TURN's UDP).

## Why the browser-facing URLs are blank

Blank is not "unconfigured" — it is the correct value. The frontend
resolves the API, WebSocket and WebRTC **same-origin through nginx**
(`frontend/src/lib/api-base.ts`), so one build works at every hostname it's
reached by: the LAN IP *and* the ngrok domain, with no rebuild.

Setting them in `.env` pins the build to that one host and stops it working
everywhere else. If you do override them:

**`PUBLIC_API_URL` must be the ORIGIN only — never append `/api`.** The
client appends `/api` itself, so `https://host/api` becomes
`https://host/api/api/auth/login` → 404 and login breaks. A bare host gets
the scheme added, and a trailing `/api` is stripped, so both shapes land
correct.

`PUBLIC_WS_URL` is the opposite — it *does* carry the `/ws` path. A bare host
is expanded to `wss://host/ws`.

## What the validators reject

`start.sh` re-prompts instead of failing later:
- `KEYCLOAK_ADMIN`, `MINIO_ROOT_USER` — required, any non-empty value.
- `WW_KEYCLOAK_CLIENT_SECRET` — required, hidden.
- `WW_SMTP_FROM` — required once `WW_SMTP_HOST` is non-blank.

Watch what you paste into a hidden prompt: nothing echoes, so pasting a
multi-line blob writes all of it into that one variable. That is how `.env`
ended up with `NGROK_AUTHTOKEN=NGROK_DOMAIN (optional, Enter to skip): ...`
— a 292-character value and no usable token.

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

## If your checkout is older

An older checkout asks one extra prompt, because the ngrok domain used to be
operator input rather than fixed in the script:

```
Optional stable ngrok public domain, e.g. my-site.ngrok-free.app.
  NGROK_DOMAIN (optional, Enter to skip): related-elsewhere-undertook.ngrok-free.dev

  Set KC_HOSTNAME to the public ngrok domain
  (related-elsewhere-undertook.ngrok-free.dev)? [Y/n]: y
```

Answer it with the domain (not a bare word — `admin` is rejected), then `y`.

Such a checkout is also missing two fixes that break the deployment in ways
the prompts won't warn you about:

| Missing | Symptom |
| --- | --- |
| the nginx same-server fix | ngrok terminates TLS and forwards plain HTTP to `:80`; nginx unconditionally 301s back to HTTPS → **infinite redirect loop**, public URL unusable |
| the `PUBLIC_API_URL` origin fix | a non-blank answer keeps its `/api`, so the client's own `/api` double-prefixes → `/api/api/auth/login` **404 on every login** |

Leaving the browser-facing URLs blank avoids the 404, but the redirect loop
needs the code. Pull, then rebuild the two affected images:

```
git pull origin dev
docker compose -p civilytics-prod --profile prod build nginx frontend-prod
docker compose -p civilytics-prod --profile prod up -d nginx frontend-prod
```

`nginx.conf` is baked into the image at build time (not bind-mounted), so a
plain restart does **not** pick up the fix — it must be rebuilt.

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
