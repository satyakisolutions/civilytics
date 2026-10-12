#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Civilytics — TLS certificates for the nginx ingress (and coturn).
#
#   ./scripts/make-certs.sh selfsigned [--host NAME] [--ip ADDR]...
#   ./scripts/make-certs.sh letsencrypt --domain civilytics.example --email ops@...
#   ./scripts/make-certs.sh renew
#   ./scripts/make-certs.sh info
#
# WHY TWO MODES
# Let's Encrypt will not issue a certificate for a bare IP address or a
# .local name, and needs inbound port 80 reachable from the internet for
# the HTTP-01 challenge. Municipal on-prem deployments frequently have
# neither. So:
#
#   letsencrypt  public DNS name + port 80 reachable -> trusted, auto-renews
#   selfsigned   anything else -> own CA, install ca.crt on operator machines
#
# WHY TLS IS NOT OPTIONAL HERE
# The app sets session cookies with Secure (WW_AUTH_COOKIE_SECURE=true).
# Over plain HTTP the browser silently discards them, every API call
# arrives anonymous, and because fetchJson falls back to empty data the
# dashboard renders zeros instead of an error. It looks like a quiet night.
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

CERT_DIR="${TLS_CERT_DIR:-./nginx/certs}"
CA_DIR="${CERT_DIR}/ca"
DAYS_SERVER=825      # ~27 months; browsers reject longer-dated leaf certs
DAYS_CA=3650

c_red()  { printf '\033[31m%s\033[0m\n' "$*"; }
c_grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
c_ylw()  { printf '\033[33m%s\033[0m\n' "$*"; }
die()    { c_red "ERROR: $*" >&2; exit 1; }

usage() {
    sed -n '3,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
}

# ── Self-signed: own CA, then a SAN leaf signed by it ─────────────────
# A bare self-signed leaf is rejected outright by modern Chrome. A local
# CA that operators install once works properly, including for WebSockets.
make_selfsigned() {
    local host="" ips=() sans=() i=1
    while [ $# -gt 0 ]; do
        case "$1" in
            --host) host="${2:?--host needs a value}"; shift 2 ;;
            --ip)   ips+=("${2:?--ip needs a value}"); shift 2 ;;
            *)      die "unknown argument: $1" ;;
        esac
    done

    host="${host:-${KC_HOSTNAME:-civilytics.local}}"
    # Default to the host's primary LAN address, since operators usually
    # reach the box by IP on a municipal network.
    if [ ${#ips[@]} -eq 0 ]; then
        local detected
        detected="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)"
        [ -n "$detected" ] && ips+=("$detected")
        ips+=("127.0.0.1")
    fi

    mkdir -p "$CA_DIR" "$CERT_DIR"
    chmod 700 "$CA_DIR"

    # Reuse an existing CA so previously-installed trust keeps working.
    if [ -f "$CA_DIR/ca.key" ] && [ -f "$CA_DIR/ca.crt" ]; then
        c_ylw "Reusing existing CA at $CA_DIR (operators keep their trust)"
    else
        c_grn "Creating CA..."
        openssl req -x509 -nodes -newkey rsa:4096 -days "$DAYS_CA" \
            -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" \
            -subj "/C=IN/O=Satyaki Solutions Pvt Ltd/CN=Civilytics Local CA" 2>/dev/null
        chmod 600 "$CA_DIR/ca.key"
    fi

    sans+=("DNS:${host}")
    sans+=("DNS:localhost")
    for a in "${ips[@]}"; do sans+=("IP:${a}"); done
    local san_str
    san_str="$(IFS=,; echo "${sans[*]}")"

    c_grn "Issuing server certificate"
    echo "  CN   : ${host}"
    echo "  SANs : ${san_str}"

    openssl req -nodes -newkey rsa:2048 \
        -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/server.csr" \
        -subj "/C=IN/O=Satyaki Solutions Pvt Ltd/CN=${host}" 2>/dev/null

    # SANs must be in the signed cert, not just the CSR: browsers ignore
    # CN entirely and validate against subjectAltName only.
    cat > "$CERT_DIR/server.ext" <<EXT
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=${san_str}
EXT

    openssl x509 -req -in "$CERT_DIR/server.csr" \
        -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
        -out "$CERT_DIR/fullchain.pem" -days "$DAYS_SERVER" \
        -extfile "$CERT_DIR/server.ext" 2>/dev/null

    # nginx wants the chain; append the CA so clients that have it only in
    # the store still get a complete path.
    cat "$CA_DIR/ca.crt" >> "$CERT_DIR/fullchain.pem"

    rm -f "$CERT_DIR/server.csr" "$CERT_DIR/server.ext"
    chmod 644 "$CERT_DIR/fullchain.pem"
    chmod 600 "$CERT_DIR/privkey.pem"

    c_grn "Done -> $CERT_DIR/{fullchain.pem,privkey.pem}"
    echo
    c_ylw "Install $CA_DIR/ca.crt on every operator machine, or browsers will warn:"
    echo "  Linux : sudo cp $CA_DIR/ca.crt /usr/local/share/ca-certificates/civilytics.crt && sudo update-ca-certificates"
    echo "  macOS : sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain $CA_DIR/ca.crt"
    echo "  Win   : certutil -addstore -f ROOT ca.crt   (as Administrator)"
    echo "  Chrome/Firefox keep their own stores — import there too if needed."
}

# ── Let's Encrypt via certbot in a container ──────────────────────────
make_letsencrypt() {
    local domain="" email="" staging=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --domain)  domain="${2:?}"; shift 2 ;;
            --email)   email="${2:?}";  shift 2 ;;
            --staging) staging="--staging"; shift ;;
            *) die "unknown argument: $1" ;;
        esac
    done
    [ -n "$domain" ] || die "--domain is required"
    [ -n "$email" ]  || die "--email is required (Let's Encrypt expiry notices)"

    case "$domain" in
        *.local|*.localdomain|localhost)
            die "Let's Encrypt cannot issue for '$domain'. Use: selfsigned --host $domain" ;;
    esac
    if printf '%s' "$domain" | grep -qE '^[0-9]+(\.[0-9]+){3}$'; then
        die "Let's Encrypt cannot issue for a bare IP. Use: selfsigned --ip $domain"
    fi

    command -v docker >/dev/null || die "docker is required"

    c_ylw "HTTP-01 challenge needs inbound TCP/80 on $domain to reach this host."
    c_ylw "Stopping the nginx container for the duration (standalone mode binds :80)."

    mkdir -p "$CERT_DIR" ./nginx/letsencrypt

    docker compose -f docker-compose.yml --profile prod stop nginx 2>/dev/null || true

    docker run --rm \
        -p 80:80 \
        -v "$PWD/nginx/letsencrypt:/etc/letsencrypt" \
        certbot/certbot certonly --standalone $staging \
        --non-interactive --agree-tos \
        --email "$email" -d "$domain" \
        || { c_red "certbot failed — leaving any existing certificate in place"; exit 1; }

    # Copy rather than symlink: the nginx container mounts CERT_DIR and
    # cannot follow a symlink out of it.
    cp "./nginx/letsencrypt/live/$domain/fullchain.pem" "$CERT_DIR/fullchain.pem"
    cp "./nginx/letsencrypt/live/$domain/privkey.pem"   "$CERT_DIR/privkey.pem"
    chmod 644 "$CERT_DIR/fullchain.pem"
    chmod 600 "$CERT_DIR/privkey.pem"

    c_grn "Done -> $CERT_DIR/{fullchain.pem,privkey.pem}"
    echo
    c_ylw "Let's Encrypt certificates last 90 days. Add a renewal cron:"
    echo "  0 3 * * 1  cd $PWD && ./scripts/make-certs.sh renew >> /var/log/civilytics-certs.log 2>&1"
}

