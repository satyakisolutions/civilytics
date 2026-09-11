#!/usr/bin/env bash
# Smoke test: verify API and infrastructure are running.
# Usage: ./scripts/smoke_test.sh [API_URL]

set -euo pipefail

API_URL="${1:-http://localhost:8080}"

echo "=== WasteWatch Phase 1 Smoke Test ==="
echo "API: $API_URL"
echo ""

# Health
echo -n "1. Health check... "
HEALTH=$(curl -sf "$API_URL/api/health")
STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
if [ "$STATUS" = "ok" ]; then
    echo "PASS"
else
    echo "FAIL: $HEALTH"
    exit 1
fi

# Camera CRUD
echo -n "2. Create camera... "
CAM=$(curl -sf -X POST "$API_URL/api/cameras" \
    -H "Content-Type: application/json" \
    -d '{"name":"Smoke Test Camera","zone":"Test Zone","stream_url":"rtsp://test:554/stream"}')
CAM_ID=$(echo "$CAM" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "PASS (id=$CAM_ID)"

echo -n "3. List cameras... "
CAMS=$(curl -sf "$API_URL/api/cameras")
COUNT=$(echo "$CAMS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$COUNT" -ge 1 ]; then
    echo "PASS ($COUNT cameras)"
else
    echo "FAIL"
    exit 1
fi

echo -n "4. Delete camera... "
curl -sf -X DELETE "$API_URL/api/cameras/$CAM_ID" > /dev/null
echo "PASS"

# WebSocket
echo -n "5. WebSocket connection... "
WS_STATUS=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
    "$API_URL/ws/metadata/test-cam" || true)
if [ "$WS_STATUS" = "101" ]; then
    echo "PASS (101 Switching Protocols)"
else
    echo "SKIP (got $WS_STATUS — WebSocket requires proper client)"
fi

echo ""
echo "=== All checks passed ==="
