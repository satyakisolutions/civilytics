#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Civilytics / WasteWatch — stack bootstrap
#
#   ./scripts/bootstrap.sh dev            # bring dev up (no secrets needed)
#   ./scripts/bootstrap.sh test           # bring test up (no secrets needed)
#   ./scripts/bootstrap.sh prod           # prompt/generate secrets, then up
#   ./scripts/bootstrap.sh prod --no-up   # write .env only, don't start
#   ./scripts/bootstrap.sh prod --rotate  # regenerate generated passwords
#
# dev and test carry working defaults inline in their compose files, so
# they start with no .env content at all. Only prod requires real secrets,
# and this script is how they get created.
#
# Generated passwords are deliberately ALPHANUMERIC ONLY. They are
# interpolated into a SQLAlchemy URL
# (postgresql+asyncpg://user:PASSWORD@host/db) and passed to redis-server
# --requirepass; a '/', '@', '#' or ':' in either position silently breaks
# connection parsing in ways that surface much later as an auth failure.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ENV_FILE=".env"

STACK="${1:-}"
shift || true
NO_UP=0
ROTATE=0
for arg in "$@"; do
  case "$arg" in
    --no-up)  NO_UP=1 ;;
    --rotate) ROTATE=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

case "$STACK" in
  dev)  COMPOSE_FILE="docker-compose.dev.yml" ;;
  test) COMPOSE_FILE="docker-compose.test.yml" ;;
  prod) COMPOSE_FILE="docker-compose.prod.yml" ;;
  *)
    cat >&2 <<USAGE
Usage: $0 {dev|test|prod} [--no-up] [--rotate]

  dev    local workstation, bind-mounted source, weak default secrets
  test   ephemeral integration stack, offset ports, weak default secrets
  prod   requires real secrets; this script creates them
USAGE
    exit 2
    ;;
esac

