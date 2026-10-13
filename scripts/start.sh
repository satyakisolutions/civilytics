#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Civilytics / WasteWatch — single script, single compose file.
#
#   ./scripts/start.sh dev|test|prod|lite [options]
#
#   --build        rebuild images before starting
#   --pull         pull newer base images first
#   --turn         prod only: also start coturn (TURN relay)
#   --no-wait      return as soon as compose returns, don't wait for health
#   --timeout N    seconds to wait for health (default 900)
#   --rotate       prod only: regenerate generated passwords in .env
#   --seed-owner   create the first owner account (no-op if one exists) --
#                  NOT run by default, see scripts/seed_owner.py
#   --down         stop the stack (test also removes its volumes)
#   --status       show health of every container and exit
#
# This script now does everything the old scripts/bootstrap.sh did
# (secret generation/prompting) and everything the old scripts/start.sh
# did (sequencing, health wait), against ONE docker-compose.yml with one
# profile per environment, instead of docker-compose.{dev,test,prod,lite}.yml.
#
# Previous 5 compose files + bootstrap.sh kept at docker/legacy/.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE=".env"
ENVIRONMENT=""
BUILD="" PULL="" EXTRA_PROFILES=() WAIT=1 TIMEOUT=900 ACTION="up" ROTATE=0 SEED_OWNER=0

c_red() { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
c_dim() { printf '\033[2m%s\033[0m\n' "$*"; }
die()   { c_red "ERROR: $*" >&2; exit 1; }

usage() { sed -n '3,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

[ $# -ge 1 ] || usage
case "$1" in
    dev|test|prod|lite) ENVIRONMENT="$1"; shift ;;
    *) usage ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        --build)    BUILD="--build"; shift ;;
        --pull)     PULL=1; shift ;;
        --turn)     EXTRA_PROFILES+=(--profile turn); shift ;;
        --no-wait)  WAIT=0; shift ;;
        --timeout)  TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
        --rotate)   ROTATE=1; shift ;;
        --seed-owner) SEED_OWNER=1; shift ;;
        --down)     ACTION="down"; shift ;;
        --status)   ACTION="status"; shift ;;
        *) die "unknown option: $1" ;;
    esac
done

if [ "${#EXTRA_PROFILES[@]}" -gt 0 ] && [ "$ENVIRONMENT" != "prod" ]; then
    die "--turn is prod only (coturn is defined under the turn profile)"
fi

# Explicit -p keeps each environment's Docker volumes under the exact
# names the old per-environment compose files used (civilytics-dev,
# civilytics-test, civilytics-prod) -- the merged file has no top-level
# `name:` for this reason (see its header). Without this, Compose would
# fall back to a single shared name and existing dev/test/prod data
# would look like it vanished on the next start.
DC=(docker compose -f docker-compose.yml -p "civilytics-${ENVIRONMENT}" --profile "$ENVIRONMENT" "${EXTRA_PROFILES[@]+"${EXTRA_PROFILES[@]}"}")

# ── down / status — no secret handling needed ──────────────────────────
if [ "$ACTION" = "down" ]; then
    if [ "$ENVIRONMENT" = "test" ]; then
        c_ylw "Stopping test stack and REMOVING its volumes (test state is disposable)"
        "${DC[@]}" down -v
    else
        "${DC[@]}" down
    fi
    exit 0
fi

if [ "$ACTION" = "status" ]; then
    "${DC[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
    exit 0
fi

# ── Preflight ────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found"

if ! docker info 2>/dev/null | grep -qi nvidia; then
    c_ylw "WARNING: the NVIDIA container runtime was not detected."
    c_ylw "         The pipeline service will fail to start without it."
fi

# ── Host directories compose expects to already exist ──────────────────
mkdir -p nginx/certs keycloak/empty docker/legacy
[ -f keycloak/empty/.gitkeep ] || touch keycloak/empty/.gitkeep

# ── .env helpers (folded in from the old bootstrap.sh) ──────────────────
[ -f "$ENV_FILE" ] || { touch "$ENV_FILE"; chmod 600 "$ENV_FILE"; }

env_get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1; }

env_set() {
    local key="$1" val="$2" tmp
    tmp="$(mktemp)"
    grep -v "^${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
}

