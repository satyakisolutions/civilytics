#!/usr/bin/env bash
# DP (api) container health test -- verifies the container Docker itself
# considers healthy, and that /api/health agrees from inside the request
# path a real client takes (not just the container's internal healthcheck
# command, which curls localhost:8080 from inside the container).
#
# Usage: ./scripts/test_dp_container.sh [API_URL] [COMPOSE_PROJECT]
#   API_URL defaults to the test profile's offset port (9082).
#   COMPOSE_PROJECT defaults to civilytics-test (see scripts/start.sh).

set -euo pipefail

API_URL="${1:-http://localhost:9082}"
PROJECT="${2:-civilytics-test}"

echo "=== DP (api) container test ==="
echo "API: $API_URL"
echo "Compose project: $PROJECT"
echo ""

echo -n "1. Container 'api' is running... "
STATE=$(docker inspect -f '{{.State.Status}}' "${PROJECT}-api-1" 2>/dev/null || echo "missing")
if [ "$STATE" = "running" ]; then
    echo "PASS"
else
    echo "FAIL: container state is '$STATE' (expected running)"
    exit 1
fi

echo -n "2. Docker healthcheck reports healthy... "
HEALTH=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${PROJECT}-api-1")
if [ "$HEALTH" = "healthy" ]; then
    echo "PASS"
else
    echo "FAIL: docker healthcheck status is '$HEALTH'"
    exit 1
fi

echo -n "3. /api/health reachable from outside the container... "
HEALTH_RESP=$(curl -sf "$API_URL/api/health")
STATUS=$(echo "$HEALTH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
if [ "$STATUS" = "ok" ]; then
    echo "PASS"
else
    echo "FAIL: /api/health returned status=$STATUS"
    exit 1
fi

echo -n "4. Restart policy is set (survives a crash)... "
POLICY=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "${PROJECT}-api-1")
if [ -n "$POLICY" ] && [ "$POLICY" != "no" ]; then
    echo "PASS ($POLICY)"
else
    echo "SKIP: restart policy is '$POLICY' (expected for the test profile, see docker-compose.yml RESTART_POLICY)"
fi

echo ""
echo "=== DP container test: all checks passed ==="
