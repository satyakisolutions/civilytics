#!/usr/bin/env bash
# Login flow test against a running stack's real Keycloak-backed auth
# (not WW_AUTH_DEV_MODE). Assumes keycloak/realm-test.json's seeded
# owner/owner-test-pass user -- that's what makes this runnable without
# any manual Keycloak setup (see keycloak/realm-test.json).
#
# Usage: ./scripts/test_login_container.sh [API_URL]
#   API_URL defaults to the test profile's offset port (9082).

set -euo pipefail

API_URL="${1:-http://localhost:9082}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

echo "=== Login container test ==="
echo "API: $API_URL"
echo ""

echo -n "1. Login with valid credentials (owner/owner-test-pass)... "
RESP=$(curl -sf -c "$COOKIE_JAR" -X POST "$API_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"owner","password":"owner-test-pass"}')
ROLES=$(echo "$RESP" | python3 -c "import sys,json; print(','.join(json.load(sys.stdin)['roles']))")
if echo "$ROLES" | grep -q "owner"; then
    echo "PASS (roles=$ROLES)"
else
    echo "FAIL: unexpected roles: $ROLES"
    exit 1
fi

echo -n "2. Session cookie set (httpOnly ww_access)... "
if grep -q "ww_access" "$COOKIE_JAR"; then
    echo "PASS"
else
    echo "FAIL: no ww_access cookie in jar"
    exit 1
fi

echo -n "3. /api/auth/me authenticates via cookie... "
ME=$(curl -sf -b "$COOKIE_JAR" "$API_URL/api/auth/me")
ME_USER=$(echo "$ME" | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")
if [ "$ME_USER" = "owner" ]; then
    echo "PASS"
else
    echo "FAIL: /api/auth/me returned username=$ME_USER"
    exit 1
fi

echo -n "4. Login with wrong password is rejected... "
BAD_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"owner","password":"not-the-password"}')
if [ "$BAD_STATUS" = "401" ]; then
    echo "PASS (401)"
else
    echo "FAIL: expected 401, got $BAD_STATUS"
    exit 1
fi

echo -n "5. Logout clears the session... "
curl -sf -b "$COOKIE_JAR" -c "$COOKIE_JAR" -X POST "$API_URL/api/auth/logout" > /dev/null
POST_LOGOUT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "$API_URL/api/auth/me")
if [ "$POST_LOGOUT_STATUS" = "401" ]; then
    echo "PASS (401 after logout)"
else
    echo "FAIL: expected 401 after logout, got $POST_LOGOUT_STATUS"
    exit 1
fi

echo ""
echo "=== Login container test: all checks passed ==="