# Set only if absent — never overwrite an operator's existing value.
env_default() {
    local key="$1" val="$2"
    # Must not return nonzero when the var already exists (the normal
    # case on any re-run) -- under `set -e`, a bare `cond && cmd` whose
    # cond is false makes the whole statement (and this function) exit
    # nonzero, silently killing the script. Only ever caught on a
    # brand-new .env, where every call takes the true branch.
    if [ -z "$(env_get "$key")" ]; then
        env_set "$key" "$val"
    fi
}

gen_password() {
    # 32 alphanumeric chars from a CSPRNG — no punctuation, because these
    # get interpolated into a SQLAlchemy URL and passed to redis-server
    # --requirepass, where '/', '@', '#' or ':' silently breaks parsing.
    LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32
}

ensure_generated() {
    local key="$1" existing
    existing="$(env_get "$key")"
    if [ -n "$existing" ] && [ "$ROTATE" -eq 0 ]; then
        return
    fi
    if [ -n "$existing" ] && [ "$ROTATE" -eq 1 ]; then
        c_ylw "$key: rotating. Dependent services must be restarted, and an"
        c_ylw "    existing Postgres volume will REJECT the new password --"
        c_ylw "    rotate the role inside the DB too, or start from a fresh volume."
    fi
    env_set "$key" "$(gen_password)"
}

# Hostname only: no scheme, no path, no spaces, at least one dot --
# catches a bare word like "civlytics" typed where a real public
# hostname (e.g. auth.civilytics.in) is needed. Keycloak builds its
# issuer/redirect URLs from this; a bad value fails token validation
# later, far from where the mistake was actually made.
validate_hostname() {
    [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$ ]]
}