info() { printf '\033[0;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[0;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker not found"
docker compose version >/dev/null 2>&1 || die "docker compose v2 not found"

# Compose errors out when a declared env_file is missing, and all three
# stacks declare one. An empty file satisfies that for dev/test.
if [[ ! -f "$ENV_FILE" ]]; then
  info "Creating empty $ENV_FILE (compose requires the file to exist)"
  : > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

# ── .env helpers ──────────────────────────────────────────────────────
env_get() {
  # Last assignment wins, matching dotenv semantics.
  sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1
}

env_set() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  # Drop any existing assignment, then append. Avoids sed escaping issues
  # with values containing / & etc.
  grep -v "^${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

gen_password() {
  # 32 alphanumeric chars from a CSPRNG. See the header note on why no
  # punctuation. ~190 bits of entropy.
  LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 32
}

# Generate only if absent (or --rotate). Never prints the value.
ensure_generated() {
  local key="$1" existing
  existing="$(env_get "$key")"
  if [[ -n "$existing" && $ROTATE -eq 0 ]]; then
    info "$key already set — keeping"
    return
  fi
  if [[ -n "$existing" && $ROTATE -eq 1 ]]; then
    warn "$key: rotating. Dependent services must be restarted, and an"
    warn "    existing Postgres volume will REJECT the new password --"
    warn "    rotate the role inside the DB too, or start from a fresh volume."
  fi
  env_set "$key" "$(gen_password)"
  info "$key generated (32 chars, alphanumeric)"
}

# Prompt for a value that cannot be generated locally.
ensure_prompted() {
  local key="$1" desc="$2" secret="${3:-0}" optional="${4:-0}" existing val
  existing="$(env_get "$key")"
  if [[ -n "$existing" ]]; then
    info "$key already set — keeping"
    return
  fi
  if [[ ! -t 0 ]]; then
    if [[ "$optional" == "1" ]]; then
      warn "$key unset and no TTY — leaving empty (feature stays disabled)"
      env_set "$key" ""
      return
    fi
    die "$key is required but there is no TTY to prompt on. Set it in $ENV_FILE and re-run."
  fi
  printf '\n  %s\n' "$desc"
  if [[ "$optional" == "1" ]]; then
    printf '  %s (optional, Enter to skip): ' "$key"
  else
    printf '  %s: ' "$key"
  fi
  if [[ "$secret" == "1" ]]; then
    read -rs val; printf '\n'
  else
    read -r val
  fi
  if [[ -z "$val" && "$optional" != "1" ]]; then
    die "$key is required"
  fi
  env_set "$key" "$val"
}

# ── dev / test: nothing to do but start ───────────────────────────────
if [[ "$STACK" != "prod" ]]; then
  info "Stack: $STACK (defaults come from $COMPOSE_FILE; no secrets required)"
  docker compose -f "$COMPOSE_FILE" config >/dev/null || die "compose config invalid"
  if [[ $NO_UP -eq 1 ]]; then
    info "Config valid. --no-up given, not starting."
    exit 0
  fi
  info "Starting $STACK ..."
  docker compose -f "$COMPOSE_FILE" up -d
  echo
  docker compose -f "$COMPOSE_FILE" ps
  exit 0
fi

# ── prod ──────────────────────────────────────────────────────────────
info "Stack: prod — secrets are required and will be written to $ENV_FILE"
echo
warn "$ENV_FILE is gitignored and will be chmod 600. Do not commit it."

info "Generating passwords that have no external source ..."
ensure_generated POSTGRES_PASSWORD
ensure_generated REDIS_PASSWORD
ensure_generated KEYCLOAK_ADMIN_PASSWORD
ensure_generated MINIO_ROOT_PASSWORD

# KC_DB_PASSWORD intentionally not generated: Keycloak connects to the SAME
# Postgres database as the app (see docker-compose.prod.yml), so a separate
# value would simply fail to authenticate.
env_set KC_DB_PASSWORD "$(env_get POSTGRES_PASSWORD)"
info "KC_DB_PASSWORD set to POSTGRES_PASSWORD (same database)"

echo
info "Values that must come from outside this script ..."

ensure_prompted KEYCLOAK_ADMIN \
  "Keycloak admin username (console login at the host below)." 0 0

ensure_prompted KC_HOSTNAME \
  "Public hostname Keycloak is served on, e.g. auth.civilytics.in
  Keycloak builds its issuer and redirect URLs from this; a wrong value
  makes tokens fail audience validation." 0 0

ensure_prompted MINIO_ROOT_USER \
  "MinIO root access key (evidence + backup object store)." 0 0

ensure_prompted PUBLIC_API_URL \
  "Browser-facing API URL, e.g. https://civilytics.example/api
  Baked into the frontend bundle at build time." 0 0

ensure_prompted PUBLIC_WS_URL \
  "Browser-facing WebSocket URL, e.g. wss://civilytics.example/ws" 0 0

ensure_prompted PUBLIC_MEDIAMTX_URL \
  "Browser-facing MediaMTX WebRTC URL, e.g. https://civilytics.example/webrtc" 0 0

ensure_prompted WW_KEYCLOAK_CLIENT_SECRET \
  "Keycloak confidential-client secret for the branded login.
  Keycloak generates this -- it cannot be invented here. Create the client
  in the realm, enable 'Direct access grants' (OFF by default on new
  clients in recent Keycloak), then copy Credentials > Client secret." 1 0

echo
info "Optional outbound channels — press Enter to leave disabled ..."
ensure_prompted WW_SMTP_PASSWORD   "SMTP password for report email."            1 1
ensure_prompted WW_TELEGRAM_BOT_TOKEN "Telegram bot token for alert messages."  1 1
ensure_prompted WW_DROPBOX_ACCESS_TOKEN \
  "Dropbox access token for evidence sync.
  Note: prod also runs MinIO (S3-compatible), which is the preferred
  target -- it supports versioning and object lock, which Dropbox does not." 1 1

# Enable a channel only when its credential is actually present, so a
# half-configured feature cannot fail at runtime.
[[ -n "$(env_get WW_SMTP_PASSWORD)"      ]] && env_set WW_SMTP_ENABLED true     || env_set WW_SMTP_ENABLED false
[[ -n "$(env_get WW_TELEGRAM_BOT_TOKEN)" ]] && env_set WW_TELEGRAM_ENABLED true || env_set WW_TELEGRAM_ENABLED false

# SECURITY: this must never be on in prod -- it returns a mock user holding
# owner+admin+viewer and bypasses REST *and* WebSocket auth.
if [[ "$(env_get WW_AUTH_DEV_MODE)" == "1" ]]; then
  warn "WW_AUTH_DEV_MODE was 1 in $ENV_FILE — forcing to 0 for prod."
fi
env_set WW_AUTH_DEV_MODE 0

echo
info "Validating compose config ..."
docker compose -f "$COMPOSE_FILE" config >/dev/null \
  || die "compose config invalid — a required variable is still unset"
info "Config valid."

echo
info "Secrets written to $ENV_FILE (mode $(stat -c '%a' "$ENV_FILE"))"
cat <<'NEXT'

  Manual steps this script cannot do:
    1. Create the Keycloak realm and the confidential client, and enable
       "Direct access grants" on it (off by default on new clients).
    2. Define COMPOSITE roles owner -> admin -> viewer. Keycloak realm
       roles are flat and require_role() matches the exact string, so a
       plain `owner` grant is denied every admin endpoint.
    3. Create the MinIO bucket and set a lifecycle/retention policy.
    4. Back up .env somewhere safe. Losing POSTGRES_PASSWORD with an
       existing data volume means losing access to the database.

NEXT

if [[ $NO_UP -eq 1 ]]; then
  info "--no-up given, not starting."
  exit 0
fi

info "Starting prod ..."
docker compose -f "$COMPOSE_FILE" up -d
echo
docker compose -f "$COMPOSE_FILE" ps
