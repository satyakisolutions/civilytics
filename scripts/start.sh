#!/bin/bash
# WasteWatch — full stack startup script
# Usage: ./scripts/start.sh [--rebuild]
#
# Brings up all services in the correct order:
# 1. Infrastructure (postgres, redis, kafka, mediamtx, qdrant, keycloak)
# 2. RTSP streamer (camera feeds)
# 3. vLLM (GPU model — takes ~4 min to load)
# 4. Pipeline (DeepStream inference)
# 5. Backend services (API, workers, TTS)
# 6. Frontend dev server

set -e
cd "$(dirname "$0")/.."

REBUILD=""
if [ "$1" = "--rebuild" ]; then
    REBUILD="--build"
    echo "🔨 Rebuilding all images..."
fi

echo "═══════════════════════════════════════════════════"
echo "  WasteWatch | Satyaki Solutions Pvt Ltd"
echo "  Starting full stack..."
echo "═══════════════════════════════════════════════════"

# Step 1: Infrastructure
echo ""
echo "▶ [1/6] Starting infrastructure..."
docker compose up -d $REBUILD postgres redis kafka mediamtx qdrant keycloak
echo "  Waiting for health checks..."
docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list > /dev/null 2>&1 || sleep 5
echo "  ✓ Infrastructure ready"

# Step 2: RTSP streamer
echo ""
echo "▶ [2/6] Starting RTSP camera streams..."
docker compose up -d $REBUILD rtsp-streamer
echo "  ✓ Camera streams publishing"

# Step 3: vLLM (background — takes ~4 min)
echo ""
echo "▶ [3/6] Starting vLLM (Qwen2.5-VL-7B-AWQ)..."
docker compose up -d vlm
echo "  ⏳ Model loading in background (~4 min)"

# Step 4: Pipeline
echo ""
echo "▶ [4/6] Starting DeepStream pipeline..."
docker compose up -d $REBUILD pipeline
echo "  ✓ Pipeline started"

# Step 5: Backend services
echo ""
echo "▶ [5/6] Starting backend services..."
docker compose up -d $REBUILD \
    api \
    vlm-worker \
    tts-worker tts \
    alert-svc evidence-svc \
    lpr-worker log-worker \
    healer health-monitor \
    sip-svc reports
echo "  ✓ All backend services started"

# Step 6: Frontend
echo ""
echo "▶ [6/6] Starting frontend dev server..."
docker compose up -d frontend 2>/dev/null || true
echo "  ✓ Frontend started"

# Wait for vLLM
echo ""
echo "⏳ Waiting for vLLM to finish loading..."
for i in $(seq 1 60); do
    if curl -s http://localhost:8001/health > /dev/null 2>&1; then
        echo "  ✓ vLLM ready!"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "  ⚠ vLLM still loading — check with: docker compose logs vlm"
        break
    fi
    sleep 5
    echo -n "."
done

# Summary
echo ""
echo "═══════════════════════════════════════════════════"
echo "  WasteWatch is running!"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Frontend:  http://localhost:5174"
echo "  API:       http://localhost:8082"
echo "  MediaMTX:  http://localhost:8889"
echo "  vLLM:      http://localhost:8001"
echo ""
echo "  GPU usage: $(nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader 2>/dev/null || echo 'N/A')"
echo ""
docker compose ps --format "table {{.Name}}\t{{.Status}}" 2>&1 | head -30
echo ""
echo "  Powered by Satyaki Solutions Pvt Ltd"
echo "  https://satyaki.co.in"