# scheme://host[:port][/path], no "://" typos like "wss:///host" (an
# extra slash some other tool's output gets pasted with), no spaces.
validate_url() {
    local scheme="$1" val="$2"
    [[ "$val" =~ ^${scheme}://[A-Za-z0-9.-]+(:[0-9]+)?(/[^[:space:]]*)?$ ]]
}
validate_https() { validate_url https "$1"; }
validate_wss()   { validate_url wss "$1"; }
# nginx path-routes on these exact suffixes (nginx/nginx.conf's
# location /api/, /ws/, /webrtc/ blocks) -- a URL missing the suffix
# silently hits the dashboard's own "/" instead of the real backend.
validate_api_url()      { validate_url https "$1" && [[ "$1" == */api ]]; }
validate_ws_url()       { validate_url wss "$1" && [[ "$1" == */ws ]]; }
validate_mediamtx_url() { validate_url https "$1" && [[ "$1" == */webrtc ]]; }

ensure_prompted() {
    local key="$1" desc="$2" secret="${3:-0}" optional="${4:-0}" validate="${5:-}" existing val
    existing="$(env_get "$key")"
    [ -n "$existing" ] && return
    if [ ! -t 0 ]; then
        if [ "$optional" = "1" ]; then
            c_ylw "$key unset and no TTY — leaving empty (feature stays disabled)"
            env_set "$key" ""
            return
        fi
        die "$key is required but there is no TTY to prompt on. Set it in $ENV_FILE and re-run."
    fi
    while :; do
        printf '\n  %s\n' "$desc"
        if [ "$optional" = "1" ]; then printf '  %s (optional, Enter to skip): ' "$key"
        else printf '  %s: ' "$key"; fi
        if [ "$secret" = "1" ]; then read -rs val; printf '\n'; else read -r val; fi
        if [ -z "$val" ]; then
            [ "$optional" = "1" ] && break
            c_red "  $key is required"
            continue
        fi
        if [ -n "$validate" ] && ! "$validate" "$val"; then
            c_red "  '$val' doesn't look right for $key — check it and try again."
            continue
        fi
        break
    done
    env_set "$key" "$val"
}

c_dim "Stack: $ENVIRONMENT (writing defaults to $ENV_FILE as needed)"

case "$ENVIRONMENT" in
  # ── dev: working defaults inline, Keycloak configured by hand ──────
  dev)
    env_default WW_AUTH_DEV_MODE 0
    env_default WW_AUTH_COOKIE_SECURE false
    env_default KC_COMMAND start-dev
    env_default KC_HOSTNAME localhost
    ensure_generated POSTGRES_PASSWORD
    ensure_generated REDIS_PASSWORD
    ;;

  # ── test: same topology as prod, ephemeral, offset ports ───────────
  test)
    env_default WW_AUTH_DEV_MODE 0     # hard requirement — never 1 here
    env_default WW_AUTH_COOKIE_SECURE false
    env_default KC_COMMAND "start-dev --import-realm"
    env_default KC_HOSTNAME localhost
    env_default KEYCLOAK_REALM_IMPORT ./keycloak
    env_default WW_KEYCLOAK_CLIENT_SECRET test-client-secret-not-for-production
    # Mailpit needs no credentials and can never reach a real inbox, so
    # there's no reason to leave email-sending gated off here the way prod
    # is -- without this, WW_SMTP_HOST pointing at mailpit was a no-op
    # because the app's own smtp_enabled check still said no.
    env_default WW_SMTP_ENABLED true
    # +1000 offset so test can run alongside dev on one host.
    env_default PG_PORT 6433
    env_default KAFKA_PORT 10092
    env_default REDIS_PORT 7380
    env_default KEYCLOAK_PORT 9180
    env_default MAILPIT_PORT 9025
    env_default API_PORT 9082
    env_default MEDIAMTX_RTSP_PORT 9554
    env_default MEDIAMTX_WEBRTC_PORT 9889
    env_default MEDIAMTX_ICE_PORT 9189
    env_default MEDIAMTX_API_PORT 10997
    env_default RESTART_POLICY no
    ensure_generated POSTGRES_PASSWORD
    ensure_generated REDIS_PASSWORD
    [ -f keycloak/realm-test.json ] || c_ylw "keycloak/realm-test.json missing — Keycloak will start with an empty realm and auth tests will fail loudly (correct, not a silent pass)."
    ;;

  # ── prod: real secrets required, enforced here (not in compose) ────
  prod)
    if [ "$(env_get WW_AUTH_DEV_MODE)" = "1" ]; then
        die "WW_AUTH_DEV_MODE=1 in $ENV_FILE — it bypasses ALL authentication. Set it to 0 before starting prod."
    fi
    env_set WW_AUTH_DEV_MODE 0
    env_default WW_AUTH_COOKIE_SECURE true
    # Plain `start` (not `start --optimized`): the stock keycloak image has
    # no pre-built postgres config, so --optimized falls back to the h2
    # default and crashes against the postgres KC_DB_URL. `start` augments
    # the config for postgres at boot (~11s) and runs in production mode.
    env_default KC_COMMAND "start"
    env_default RESTART_POLICY always
    env_default PG_BIND 127.0.0.1
    env_default KAFKA_BIND 127.0.0.1
    env_default REDIS_BIND 127.0.0.1
    env_default KEYCLOAK_BIND 127.0.0.1

    c_dim "Generating passwords that have no external source ..."
    ensure_generated POSTGRES_PASSWORD
    ensure_generated REDIS_PASSWORD
    ensure_generated KEYCLOAK_ADMIN_PASSWORD
    ensure_generated MINIO_ROOT_PASSWORD

    c_dim "Values that must come from outside this script ..."
    ensure_prompted KEYCLOAK_ADMIN "Keycloak admin username." 0 0
    ensure_prompted KC_HOSTNAME \
      "Public hostname Keycloak is served on, e.g. auth.civilytics.in
  Keycloak builds its issuer/redirect URLs from this; a wrong value makes
  tokens fail audience validation." 0 0 validate_hostname
    ensure_prompted MINIO_ROOT_USER "MinIO root access key (evidence + backup object store)." 0 0
    ensure_prompted PUBLIC_API_URL "Browser-facing API URL, e.g. https://civilytics.example/api" 0 0 validate_api_url
    ensure_prompted PUBLIC_WS_URL "Browser-facing WebSocket URL, e.g. wss://civilytics.example/ws" 0 0 validate_ws_url
    ensure_prompted PUBLIC_MEDIAMTX_URL "Browser-facing MediaMTX WebRTC URL, e.g. https://civilytics.example/webrtc" 0 0 validate_mediamtx_url
    ensure_prompted WW_KEYCLOAK_CLIENT_SECRET \
      "Keycloak confidential-client secret for the branded login.
  Create the client in the realm, enable 'Direct access grants', then
  copy Credentials > Client secret." 1 0
    ensure_prompted WW_INTERNAL_SERVICE_TOKEN \
      "Shared secret for worker-to-api calls (leave empty to auto-generate)." 1 1
    if [ -z "$(env_get WW_INTERNAL_SERVICE_TOKEN)" ]; then
        env_set WW_INTERNAL_SERVICE_TOKEN "$(gen_password)"
    fi

    c_dim "Optional outbound channels — press Enter to leave disabled ..."
    ensure_prompted WW_SMTP_PASSWORD "SMTP password for report email." 1 1
    ensure_prompted WW_TELEGRAM_BOT_TOKEN "Telegram bot token for alert messages." 1 1
    [ -n "$(env_get WW_SMTP_PASSWORD)" ] && env_set WW_SMTP_ENABLED true || env_set WW_SMTP_ENABLED false
    [ -n "$(env_get WW_TELEGRAM_BOT_TOKEN)" ] && env_set WW_TELEGRAM_ENABLED true || env_set WW_TELEGRAM_ENABLED false

    CERT_DIR="${TLS_CERT_DIR:-./nginx/certs}"
    if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
        c_ylw "No TLS certificate at $CERT_DIR."
        c_ylw "nginx will generate a SELF-SIGNED one and browsers will warn."
        c_ylw "Run ./scripts/make-certs.sh first for a proper certificate."
    fi
    c_dim "Manual steps this script cannot do: create the Keycloak realm +"
    c_dim "confidential client (enable Direct access grants), define composite"
    c_dim "roles owner->admin->viewer, create the MinIO bucket + retention"
    c_dim "policy, and back up $ENV_FILE somewhere safe."
    ;;

  # ── lite: minimal footprint, no Keycloak at all ─────────────────────
  lite)
    c_ylw "lite has no Keycloak — forcing WW_AUTH_DEV_MODE=1 (no auth)."
    c_ylw "Do not expose this profile beyond localhost."
    env_set WW_AUTH_DEV_MODE 1
    env_default WW_AUTH_COOKIE_SECURE false
    env_default RESTART_POLICY unless-stopped
    ensure_generated POSTGRES_PASSWORD
    ensure_generated REDIS_PASSWORD
    ;;
