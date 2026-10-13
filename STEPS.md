# Bring up Civilytics

`scripts/start.sh <env>` is the single entrypoint for every environment
(`dev` | `test` | `prod` | `lite`). It generates/prompts secrets, validates
the model, brings up the compose stack, waits for health, and (prod) launches
the public tunnel.

## Prerequisites
1. Docker + Docker Compose v2 plugin.
2. NVIDIA container runtime (the pipeline/GPU services need it).
3. **Git LFS** — the detector weights are LFS-tracked. On a fresh clone:
   ```
   git lfs install && git lfs pull
   ```
   `start.sh` validates the detector ONNX at startup and **aborts** if it's
   missing or still an unresolved LFS pointer, so do this first.

## Fresh run — dev
```
git lfs pull            # once, on a fresh clone
./scripts/start.sh dev
```
- First run writes generated secrets into `.env` and prompts for anything it
  can't generate.
- Validates the detector model, then `up -d` and waits for health.
- The **pipeline builds its TensorRT engines on first start** (a few minutes)
  into the `pipeline_engine` volume. This happens **once** — later restarts
  reuse the cached engines and only rebuild if the ONNX changes (tracked by a
  SHA sidecar), so the first boot is slow and the rest are fast.

Open:
- Dashboard: http://localhost:5174
- API health: http://localhost:8082/api/health
- Keycloak: http://localhost:8180 (admin/admin by default)

## Fresh run — prod
```
git lfs pull
./scripts/start.sh prod
```
On first run, prod interactively prompts for (see **[SAMPLE.md](SAMPLE.md)** for
copy-paste values — swap in your own domain):
- Keycloak admin username, `KC_HOSTNAME` (must match the token issuer host),
- Browser-facing URLs: `PUBLIC_API_URL`, `PUBLIC_WS_URL`, `PUBLIC_MEDIAMTX_URL`
  (leave API/WS blank to serve same-origin through nginx — recommended),
- `WW_KEYCLOAK_CLIENT_SECRET`, `WW_INTERNAL_SERVICE_TOKEN`,
- SMTP (`WW_SMTP_HOST/PORT/USER/FROM/PASSWORD`) for owner-activation and
  password-reset emails (blank to skip; wire it later).

prod also, automatically:
- **imports the `wastewatch` Keycloak realm** (confidential client + viewer/
  admin/owner roles + SMTP) from `keycloak/realm-prod.json`, so no manual
  realm setup,
- serves everything behind nginx on 443 (self-signed cert generated if none
  is mounted),
- **launches the ngrok tunnel** (kills any stale one first) if `ngrok` is
  installed — overridable with `NGROK_DOMAIN` / `NGROK_TARGET`.

Open: `https://<KC_HOSTNAME>/` (and the ngrok URL it prints).

### Create the first owner (prod/dev/test)
Owner seeding is **not** automatic — run it explicitly once:
```
./scripts/start.sh prod --seed-owner
```
It prompts for the owner username + email and creates the account with **no
password** — Keycloak emails an activation link (needs working SMTP). The
owner sets their own password from that link (non-repudiation). Resend later:
```
docker compose -p civilytics-prod --profile prod exec api \
  python scripts/seed_owner.py --resend --username <user>
```

## Other environments
```
./scripts/start.sh test    # disposable, offset ports (+1000), seeded test realm, Mailpit catches email
./scripts/start.sh lite    # minimal footprint, no auth, localhost only, no detector
```

## Stop
```
./scripts/start.sh dev --down
```

## Useful flags
- `--build`       rebuild images first
- `--status`      show container health and exit
- `--no-wait`     return immediately instead of waiting for health
- `--seed-owner`  create the first owner account (prompts; needs SMTP for the email)
- `--e2e`         after health, run the `tests/e2e` suite against the started stack
- `--turn`        (prod) also start the coturn TURN relay for off-LAN WebRTC viewers
- `--rotate`      (prod) regenerate generated passwords in `.env`

## Notes
- Always manage the prod stack with the project name:
  `docker compose -p civilytics-prod --profile prod ...` (mixing the default
  project name desyncs state).
- `WW_AUTH_DEV_MODE` must be `0` in prod (start.sh enforces it).
- The "Waiting up to 900s for health" line is a safety ceiling, not the
  expected boot time — most of a slow first boot is the one-time engine build.
