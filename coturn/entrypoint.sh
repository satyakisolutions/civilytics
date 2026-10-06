#!/bin/sh
# Substitute secrets into the config at start, so no credential is baked
# into the image or committed to the repo.
set -eu

: "${TURN_USER:?Set TURN_USER}"
: "${TURN_PASSWORD:?Set TURN_PASSWORD}"
: "${TURN_EXTERNAL_IP:?Set TURN_EXTERNAL_IP to the PUBLIC address clients reach}"

sed -e "s|\${TURN_USER}|${TURN_USER}|" \
    -e "s|\${TURN_PASSWORD}|${TURN_PASSWORD}|" \
    -e "s|\${TURN_EXTERNAL_IP}|${TURN_EXTERNAL_IP}|" \
    /etc/coturn/turnserver.conf.template > /tmp/turnserver.conf

exec turnserver -c /tmp/turnserver.conf --no-stdout-log
