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
#   --smtp         prod only: prompt for SMTP host/account/password again
#   --down         stop the tunnel, stop the stack, and remove its volumes
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
# Make Ctrl-C actually abort — without this, an interrupt during a `read`
# prompt just returns to the prompt loop and the script can't be killed.
trap 'printf "\nAborted.\n" >&2; exit 130' INT

cd "$(dirname "${BASH_SOURCE[0]}")/.."

ENV_FILE=".env"
ENVIRONMENT=""
BUILD="" PULL="" EXTRA_PROFILES=() WAIT=1 TIMEOUT=900 ACTION="up" ROTATE=0 SEED_OWNER=0 RUN_E2E=0 CONFIGURE_SMTP=0
# A build from an empty environment is the one-time provisioning flow.  Keep
# subsequent starts non-interactive and preserve the operator's saved values.
FRESH_ENV=0
[ -s "$ENV_FILE" ] || FRESH_ENV=1

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
        --smtp)      CONFIGURE_SMTP=1; shift ;;
        --e2e)      RUN_E2E=1; shift ;;
        --down)     ACTION="down"; shift ;;
        --status)   ACTION="status"; shift ;;
        --clean-volumes) ACTION="clean-volumes"; shift ;;
        *) die "unknown option: $1" ;;
    esac
done

# A fresh production build provisions the complete public stack, including
# Coturn.  The profile is still optional on ordinary restarts so existing
# deployments are not changed unexpectedly.
if [ "$ENVIRONMENT" = "prod" ] && [ "$BUILD" = "--build" ] && [ "$FRESH_ENV" -eq 1 ]; then
    EXTRA_PROFILES+=(--profile turn)
    c_dim "Fresh production build: Coturn and SMTP settings will be collected now."
fi

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

stop_ngrok_tunnel() {
    # The tunnel is deliberately outside Compose so it can use host networking
    # to reach nginx on port 80. Its fixed name keeps cleanup project-scoped.
    if docker container inspect civilytics-prod-ngrok >/dev/null 2>&1; then
        docker rm -f civilytics-prod-ngrok >/dev/null
        c_grn "Stopped ngrok tunnel."
    fi
}

start_ngrok_tunnel() {
    local ngrok_domain ngrok_token
    ngrok_domain="$(env_get NGROK_DOMAIN)"
    ngrok_token="$(env_get NGROK_AUTHTOKEN)"
    [ -n "$ngrok_domain" ] || die "NGROK_DOMAIN is required for the production tunnel."
    # Never prompted (it is a credential). Say exactly how to supply it, and
    # that it must belong to the account that reserved the domain -- a token
    # from any other account fails with ERR_NGROK_320 and the tunnel, but not
    # the app, stays down.
    [ -n "$ngrok_token" ] || die "NGROK_AUTHTOKEN is empty. Add it to .env (NGROK_AUTHTOKEN=<token>) or run:
  NGROK_AUTHTOKEN=<token> ./scripts/start.sh $ENVIRONMENT
It must be the authtoken of the ngrok account that reserved ${ngrok_domain}."

    # Replace a stale project tunnel before creating this one. `-d` is needed
    # here so the script can continue immediately to bring up the app.
    stop_ngrok_tunnel
    c_grn "Starting ngrok tunnel for https://${ngrok_domain}/"
    docker run --net=host -d --name civilytics-prod-ngrok \
        -e "NGROK_AUTHTOKEN=${ngrok_token}" \
        ngrok/ngrok:latest http 80 --url "https://${ngrok_domain}" >/dev/null
}

# ── down / status — no secret handling needed ──────────────────────────
if [ "$ACTION" = "down" ]; then
    # Stop the public endpoint first so it cannot point to a shutting-down app.
    [ "$ENVIRONMENT" != "prod" ] || stop_ngrok_tunnel
    c_ylw "Stopping ${ENVIRONMENT} stack and removing its volumes"
    "${DC[@]}" down --volumes --remove-orphans
    exit 0
