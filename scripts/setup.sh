#!/usr/bin/env bash
# ==========================================================================
#  WasteWatch — Full Setup Script
#  Satyaki Solutions Pvt Ltd | https://satyaki.co.in
#  "Creating Innovative Impulse"
#
#  Sets up everything from scratch:
#    1. System dependencies (Python, Node, Docker, NVIDIA)
#    2. Backend (Python venv, pip install, Alembic migrations)
#    3. Frontend (npm install, build)
#    4. AI Models (ONNX export, TensorRT engine build)
#    5. Infrastructure (Docker Compose services)
#    6. Validation (smoke test)
# ==========================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}/.."
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
GOLD='\033[0;33m'
NC='\033[0m'
BOLD='\033[1m'

log()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[FAIL]${NC}  $*"; }
step() { echo -e "\n${GOLD}${BOLD}━━━ $* ━━━${NC}"; }

banner() {
    echo -e "${GOLD}"
    echo "  ╔══════════════════════════════════════════════════╗"
    echo "  ║         WasteWatch Platform Setup                ║"
    echo "  ║         Satyaki Solutions Pvt Ltd                 ║"
    echo "  ║         Creating Innovative Impulse               ║"
    echo "  ╚══════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Track what was done for final summary
declare -a SUMMARY=()
summary_add() { SUMMARY+=("$1"); }

# ==========================================================================
# Phase 0: Pre-checks
# ==========================================================================

banner

step "Phase 0: System Pre-checks"

# OS
log "OS: $(uname -s) $(uname -r)"

# Python
if command -v python3 &>/dev/null; then
    PYTHON_VER=$(python3 --version 2>&1)
    ok "Python: $PYTHON_VER"
else
    err "Python 3 not found. Install: sudo apt install python3 python3-pip python3-venv"
    exit 1
fi

# Node.js
if command -v node &>/dev/null; then
    ok "Node.js: $(node --version)"
else
    warn "Node.js not found — frontend build will be skipped"
    warn "Install: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
fi

# Docker
if command -v docker &>/dev/null; then
    ok "Docker: $(docker --version | cut -d' ' -f3)"
else
    warn "Docker not found — infrastructure services will be skipped"
fi

# Docker Compose
if docker compose version &>/dev/null 2>&1; then
    ok "Docker Compose: $(docker compose version --short 2>/dev/null)"
else
    warn "Docker Compose not found"
fi

# NVIDIA GPU
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
    ok "GPU: $GPU_NAME ($GPU_MEM)"
    HAS_GPU=true
else
    warn "nvidia-smi not found — GPU features disabled"
    HAS_GPU=false
fi

# NVIDIA Container Toolkit
if command -v nvidia-container-cli &>/dev/null || docker info 2>/dev/null | grep -q nvidia; then
    ok "NVIDIA Container Toolkit: available"
else
    warn "NVIDIA Container Toolkit not detected — GPU in Docker may not work"
    warn "Install: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
fi

# trtexec
if command -v trtexec &>/dev/null; then
    ok "trtexec: available (TensorRT engines will be built on host)"
    HAS_TRTEXEC=true
else
    warn "trtexec not found — TensorRT engines will be built inside Docker on first run"
    HAS_TRTEXEC=false
fi

# ==========================================================================
# Phase 1: Backend Setup
# ==========================================================================

step "Phase 1: Backend Setup"

cd "$PROJECT_ROOT/backend"

# Create virtual environment
if [ ! -d ".venv" ]; then
    log "Creating Python virtual environment..."
    python3 -m venv .venv
    ok "Virtual environment created"
else
    ok "Virtual environment exists"
fi

# Activate venv
source .venv/bin/activate

# Install dependencies
log "Installing backend dependencies..."
if command -v uv &>/dev/null; then
    uv pip install -e ".[dev]" 2>&1 | tail -3
else
    pip install -e ".[dev]" 2>&1 | tail -3
fi
ok "Backend dependencies installed"
summary_add "Backend: Python deps installed"

# .env file
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    if [ -f "$PROJECT_ROOT/.env.example" ]; then
        cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
        warn "Created .env from .env.example — edit with your credentials"
        summary_add "Config: .env created from example — EDIT REQUIRED"
    fi
else
    ok ".env file exists"
fi

deactivate

# ==========================================================================
# Phase 2: Frontend Setup
# ==========================================================================

step "Phase 2: Frontend Setup"

cd "$PROJECT_ROOT/frontend"

if command -v node &>/dev/null; then
    log "Installing frontend dependencies..."
    if command -v bun &>/dev/null; then
        bun install 2>&1 | tail -3
    else
        npm install 2>&1 | tail -3
    fi
    ok "Frontend dependencies installed"
    summary_add "Frontend: Node deps installed"

    log "Building frontend..."
    if command -v bun &>/dev/null; then
        bun run build 2>&1 | tail -3
    else
        npm run build 2>&1 | tail -3
    fi
    ok "Frontend built"
    summary_add "Frontend: Production build complete"
else
    warn "Skipping frontend — Node.js not installed"
    summary_add "Frontend: SKIPPED (no Node.js)"
fi

# ==========================================================================
# Phase 3: AI Model Setup
# ==========================================================================

step "Phase 3: AI Model Setup"

cd "$PROJECT_ROOT"

MODELS_DIR="$PROJECT_ROOT/pipeline/models"
mkdir -p "$MODELS_DIR"/{rfdetr,action_classifier,piper-tts,paddleocr}

# --- Piper TTS (direct download, no PyTorch needed) ---
log "Piper TTS model..."
PIPER_DIR="$MODELS_DIR/piper-tts"
PIPER_MODEL="en_US-lessac-medium"
PIPER_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

if [ -f "$PIPER_DIR/${PIPER_MODEL}.onnx" ]; then
    ok "Piper TTS: already downloaded ($(du -h "$PIPER_DIR/${PIPER_MODEL}.onnx" | cut -f1))"
else
    log "Downloading Piper TTS model..."
    curl -L --progress-bar -o "$PIPER_DIR/${PIPER_MODEL}.onnx" \
        "${PIPER_BASE}/${PIPER_MODEL}.onnx"
    curl -sL -o "$PIPER_DIR/${PIPER_MODEL}.onnx.json" \
        "${PIPER_BASE}/${PIPER_MODEL}.onnx.json"
    ok "Piper TTS downloaded ($(du -h "$PIPER_DIR/${PIPER_MODEL}.onnx" | cut -f1))"
fi
summary_add "Model: Piper TTS (en_US-lessac-medium)"

# --- RF-DETR ONNX Export ---
log "RF-DETR object detection model..."
RFDETR_DIR="$MODELS_DIR/rfdetr"

if [ -f "$RFDETR_DIR/rfdetr.onnx" ]; then
    ok "RF-DETR ONNX: already exported ($(du -h "$RFDETR_DIR/rfdetr.onnx" | cut -f1))"
else
    log "Exporting RF-DETR to ONNX (requires PyTorch + rfdetr)..."
    if python3 -c "import rfdetr" 2>/dev/null; then
        python3 "$PROJECT_ROOT/scripts/export_rfdetr_onnx.py" 2>&1 | tail -5
        if [ -f "$RFDETR_DIR/rfdetr.onnx" ]; then
            ok "RF-DETR ONNX exported ($(du -h "$RFDETR_DIR/rfdetr.onnx" | cut -f1))"
        else
            err "RF-DETR ONNX export failed"
        fi
    else
        warn "rfdetr package not installed — skipping ONNX export"
        warn "Install: pip install torch torchvision rfdetr onnx onnxsim"
        warn "Or: engines will be built inside DeepStream Docker container"
    fi
fi

# --- Action Classifier ONNX Export ---
log "Action classifier model..."
ACTION_DIR="$MODELS_DIR/action_classifier"

if [ -f "$ACTION_DIR/action_classifier.onnx" ]; then
    ok "Action classifier ONNX: already exported ($(du -h "$ACTION_DIR/action_classifier.onnx" | cut -f1))"
else
    log "Exporting action classifier to ONNX (requires PyTorch)..."
    if python3 -c "import torch" 2>/dev/null; then
        python3 "$PROJECT_ROOT/scripts/export_action_classifier_onnx.py" 2>&1 | tail -5
        if [ -f "$ACTION_DIR/action_classifier.onnx" ]; then
            ok "Action classifier ONNX exported ($(du -h "$ACTION_DIR/action_classifier.onnx" | cut -f1))"
        else
            err "Action classifier ONNX export failed"
        fi
    else
        warn "PyTorch not installed — skipping action classifier export"
        warn "Pipeline will run without action recognition (dwell-only detection)"
    fi
fi

# --- TensorRT Engine Build (if trtexec available) ---
if [ "$HAS_TRTEXEC" = true ] && [ "$HAS_GPU" = true ]; then
    log "Building TensorRT FP16 engines..."

    if [ -f "$RFDETR_DIR/rfdetr.onnx" ] && [ ! -f "$RFDETR_DIR/rfdetr_fp16.engine" ]; then
        log "Building RF-DETR TensorRT engine (this takes ~5 minutes)..."
        bash "$PROJECT_ROOT/scripts/build_tensorrt_engine.sh" 2>&1 | grep -E "(Building|Serializ|Total|error)" || true
        if [ -f "$RFDETR_DIR/rfdetr_fp16.engine" ]; then
            ok "RF-DETR engine built ($(du -h "$RFDETR_DIR/rfdetr_fp16.engine" | cut -f1))"
            summary_add "Model: RF-DETR TensorRT FP16 engine"
        else
            err "RF-DETR engine build failed"
        fi
    elif [ -f "$RFDETR_DIR/rfdetr_fp16.engine" ]; then
        ok "RF-DETR engine: already built ($(du -h "$RFDETR_DIR/rfdetr_fp16.engine" | cut -f1))"
    fi

    if [ -f "$ACTION_DIR/action_classifier.onnx" ] && [ ! -f "$ACTION_DIR/action_classifier_fp16.engine" ]; then
        log "Building action classifier TensorRT engine (~2 minutes)..."
        bash "$PROJECT_ROOT/scripts/build_action_engine.sh" 2>&1 | grep -E "(Building|Serializ|Total|error)" || true
        if [ -f "$ACTION_DIR/action_classifier_fp16.engine" ]; then
            ok "Action classifier engine built ($(du -h "$ACTION_DIR/action_classifier_fp16.engine" | cut -f1))"
            summary_add "Model: Action Classifier TensorRT FP16 engine"
        else
            err "Action classifier engine build failed"
        fi
    elif [ -f "$ACTION_DIR/action_classifier_fp16.engine" ]; then
        ok "Action classifier engine: already built"
    fi
else
    warn "TensorRT engines will be auto-built inside Docker on first pipeline start"
    summary_add "Model: TensorRT engines deferred to Docker"
fi

# --- PaddleOCR ---
log "PaddleOCR (License Plate Recognition)..."
log "PaddleOCR downloads models automatically on first use"
ok "PaddleOCR directory ready"

# ==========================================================================
# Phase 4: Infrastructure (Docker Compose)
# ==========================================================================

step "Phase 4: Infrastructure Services"

cd "$PROJECT_ROOT"

if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
    log "Pulling Docker images..."
    docker compose pull 2>&1 | tail -10
    ok "Docker images pulled"
    summary_add "Docker: Images pulled"

    log "Starting infrastructure services (Kafka, Redis, MediaMTX, Keycloak, Qdrant)..."
    docker compose up -d kafka redis mediamtx keycloak qdrant 2>&1 | tail -5
    ok "Infrastructure services started"
    summary_add "Docker: Kafka + Redis + MediaMTX + Keycloak + Qdrant running"

    # Wait for Kafka to be healthy
    log "Waiting for Kafka to be ready..."
    for i in $(seq 1 30); do
        if docker compose exec -T kafka /opt/kafka/bin/kafka-broker-api-versions.sh \
            --bootstrap-server localhost:9092 &>/dev/null; then
            ok "Kafka is ready"
            break
        fi
        sleep 2
        if [ "$i" -eq 30 ]; then
            warn "Kafka not ready after 60s — may need manual check"
        fi
    done
else
    warn "Skipping Docker services — Docker/Compose not available"
    summary_add "Docker: SKIPPED"
fi

# ==========================================================================
# Phase 5: Database Migration
# ==========================================================================

step "Phase 5: Database Migration"

cd "$PROJECT_ROOT/backend"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    if python3 -c "from app.config.settings import settings; assert settings.database_url" 2>/dev/null; then
        log "Running Alembic migrations..."
        alembic upgrade head 2>&1 | tail -5
        ok "Database migrated"
        summary_add "Database: Alembic migrations applied"
    else
        warn "WW_DATABASE_URL not set — skipping migrations"
        warn "Set in .env: WW_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/wastewatch"
        summary_add "Database: SKIPPED (no DATABASE_URL)"
    fi
    deactivate
fi

# ==========================================================================
# Phase 6: Validation
# ==========================================================================

step "Phase 6: Validation"

cd "$PROJECT_ROOT"

check_file() {
    if [ -f "$1" ]; then
        ok "$2 ($(du -h "$1" 2>/dev/null | cut -f1))"
        return 0
    else
        warn "$2 — MISSING"
        return 1
    fi
}

log "Checking model files..."
check_file "$MODELS_DIR/piper-tts/${PIPER_MODEL}.onnx"          "Piper TTS ONNX"
check_file "$MODELS_DIR/rfdetr/rfdetr.onnx"                     "RF-DETR ONNX" || true
check_file "$MODELS_DIR/rfdetr/rfdetr_fp16.engine"              "RF-DETR TensorRT" || true
check_file "$MODELS_DIR/action_classifier/action_classifier.onnx"          "Action Classifier ONNX" || true
check_file "$MODELS_DIR/action_classifier/action_classifier_fp16.engine"   "Action Classifier TensorRT" || true

log "Checking config files..."
check_file "pipeline/configs/nvinfer_rfdetr.txt"        "RF-DETR nvinfer config"
check_file "pipeline/configs/nvinfer_action_sgie.txt"   "Action SGIE config"
check_file "pipeline/configs/labels.txt"                "COCO labels (80 classes)"
check_file "pipeline/configs/action_labels.txt"         "Action labels (8 classes)"
check_file "pipeline/configs/nvtracker_bytetrack.yml"   "ByteTrack tracker config"
check_file "pipeline/configs/smart_record.json"         "Smart recording config"
check_file "mediamtx.yml"                               "MediaMTX config"

log "Checking Docker services..."
if command -v docker &>/dev/null; then
    for svc in kafka redis mediamtx keycloak qdrant; do
        if docker compose ps --status running 2>/dev/null | grep -q "$svc"; then
            ok "Service: $svc running"
        else
            warn "Service: $svc not running"
        fi
    done
fi

# ==========================================================================
# Summary
# ==========================================================================

echo ""
echo -e "${GOLD}${BOLD}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║              Setup Complete                      ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

for item in "${SUMMARY[@]}"; do
    echo -e "  ${GREEN}✓${NC} $item"
done

echo ""
echo -e "${BOLD}Next steps:${NC}"
echo "  1. Edit .env with your credentials (database, Keycloak, Telegram)"
echo "  2. Start all services:"
echo "       docker compose up -d"
echo "  3. Start backend (dev mode):"
echo "       cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8080"
echo "  4. Start frontend (dev mode):"
echo "       cd frontend && npm run dev"
echo "  5. Open dashboard: http://localhost:5173"
echo "  6. Keycloak admin: http://localhost:8180 (admin / \$KEYCLOAK_ADMIN_PASSWORD)"
echo ""
echo -e "  ${GOLD}Satyaki Solutions Pvt Ltd${NC} | https://satyaki.co.in"
echo ""