renew() {
    [ -d ./nginx/letsencrypt ] || die "no Let's Encrypt state found; nothing to renew"
    docker compose -f docker-compose.yml --profile prod stop nginx 2>/dev/null || true
    docker run --rm -p 80:80 \
        -v "$PWD/nginx/letsencrypt:/etc/letsencrypt" \
        certbot/certbot renew
    # certbot exits 0 when nothing was due, so copy unconditionally.
    local live
    live="$(find ./nginx/letsencrypt/live -mindepth 1 -maxdepth 1 -type d | head -1)"
    if [ -n "$live" ]; then
        cp "$live/fullchain.pem" "$CERT_DIR/fullchain.pem"
        cp "$live/privkey.pem"   "$CERT_DIR/privkey.pem"
        chmod 644 "$CERT_DIR/fullchain.pem"; chmod 600 "$CERT_DIR/privkey.pem"
    fi
    docker compose -f docker-compose.yml --profile prod up -d nginx 2>/dev/null || true
    c_grn "Renewal pass complete"
}

info() {
    [ -f "$CERT_DIR/fullchain.pem" ] || die "no certificate at $CERT_DIR/fullchain.pem"
    openssl x509 -in "$CERT_DIR/fullchain.pem" -noout \
        -subject -issuer -dates -ext subjectAltName
    local end epoch now days
    end="$(openssl x509 -in "$CERT_DIR/fullchain.pem" -noout -enddate | cut -d= -f2)"
    epoch="$(date -d "$end" +%s 2>/dev/null || echo 0)"
    now="$(date +%s)"
    if [ "$epoch" -gt 0 ]; then
        days=$(( (epoch - now) / 86400 ))
        if [ "$days" -lt 0 ];      then c_red  "EXPIRED ${days#-} days ago"
        elif [ "$days" -lt 14 ];   then c_ylw  "Expires in $days days — renew now"
        else                            c_grn  "Valid for $days more days"
        fi
    fi
}

case "${1:-}" in
    selfsigned)   shift; make_selfsigned "$@" ;;
    letsencrypt)  shift; make_letsencrypt "$@" ;;
    renew)        renew ;;
    info)         info ;;
    *)            usage ;;
esac