fi

if [ "$ACTION" = "status" ]; then
    "${DC[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}'
    exit 0
fi

# ── clean-volumes — wipe ONLY this env's data volumes ──────────────────
# Stops the stack and deletes its named volumes (postgres, kafka, redis,
# qdrant, minio, evidence, tts, engine cache, …). Images, networks and the
# repo are untouched. DESTRUCTIVE and irreversible — all DB rows, evidence,
# object storage and the built TRT engines for this environment are gone.
if [ "$ACTION" = "clean-volumes" ]; then
    c_red "This DELETES all '${ENVIRONMENT}' data volumes: Postgres, Kafka, Redis,"
    c_red "Qdrant, MinIO, evidence, TTS cache, and the built TensorRT engines."
    c_red "Containers stop; images and the repo are kept. This cannot be undone."
    if [ -t 0 ]; then
        printf 'Type the environment name (%s) to confirm: ' "$ENVIRONMENT"
        read -r reply
        [ "$reply" = "$ENVIRONMENT" ] || die "Confirmation did not match — aborted."
    else
        die "Refusing to wipe volumes without an interactive confirmation (no TTY)."
    fi
    # Keep the public endpoint from pointing at a freshly erased stack.
    [ "$ENVIRONMENT" != "prod" ] || stop_ngrok_tunnel
    "${DC[@]}" down --volumes --remove-orphans
    c_grn "All '${ENVIRONMENT}' volumes removed. Next start rebuilds DB schema + TRT engines."
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
# The frontend's API client (frontend/src/lib/api-base.ts) treats the API
# base as an ORIGIN and appends "/api/..." itself, so PUBLIC_API_URL must be
# scheme://host[:port] with NO /api suffix -- a trailing /api double-prefixes
# into /api/api/... and 404s. WS and MediaMTX are used as-is, so they DO carry
# their /ws and /webrtc suffix. Accept an origin or a bare host for the API
# (the /api, if the user types one, is stripped at expansion below).
validate_api_url()      { validate_url https "$1" || validate_hostname "$1"; }
validate_ws_url()       { { validate_url wss "$1" && [[ "$1" == */ws ]]; } || validate_hostname "$1"; }
validate_mediamtx_url() { validate_url https "$1" && [[ "$1" == */webrtc ]]; }