esac

# ── Validate, then up ────────────────────────────────────────────────
c_dim "Validating compose config (profile=$ENVIRONMENT) ..."
"${DC[@]}" config >/dev/null || die "compose validation failed"

if [ -n "$PULL" ]; then
    c_dim "Pulling base images ..."
    "${DC[@]}" pull --ignore-buildable || true
fi

c_grn "Starting $ENVIRONMENT stack"
"${DC[@]}" up -d ${BUILD:+$BUILD}

[ "$WAIT" -eq 1 ] || { c_grn "Started (not waiting for health)"; exit 0; }

c_dim "Waiting up to ${TIMEOUT}s for containers to become healthy ..."
deadline=$(( $(date +%s) + TIMEOUT ))
last_report=""

while :; do
    ids="$("${DC[@]}" ps -q)"
    [ -n "$ids" ] || die "no containers running — check: ${DC[*]} logs"

    pending=() failed=() report=""
    for id in $ids; do
        name="$(docker inspect -f '{{.Name}}' "$id" | sed 's|^/||')"
        state="$(docker inspect -f '{{.State.Status}}' "$id")"
        health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")"
        report+="${name}:${state}/${health} "
        case "$state/$health" in
            running/healthy|running/none) ;;
            running/starting)  pending+=("$name") ;;
            running/unhealthy) failed+=("$name (unhealthy)") ;;
            exited/*)
                code="$(docker inspect -f '{{.State.ExitCode}}' "$id")"
                [ "$code" = "0" ] || failed+=("$name (exit $code)") ;;
            *) pending+=("$name") ;;
        esac
    done

    if [ ${#failed[@]} -gt 0 ]; then
        c_red "Failed: ${failed[*]}"
        for f in "${failed[@]}"; do
            svc="${f%% *}"
            echo; c_red "--- last 25 log lines: $svc ---"
            docker logs --tail 25 "$svc" 2>&1 | sed 's/^/    /'
        done
        exit 1
    fi

    if [ ${#pending[@]} -eq 0 ]; then c_grn "All containers healthy"; break; fi

    if [ "$(date +%s)" -ge "$deadline" ]; then
        c_red "Timed out after ${TIMEOUT}s. Still starting: ${pending[*]}"
        c_ylw "The pipeline can legitimately take several minutes on a cold"
        c_ylw "start if it has to build a TensorRT engine. Retry with"
        c_ylw "--timeout 1800, or check: ${DC[*]} logs pipeline"
        exit 1
    fi

    if [ "$report" != "$last_report" ]; then
        c_dim "  waiting: ${pending[*]}"
        last_report="$report"
    fi
    sleep 5
done

# ── Seed the first owner — ONLY when explicitly requested ──────────────
# Not run by default: granting the first privileged account shouldn't be
# an unconditional side effect of every container start. Whoever passes
# --seed-owner is asserting they're authorized to name the first owner.
# Requires Keycloak's confidential client + realm roles to already exist
# (manual setup for dev/prod per the comments above; test imports its own
# realm). Best-effort: a fresh dev/prod realm that hasn't been configured
# yet will fail this and the operator falls back to the admin console.
if [ "$SEED_OWNER" -eq 1 ] && [ "$ENVIRONMENT" != "lite" ]; then
    if [ "$(env_get WW_SMTP_ENABLED)" != "true" ]; then
        c_ylw "WW_SMTP_ENABLED is not 'true' — the owner invite email has nowhere to go."
        c_ylw "The account WILL be created regardless, but nobody can finish activating it"
        c_ylw "or log in until the setup link is delivered. Configure SMTP first, or resend"
        c_ylw "once it's working: ${DC[*]} exec api python scripts/seed_owner.py --resend --username <user>"
        if [ "$ENVIRONMENT" = "test" ]; then
            c_ylw "test profile: check Mailpit instead of a real inbox — http://localhost:\${MAILPIT_PORT:-8025}"
        fi
    fi
    if [ -t 0 ] && [ -t 1 ]; then
        c_dim "Owner account:"
        "${DC[@]}" exec -it api python scripts/seed_owner.py --interactive \
            || c_ylw "owner seed skipped — Keycloak realm/client not set up yet (see start.sh comments for $ENVIRONMENT), or re-run: ${DC[*]} exec -it api python scripts/seed_owner.py --interactive"
    else
        c_dim "Seeding owner account (no-op if one already exists) ..."
        "${DC[@]}" exec -T api python scripts/seed_owner.py \
            || c_ylw "owner seed skipped — Keycloak realm/client not set up yet (see start.sh comments for $ENVIRONMENT), or re-run: ${DC[*]} exec -it api python scripts/seed_owner.py --interactive"
    fi
fi

echo
"${DC[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
echo

case "$ENVIRONMENT" in
    dev)
        c_grn "Dashboard : http://localhost:5174"
        echo  "API       : http://localhost:8082/api/health"
        echo  "Keycloak  : http://localhost:8180  (admin/admin by default)"
        echo  "Conductor : http://localhost:5001"
        [ "$SEED_OWNER" -eq 1 ] && c_dim "Owner login: see 'Owner account' output above." \
            || c_dim "No owner yet — run: ./scripts/start.sh $ENVIRONMENT --seed-owner"
        ;;
    test)
        c_grn "Dashboard : http://localhost:6174"
        echo  "API       : http://localhost:9082/api/health"
        echo  "Keycloak  : http://localhost:9180"
        echo  "Mailpit   : http://localhost:9025  (catches the owner/invite setup emails)"
        c_dim "Host ports are offset +1000 so this can run alongside dev."
        [ "$SEED_OWNER" -eq 1 ] && c_dim "Owner login: see 'Owner account' output above." \
            || c_dim "No owner yet — run: ./scripts/start.sh $ENVIRONMENT --seed-owner"
        ;;
    prod)
        host="${KC_HOSTNAME:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
        c_grn "Dashboard : https://${host}/"
        echo  "API       : https://${host}/api/health"
        echo  "Storage   : https://${host}/storage/   (MinIO console)"
        echo  "RTSP in   : rtsp://${host}:554/<path>  (cameras publish here)"
        c_dim "Start coturn (--turn) if viewers are off the camera LAN."
        [ "$SEED_OWNER" -eq 1 ] && c_ylw "Owner login: see 'Owner account' output above." \
            || c_ylw "No owner yet — run: ./scripts/start.sh $ENVIRONMENT --seed-owner"
        ;;
    lite)
        c_grn "Dashboard : http://localhost:5174"
        echo  "API       : http://localhost:8082/api/health"
        c_ylw "No auth — do not expose this host beyond localhost."
        ;;
esac
