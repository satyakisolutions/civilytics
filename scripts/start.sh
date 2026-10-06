#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Civilytics / WasteWatch — stack startup.
#
#   ./scripts/start.sh dev|test|prod [options]
#
#   --build        rebuild images before starting
#   --pull         pull newer base images first
#   --turn         prod only: also start coturn (TURN relay)
#   --no-wait      return as soon as compose returns, don't wait for health
#   --timeout N    seconds to wait for health (default 900)
#   --down         stop the stack (test also removes its volumes)
#   --status       show health of every container and exit
#
# The previous version of this script sequenced startup by hand with
# sleeps, and referenced qdrant / rtsp-streamer / vlm, which no longer
# exist. Ordering is now declarative: every service has a healthcheck and
# depends_on: condition: service_healthy, with api and pipeline gating
# backend-workers, and nginx gating on both. Compose does the ordering;
# this script only waits and reports.
#
# Original kept at docker/legacy/start.sh.bak
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENVIRONMENT=""
BUILD="" PULL="" PROFILES=() WAIT=1 TIMEOUT=900 ACTION="up"

c_red() { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn() { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw() { printf '\033[33m%s\033[0m\n' "$*"; }
c_dim() { printf '\033[2m%s\033[0m\n' "$*"; }
die()   { c_red "ERROR: $*" >&2; exit 1; }

usage() { sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2; }

[ $# -ge 1 ] || usage
case "$1" in
    dev|test|prod) ENVIRONMENT="$1"; shift ;;
    *) usage ;;
esac

while [ $# -gt 0 ]; do
    case "$1" in
        --build)    BUILD="--build"; shift ;;
        --pull)     PULL=1; shift ;;
        --turn)     PROFILES+=(--profile turn); shift ;;
        --no-wait)  WAIT=0; shift ;;
        --timeout)  TIMEOUT="${2:?--timeout needs a value}"; shift 2 ;;
        --down)     ACTION="down"; shift ;;
        --status)   ACTION="status"; shift ;;
        *) die "unknown option: $1" ;;
    esac
done

COMPOSE_FILE="docker-compose.${ENVIRONMENT}.yml"
[ -f "$COMPOSE_FILE" ] || die "$COMPOSE_FILE not found"

DC=(docker compose -f "$COMPOSE_FILE" "${PROFILES[@]+"${PROFILES[@]}"}")

if [ "${PROFILES[*]+x}" ] && [ "$ENVIRONMENT" != "prod" ]; then
    die "--turn is prod only (coturn is defined in the prod stack)"
fi

# ── down / status ─────────────────────────────────────────────────────
if [ "$ACTION" = "down" ]; then
    if [ "$ENVIRONMENT" = "test" ]; then
        # Test state is meant to be disposable; leaving volumes behind
        # would let one run's data leak into the next.
        c_ylw "Stopping test stack and REMOVING its volumes"
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

# ── Preflight ─────────────────────────────────────────────────────────
command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 plugin not found"

[ -f .env ] || {
    c_ylw ".env missing — creating an empty one (compose declares env_file: .env)"
    touch .env
}

# GPU is required by the pipeline in all three environments.
if ! docker info 2>/dev/null | grep -qi nvidia; then
    c_ylw "WARNING: the NVIDIA container runtime was not detected."
    c_ylw "         The pipeline service will fail to start without it."
fi

if [ "$ENVIRONMENT" = "prod" ]; then
    CERT_DIR="${TLS_CERT_DIR:-./nginx/certs}"
    if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
        c_ylw "No TLS certificate at $CERT_DIR."
        c_ylw "nginx will generate a SELF-SIGNED one and browsers will warn."
        c_ylw "Run ./scripts/make-certs.sh first for a proper certificate."
    fi
    # WW_AUTH_DEV_MODE bypasses REST *and* WebSocket auth and grants
    # owner+admin+viewer. Refuse rather than warn.
    if grep -qE '^WW_AUTH_DEV_MODE=(1|true|yes)' .env 2>/dev/null; then
        die "WW_AUTH_DEV_MODE is enabled in .env — it bypasses ALL authentication. Set it to 0 before starting prod."
    fi
fi

c_dim "Validating $COMPOSE_FILE ..."
"${DC[@]}" config >/dev/null || die "compose validation failed (missing secrets? run ./scripts/bootstrap.sh $ENVIRONMENT)"

[ -n "$PULL" ] && { c_dim "Pulling base images ..."; "${DC[@]}" pull --ignore-buildable || true; }

# ── Up ────────────────────────────────────────────────────────────────
c_grn "Starting $ENVIRONMENT stack"
c_dim "Compose resolves the order from healthchecks: stores -> keycloak/mediamtx -> api + pipeline -> workers -> frontend -> nginx"
"${DC[@]}" up -d ${BUILD:+$BUILD}

[ "$WAIT" -eq 1 ] || { c_grn "Started (not waiting for health)"; exit 0; }

# ── Wait for health ───────────────────────────────────────────────────
# Containers without a healthcheck report no status, so "running" is the
# best signal available for them; they must not block the wait.
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
                # A clean exit is fine for a one-shot; non-zero is not.
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

    if [ ${#pending[@]} -eq 0 ]; then
        c_grn "All containers healthy"
        break
    fi

    if [ "$(date +%s)" -ge "$deadline" ]; then
        c_red "Timed out after ${TIMEOUT}s. Still starting: ${pending[*]}"
        c_ylw "The pipeline can legitimately take several minutes on a cold"
        c_ylw "start if it has to build a TensorRT engine. Retry with"
        c_ylw "--timeout 1800, or check: ${DC[*]} logs pipeline"
        exit 1
    fi

    # Only reprint when something changed, so the log stays readable.
    if [ "$report" != "$last_report" ]; then
        c_dim "  waiting: ${pending[*]}"
        last_report="$report"
    fi
    sleep 5
done

# ── Report ────────────────────────────────────────────────────────────
echo
"${DC[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
echo

case "$ENVIRONMENT" in
    dev)
        c_grn "Dashboard : http://localhost:5174"
        echo  "API       : http://localhost:8082/api/health"
        echo  "Keycloak  : http://localhost:8180  (admin/admin by default)"
        echo  "WebRTC    : http://localhost:8889"
        ;;
    test)
        c_grn "Dashboard : http://localhost:6174"
        echo  "API       : http://localhost:9082/api/health"
        echo  "Keycloak  : http://localhost:9180"
        echo  "WebRTC    : http://localhost:9889"
        c_dim "Host ports are offset +1000 so this can run alongside dev."
        ;;
    prod)
        host="${KC_HOSTNAME:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
        c_grn "Dashboard : https://${host}/"
        echo  "API       : https://${host}/api/health"
        echo  "Storage   : https://${host}/storage/   (MinIO console)"
        echo  "RTSP in   : rtsp://${host}:554/<path>  (cameras publish here)"
        echo
        c_dim "nginx is the only ingress. The one exception is mediamtx's"
        c_dim "8189/udp — WebRTC media is ICE over UDP and cannot be proxied."
        c_dim "Start coturn (--turn) if viewers are off the camera LAN."
        ;;
esac
