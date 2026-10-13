# Civilytics — Server-side Log Commands

```bash
cd /mnt/1tb-backup/Projects/Satyaki/LSG-Wastewatch
DC="docker compose -p civilytics-prod --profile prod"
```

## 1. Container stdout (Python app logs — error/warn/info/debug)
```bash
$DC logs -f api                                   # live tail, api
$DC logs --tail 200 api                           # last 200 lines
$DC logs --since 10m api backend-workers pipeline # last 10 min, several services
$DC logs api | grep -iE 'ERROR|WARNING'           # filter levels
```

## 2. Captured logs in the DB (log_entries — includes browser/UI logs when capture is on)

### Via API (login once, reuse cookie)
```bash
curl -ks -X POST https://192.168.1.8/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"Civilytics5829!"}' -c /tmp/cj.txt -o /dev/null

# browser (UI) logs
curl -ks -b /tmp/cj.txt "https://192.168.1.8/api/system/logs?service=browser&limit=50" | jq
# filter by level / search / session
curl -ks -b /tmp/cj.txt "https://192.168.1.8/api/system/logs?level=error&q=camera&limit=50" | jq
curl -ks -b /tmp/cj.txt "https://192.168.1.8/api/system/logs?service=browser&session_id=<id>" | jq
```

### Via psql (direct)
```bash
$DC exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT timestamp, service, level, left(message,120) FROM log_entries ORDER BY timestamp DESC LIMIT 50;"'
```

## 3. Runtime log level (turn on DEBUG, then back)
```bash
curl -ks -b /tmp/cj.txt https://192.168.1.8/api/system/log-level | jq
curl -ks -b /tmp/cj.txt -X PUT https://192.168.1.8/api/system/log-level \
  -H 'Content-Type: application/json' -d '{"level":"DEBUG","logger":"app"}' | jq
# reset
curl -ks -b /tmp/cj.txt -X PUT https://192.168.1.8/api/system/log-level \
  -H 'Content-Type: application/json' -d '{"level":"INFO","logger":"app"}' | jq
```

## 4. Enable browser/UI log capture (admin; off by default, auto-expires)
```bash
curl -ks -b /tmp/cj.txt -X POST https://192.168.1.8/api/system/logs/client/capture \
  -H 'Content-Type: application/json' -d '{"enabled":true,"ttl_s":1800}'
# disable
curl -ks -b /tmp/cj.txt -X POST https://192.168.1.8/api/system/logs/client/capture \
  -H 'Content-Type: application/json' -d '{"enabled":false}'
```

Notes:
- Browser logs populate `service=browser` only AFTER capture is enabled.
- UI event summaries are logged via `logUiEvent(...)` at level `info`, meta.kind=`event`.
- Logs are tenant-scoped: owner (no ww_tenant) sees all; admin/viewer see their tenant.
