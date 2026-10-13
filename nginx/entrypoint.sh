#!/bin/sh
# Render the config, then make sure a certificate exists before nginx
# starts -- nginx exits immediately on a missing ssl_certificate, which
# looks like a crash loop rather than a configuration problem.
set -eu

CERT_DIR=/etc/nginx/certs

# NGINX_DOCS_HOST gates the /docs route to a single Host (the LAN IP);
# defaults to the LAN address the stack is reached by.
sed -e "s/\${NGINX_SERVER_NAME}/${NGINX_SERVER_NAME:-_}/" \
    -e "s/\${NGINX_DOCS_HOST}/${NGINX_DOCS_HOST:-192.168.1.8}/" \
    /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

if [ ! -f "$CERT_DIR/fullchain.pem" ] || [ ! -f "$CERT_DIR/privkey.pem" ]; then
    echo "WARNING: no TLS certificate in $CERT_DIR — generating a SELF-SIGNED one."
    echo "WARNING: browsers will show a security warning. Mount a real"
    echo "WARNING: certificate at $CERT_DIR for production."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "$CERT_DIR/privkey.pem" \
        -out "$CERT_DIR/fullchain.pem" \
        -subj "/CN=${NGINX_SERVER_NAME:-civilytics.local}" 2>/dev/null
fi

nginx -t
exec nginx -g 'daemon off;'
