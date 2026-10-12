# Bring up Civilytics

1. Prerequisites: Docker + Docker Compose v2 plugin, NVIDIA container runtime (for the pipeline/GPU services).
2. From the repo root, run:
   ```
   ./scripts/start.sh dev
   ```
   First run generates secrets into `.env` automatically and prompts for anything it can't generate.
3. Wait for `All containers healthy` in the output.
4. Open:
   - Dashboard: http://localhost:5174
   - API health: http://localhost:8082/api/health
   - Keycloak: http://localhost:8180 (admin/admin by default)
5. First login: follow the "Owner account" prompt that `start.sh` prints after containers go healthy (creates/shows the first owner login).

## Other environments
```
./scripts/start.sh test    # disposable, offset ports, seeded test realm
./scripts/start.sh prod    # real secrets, asks for them on first run
./scripts/start.sh lite    # minimal footprint, no auth, localhost only
```

## Stop
```
./scripts/start.sh dev --down
```

## Useful flags
- `--build` rebuild images first
- `--status` show container health and exit
- `--no-wait` return immediately instead of waiting for health