# A reserved ngrok endpoint is a hostname just like a conventional DNS name.
# Keep it separate from KC_HOSTNAME because the tunnel is optional, but when
# it is the public ingress Keycloak *must* advertise that hostname in its
# issuer and redirect URLs.
validate_ngrok_domain() { validate_hostname "$1"; }

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
        # Exit on interrupt/EOF rather than looping forever on empty input.
        if [ "$secret" = "1" ]; then
            read -rs val || { printf '\n'; exit 130; }
            printf '\n'
        else
            read -r val || { printf '\n'; exit 130; }
        fi
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
    # --import-realm loads the wastewatch realm generated below.
    env_default KC_COMMAND "start --import-realm"
    env_default RESTART_POLICY always
    env_default PG_BIND 127.0.0.1
    env_default KAFKA_BIND 127.0.0.1
    env_default REDIS_BIND 127.0.0.1
    env_default KEYCLOAK_BIND 127.0.0.1
    env_default MEDIAMTX_WEBRTC_BIND 127.0.0.1

    c_dim "Generating passwords that have no external source ..."
    ensure_generated POSTGRES_PASSWORD
    ensure_generated REDIS_PASSWORD
    ensure_generated KEYCLOAK_ADMIN_PASSWORD
    ensure_generated MINIO_ROOT_PASSWORD

    c_dim "Values that must come from outside this script ..."
    ensure_prompted KEYCLOAK_ADMIN "Keycloak admin username." 0 0

    # The production endpoint is intentionally fixed; it is created before
    # Compose so the public URL is already reserved while the app boots.
    env_set NGROK_DOMAIN related-elsewhere-undertook.ngrok-free.dev
    # NOT prompted. The authtoken is a credential, so it stays out of this
    # script (and out of git) and comes from .env or the process environment:
    #   NGROK_AUTHTOKEN=<token> ./scripts/start.sh prod
    # Once it is in .env it is never asked for again. start_ngrok_tunnel
    # fails with the fix if it is still empty.
    env_default NGROK_AUTHTOKEN ""
    # The endpoint is fixed, so the hostname Keycloak advertises follows from
    # it with nothing to decide. Keycloak builds its issuer and redirect URLs
    # from KC_HOSTNAME, and a mismatch with the public domain breaks login for
    # every device that is not this one.
    env_set KC_HOSTNAME "$(env_get NGROK_DOMAIN)"
    ensure_prompted KC_HOSTNAME \
      "Public hostname Keycloak is served on, e.g. auth.civilytics.in.
  If using ngrok, use its public domain here. Keycloak builds issuer and
  redirect URLs from this value; a hostname that only resolves on this server
  prevents other devices from signing in." 0 0 validate_hostname
    if [[ " ${EXTRA_PROFILES[*]} " == *" turn "* ]]; then
        ensure_prompted TURN_HOST \
          "Public DNS hostname for Coturn, e.g. turn.civilytics.in.
  This must resolve directly to this server's public IP. An ngrok HTTP domain
  cannot carry TURN UDP/TLS traffic, so do not use an ngrok domain here." 0 0 validate_hostname
        ensure_prompted TURN_EXTERNAL_IP \
          "Coturn public IPv4 address (the address reached by TURN clients)." 0 0
        ensure_prompted TURN_USER "Coturn username." 0 0
        ensure_prompted TURN_PASSWORD "Coturn password." 1 0
        if [ "$(env_get TURN_HOST)" = "$(env_get NGROK_DOMAIN)" ]; then
            die "TURN_HOST cannot be the ngrok domain. Point a real DNS hostname directly at this server and expose 3478/TCP+UDP, 5349/TCP, and 49160-49500/UDP."
        fi
    fi
    ensure_prompted MINIO_ROOT_USER "MinIO root access key (evidence + backup object store)." 0 0
    # NOT prompted. Blank means the frontend resolves the API, WebSocket and
    # WebRTC same-origin through nginx (frontend/src/lib/api-base.ts), so one
    # build works at every hostname it is reached by -- the LAN IP and the
    # ngrok domain -- with no rebuild. Pinning them to one absolute host is
    # what broke the other hosts, so blank is the default and the only answer
    # worth having. Override in .env if a deployment genuinely needs it.
    env_default PUBLIC_API_URL ""
    env_default PUBLIC_WS_URL ""
    env_default PUBLIC_MEDIAMTX_URL ""
    public_api="$(env_get PUBLIC_API_URL)"
    if [ -n "$public_api" ]; then
        # The client appends /api itself, so store the ORIGIN only. Add the
        # scheme to a bare host and strip any trailing slash or /api the user
        # typed (either would double-prefix into /api/api/... and 404).
        [[ "$public_api" == *://* ]] || public_api="https://$public_api"
        public_api="${public_api%/}"; public_api="${public_api%/api}"
        env_set PUBLIC_API_URL "$public_api"
    fi
    public_ws="$(env_get PUBLIC_WS_URL)"
    if [ -n "$public_ws" ] && [[ "$public_ws" != *://* ]]; then
        env_set PUBLIC_WS_URL "wss://$public_ws/ws"
    elif [ -n "$public_ws" ] && [[ "$public_ws" =~ ^wss://[^/]+/?$ ]]; then
        env_set PUBLIC_WS_URL "${public_ws%/}/ws"
    fi
    ensure_prompted WW_KEYCLOAK_CLIENT_SECRET \
      "Keycloak confidential-client secret for the branded login.
  Create the client in the realm, enable 'Direct access grants', then
  copy Credentials > Client secret." 1 0
    ensure_prompted WW_INTERNAL_SERVICE_TOKEN \
      "Shared secret for worker-to-api calls (leave empty to auto-generate)." 1 1
    if [ -z "$(env_get WW_INTERNAL_SERVICE_TOKEN)" ]; then
        env_set WW_INTERNAL_SERVICE_TOKEN "$(gen_password)"
    fi

    # SMTP — collected before the realm is generated so Keycloak's realm can
    # send owner/invite activation emails. Without a host the account is still
    # created, but you must set its password manually (kcadm set-password) or
    # re-run with SMTP configured and `seed_owner.py --resend`.
    if [ "$CONFIGURE_SMTP" -eq 1 ]; then
        c_dim "SMTP reconfiguration requested — existing SMTP values will be replaced."
        env_set WW_SMTP_HOST ""
        env_set WW_SMTP_USER ""
        env_set WW_SMTP_PASSWORD ""
        env_set WW_SMTP_FROM ""
    fi
    c_dim "SMTP for activation emails (press Enter to skip; password input is hidden) ..."
    ensure_prompted WW_SMTP_HOST "SMTP server host, e.g. smtp.gmail.com (blank to skip email)." 1 1
    if [ -n "$(env_get WW_SMTP_HOST)" ]; then
        env_default WW_SMTP_PORT 587
        ensure_prompted WW_SMTP_USER "SMTP username (often the full email address)." 1 1
        ensure_prompted WW_SMTP_FROM "From address for outgoing mail, e.g. no-reply@yourdomain." 0 0
        ensure_prompted WW_SMTP_PASSWORD "SMTP password / app-password." 1 0
        env_set WW_SMTP_ENABLED true
    fi

    # Generate the prod Keycloak realm from the committed template, with the
    # real client secret substituted in (the template ships a ${…}
    # placeholder, never a real secret). Keycloak --import-realm loads it on
    # first boot, so the wastewatch realm + wastewatch-api service-account
    # client exist and owner seeding works — no manual realm creation.
    if [ -f keycloak/realm-prod.json ]; then
        mkdir -p keycloak/prod-import
        # Substitute the client secret and the SMTP settings (so Keycloak's
        # realm can send the owner/invite activation emails) into the realm.
        WW_KEYCLOAK_CLIENT_SECRET="$(env_get WW_KEYCLOAK_CLIENT_SECRET)" \
        WW_SMTP_HOST="$(env_get WW_SMTP_HOST)" \
        WW_SMTP_PORT="$(env_get WW_SMTP_PORT)" \
        WW_SMTP_FROM="$(env_get WW_SMTP_FROM)" \
        WW_SMTP_USER="$(env_get WW_SMTP_USER)" \
        WW_SMTP_PASSWORD="$(env_get WW_SMTP_PASSWORD)" \
            envsubst '${WW_KEYCLOAK_CLIENT_SECRET} ${WW_SMTP_HOST} ${WW_SMTP_PORT} ${WW_SMTP_FROM} ${WW_SMTP_USER} ${WW_SMTP_PASSWORD}' \
            < keycloak/realm-prod.json > keycloak/prod-import/civilytics-realm.json
        env_default KEYCLOAK_REALM_IMPORT ./keycloak/prod-import
        c_dim "Keycloak realm written to keycloak/prod-import/ (imported on boot)."
    fi

    c_dim "Administrator notification settings ..."
    ensure_prompted WW_SMTP_PASSWORD "SMTP password for report email." 1 1
    # Telegram is administrator-managed application data, not bootstrap input.
    # Preserve an existing token from .env, but never request it interactively
    # during a fresh build. Configure it later from the admin settings store.
    [ -n "$(env_get WW_SMTP_PASSWORD)" ] && env_set WW_SMTP_ENABLED true || env_set WW_SMTP_ENABLED false
    [ -n "$(env_get WW_TELEGRAM_BOT_TOKEN)" ] && env_set WW_TELEGRAM_ENABLED true || env_set WW_TELEGRAM_ENABLED false
    [ -n "$(env_get WW_TELEGRAM_BOT_TOKEN)" ] && env_set WW_TELEGRAM_ENABLED true || env_set WW_TELEGRAM_ENABLED false

    CERT_DIR="${TLS_CERT_DIR:-./nginx/certs}"
    cert_file="$CERT_DIR/fullchain.pem"
    key_file="$CERT_DIR/privkey.pem"
    if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
        c_ylw "TLS certificate pair is missing at $CERT_DIR."
        # This is deliberately a host-side preflight, not a Docker build
        # layer: certificates and private keys must never be baked into an
        # image. It runs for `prod --build` as well as normal prod starts.
        # Operators who require only a CA-issued certificate can set this to
        # false and install the pair themselves before starting.
        if [ "$(env_get TLS_AUTO_GENERATE)" = "false" ]; then
            die "TLS certificate pair missing at $CERT_DIR and TLS_AUTO_GENERATE=false. Install a certificate pair before starting."
        fi
        c_dim "Generating local-CA TLS certificate for the production ingress ..."
        cert_hosts=(--host "$(env_get KC_HOSTNAME)")
        [ -n "$(env_get TURN_HOST)" ] && cert_hosts+=(--host "$(env_get TURN_HOST)")
        ./scripts/make-certs.sh selfsigned "${cert_hosts[@]}"
    fi
    openssl x509 -in "$cert_file" -noout >/dev/null 2>&1 \
        || die "Invalid TLS certificate: $cert_file"
    openssl pkey -in "$key_file" -noout >/dev/null 2>&1 \
        || die "Invalid TLS private key: $key_file"
    [ "$(openssl x509 -in "$cert_file" -noout -pubkey 2>/dev/null | openssl pkey -pubin -outform pem 2>/dev/null | sha256sum | awk '{print $1}')" = \
      "$(openssl pkey -in "$key_file" -pubout -outform pem 2>/dev/null | sha256sum | awk '{print $1}')" ] \
        || die "TLS certificate and private key do not match in $CERT_DIR"
    if ! openssl x509 -in "$cert_file" -checkend 1209600 -noout >/dev/null 2>&1; then
        c_ylw "TLS certificate expires within 14 days (or is expired) — renew it before exposing the service."
    fi
    # For a direct DNS deployment the certificate must authenticate the same
    # hostname Keycloak and nginx advertise. With ngrok, its edge owns the
    # public TLS certificate, so the local certificate is intentionally not
    # required to contain the ngrok hostname.
    if [ -z "$(env_get NGROK_DOMAIN)" ] && ! openssl x509 -in "$cert_file" -checkhost "$(env_get KC_HOSTNAME)" -noout >/dev/null 2>&1; then
        die "TLS certificate at $cert_file does not cover KC_HOSTNAME=$(env_get KC_HOSTNAME). Regenerate it or install the correct certificate."
    fi
    if [ -n "$(env_get TURN_HOST)" ] && ! openssl x509 -in "$cert_file" -checkhost "$(env_get TURN_HOST)" -noout >/dev/null 2>&1; then
        die "TLS certificate at $cert_file does not cover TURN_HOST=$(env_get TURN_HOST). Install a certificate with both Keycloak and TURN hostnames in its SAN list."
    fi
    c_dim "Manual steps this script cannot do: create the MinIO bucket +"
    c_dim "retention policy, and back up $ENV_FILE somewhere safe."
    c_dim "(The Keycloak realm + confidential client + owner->admin->viewer"
    c_dim "roles are now imported automatically from keycloak/realm-prod.json.)"
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

# ── Validate the detector model is really present ────────────────────
# A fresh clone without `git lfs pull` leaves ~130-byte pointer stubs in
# place of the model weights; the stack would then start and only fail at
# inference. Catch it here. Skipped for 'lite' (no detector).
validate_models() {
    local dir="backend/models/detector"
    local onnx="$dir/civilytics_det_3class.onnx"
    local classes="$dir/classes.json"
    [ -f "$onnx" ]    || die "detector ONNX missing: $onnx (run: git lfs pull)"
    [ -f "$classes" ] || die "detector classes.json missing: $classes (run: git lfs pull)"
    # LFS pointer stubs begin with this line and are tiny.
    if head -c 64 "$onnx" | grep -q "git-lfs.github.com"; then
        die "detector ONNX is an unresolved git-LFS pointer — run: git lfs pull"
    fi
    local sz; sz=$(stat -c%s "$onnx" 2>/dev/null || echo 0)
    [ "$sz" -gt 1000000 ] || die "detector ONNX too small (${sz} bytes) — likely corrupt or an LFS stub; run: git lfs pull"
    python3 -c "import json,sys; json.load(open('$classes'))" 2>/dev/null \
        || die "detector classes.json is not valid JSON: $classes"
    c_dim "Detector model OK ($(( sz / 1024 / 1024 )) MB ONNX, classes.json valid)."
}

# ── Validate, then up ────────────────────────────────────────────────
[ "$ENVIRONMENT" = "lite" ] || validate_models

c_dim "Validating compose config (profile=$ENVIRONMENT) ..."
"${DC[@]}" config >/dev/null || die "compose validation failed"

if [ -n "$PULL" ]; then
    c_dim "Pulling base images ..."
    "${DC[@]}" pull --ignore-buildable || true
fi

c_grn "Starting $ENVIRONMENT stack"
# Start the public endpoint before Compose. It runs in Docker with host
# networking, exactly like `docker run --net=host ... ngrok/ngrok:latest
# http 80 --url https://<domain>`, but detached so startup can continue.
[ "$ENVIRONMENT" != "prod" ] || start_ngrok_tunnel
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

# ── Apply DB migrations ────────────────────────────────────────────────
# Always run after the stack is healthy: on a fresh Postgres volume the
# schema is empty, and without this the API 500s on every query
# ("relation \"cameras\" does not exist"). alembic is idempotent — a no-op
# when already at head. Skipped for lite (no DB-backed features).
if [ "$ENVIRONMENT" != "lite" ]; then
    c_dim "Applying database migrations (alembic upgrade head) ..."
    if "${DC[@]}" exec -T api alembic upgrade head; then
        c_grn "Database schema up to date."
    else
        c_ylw "Migrations failed — the API will error until the schema is applied."
        c_ylw "Re-run: ${DC[*]} exec api alembic upgrade head"
    fi
fi

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

        c_grn "Public    : https://$(env_get NGROK_DOMAIN)/  (ngrok container → host port 80)"
        ;;
    lite)
        c_grn "Dashboard : http://localhost:5174"
        echo  "API       : http://localhost:8082/api/health"
        c_ylw "No auth — do not expose this host beyond localhost."
        ;;
esac

# ── Optional: run the E2E suite against the stack we just started ─────
if [ "${RUN_E2E:-0}" -eq 1 ]; then
    if [ ! -d tests/e2e ]; then
        c_ylw "--e2e: tests/e2e/ not present yet — skipping."
    else
        case "$ENVIRONMENT" in
            prod) e2e_base="https://${KC_HOSTNAME:-localhost}" ;;
            test) e2e_base="http://localhost:9082" ;;
            *)    e2e_base="http://localhost:8082" ;;
        esac
        echo
        c_grn "Running E2E suite against ${e2e_base} ..."
        if [ -f tests/e2e/requirements.txt ]; then
            python3 -m pip install -q --user -r tests/e2e/requirements.txt 2>/dev/null \
                || c_ylw "Could not install tests/e2e/requirements.txt — run it manually."
        fi
        CIVILYTICS_BASE_URL="${e2e_base}" python3 -m pytest tests/e2e -m e2e -q \
            || c_ylw "E2E suite reported failures (see output above)."
    fi
fi
