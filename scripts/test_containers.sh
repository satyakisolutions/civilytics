#!/usr/bin/env bash
# Container-based automation entrypoint for login + DP(api) container tests.
# Brings up the disposable `test` compose profile (offset ports, own
# volumes -- see scripts/start.sh), runs test_login_container.sh and
# test_dp_container.sh against it, then tears it down.
#
# Usage: ./scripts/test_containers.sh [--keep-up]
#   --keep-up   leave the test stack running afterward (for debugging)
#   --down-only just tear down an already-running test stack and exit

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

API_URL="http://localhost:9082"
PROJECT="civilytics-test"
KEEP_UP=0

for arg in "$@"; do
    case "$arg" in
        --keep-up) KEEP_UP=1 ;;
        --down-only) ./scripts/start.sh test --down; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

cleanup() {
    if [ "$KEEP_UP" -eq 0 ]; then
        echo ""
        echo "Tearing down test stack..."
        ./scripts/start.sh test --down
    else
        echo ""
        echo "--keep-up set: leaving test stack running (./scripts/start.sh test --down to stop it)"
    fi
}
trap cleanup EXIT

echo "Starting test stack (this seeds keycloak/realm-test.json's owner user)..."
./scripts/start.sh test

echo ""
./scripts/test_login_container.sh "$API_URL"
echo ""
./scripts/test_dp_container.sh "$API_URL" "$PROJECT"

echo ""
echo "=== All container tests passed ==="
