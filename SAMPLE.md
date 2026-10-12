# Sample answers for `./scripts/start.sh prod --seed-owner`

Copy-paste these shapes, swapping in your real domain. All four
(`KC_HOSTNAME`, `PUBLIC_API_URL`, `PUBLIC_WS_URL`, `PUBLIC_MEDIAMTX_URL`)
must point at the **same** domain — nginx splits by path
(`/api/`, `/ws/`, `/webrtc/`, see `nginx/nginx.conf`), not by subdomain.

Say your tunnel/domain is `related-elsewhere-undertook.ngrok-free.dev`:

```
Keycloak admin username.
KEYCLOAK_ADMIN: admin

Public hostname Keycloak is served on, e.g. auth.civilytics.in
KC_HOSTNAME: related-elsewhere-undertook.ngrok-free.dev

MinIO root access key (evidence + backup object store).
MINIO_ROOT_USER: admin

Browser-facing API URL, e.g. https://civilytics.example/api
PUBLIC_API_URL: https://related-elsewhere-undertook.ngrok-free.dev/api

Browser-facing WebSocket URL, e.g. wss://civilytics.example/ws
PUBLIC_WS_URL: wss://related-elsewhere-undertook.ngrok-free.dev/ws

Browser-facing MediaMTX WebRTC URL, e.g. https://civilytics.example/webrtc
PUBLIC_MEDIAMTX_URL: https://related-elsewhere-undertook.ngrok-free.dev/webrtc

Keycloak confidential-client secret for the branded login.
WW_KEYCLOAK_CLIENT_SECRET: <paste from Keycloak: Clients > wastewatch-api > Credentials>

Shared secret for worker-to-api calls (leave empty to auto-generate).
WW_INTERNAL_SERVICE_TOKEN: <Enter to auto-generate>

SMTP password for report email.
WW_SMTP_PASSWORD: <Enter to skip, or your real SMTP password>

Telegram bot token for alert messages.
WW_TELEGRAM_BOT_TOKEN: <Enter to skip>
```

## Common mistakes this catches now

`start.sh` validates these on entry and re-prompts if wrong:
- `KC_HOSTNAME` must be a real dotted hostname — not a bare word like `civlytics`, not `https://...`
- `PUBLIC_API_URL` must end in `/api`
- `PUBLIC_WS_URL` must be `wss://` (not `ws://`) and end in `/ws` — watch for an accidental extra `/` after `wss:`
- `PUBLIC_MEDIAMTX_URL` must end in `/webrtc`

## Caveat: free ngrok domains

A free ngrok tunnel's hostname changes every time it restarts. Since
`KC_HOSTNAME` is baked into every token's issuer, a restart silently
breaks login until `.env` is updated to the new hostname and the stack
is restarted. Fine for testing; get a reserved domain (ngrok paid, or a
real DNS name) before treating this as a stable deployment.
