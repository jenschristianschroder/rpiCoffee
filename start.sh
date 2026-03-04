#!/usr/bin/env bash
#
# rpiCoffee – Start all services and the application
#
# Docker services start first, then the native app launches
# after health checks pass.  Ctrl+C stops everything cleanly.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${CYAN}▸${NC} $*"; }

# ── Load .env ────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    echo "No .env found. Run ./setup.sh first."
    exit 1
fi
set -a; source .env; set +a

# ── Build profile flags ─────────────────────────────────────────
PROFILES=""
[[ "${CLASSIFIER_ENABLED:-false}"  == "true" ]] && PROFILES="$PROFILES --profile classifier"
[[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]] && PROFILES="$PROFILES --profile llm"
[[ "${TTS_ENABLED:-false}"         == "true" ]] && PROFILES="$PROFILES --profile tts"
[[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]] && PROFILES="$PROFILES --profile remote-save"

# ── Cleanup trap ─────────────────────────────────────────────────
cleanup() {
    echo ""
    info "Shutting down..."
    # Stop app (uvicorn) — the exec below replaces this shell,
    # so this trap fires if the user Ctrl-Cs or the process dies.
    # shellcheck disable=SC2086
    docker compose $PROFILES down 2>/dev/null || true
    info "Docker services stopped"
}
trap cleanup EXIT INT TERM

# ── Start Docker services ────────────────────────────────────────
echo ""
echo -e "${BOLD}Starting rpiCoffee...${NC}"
echo ""

if [[ -n "$PROFILES" ]]; then
    info "Starting Docker services..."
    # shellcheck disable=SC2086
    docker compose $PROFILES up -d --build

    # ── Health-check loop (Docker-managed services only) ────────
    declare -A SVC_HEALTH
    [[ "${CLASSIFIER_ENABLED:-false}"  == "true" ]] && SVC_HEALTH[classifier]="${CLASSIFIER_ENDPOINT:-http://localhost:8001}/health"
    [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]] && SVC_HEALTH[llm]="${LLM_ENDPOINT:-http://localhost:8002}/health"
    [[ "${TTS_ENABLED:-false}"         == "true" ]] && SVC_HEALTH[tts]="${TTS_ENDPOINT:-http://localhost:5050}/health"
    [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]] && SVC_HEALTH[remote-save]="${REMOTE_SAVE_ENDPOINT:-http://localhost:7000}/health"

    for svc in "${!SVC_HEALTH[@]}"; do
        url="${SVC_HEALTH[$svc]}"
        echo -n "  Waiting for $svc "
        TRIES=0
        MAX_TRIES=60
        while ! curl -sf --max-time 2 "$url" > /dev/null 2>&1; do
            ((TRIES++))
            if (( TRIES >= MAX_TRIES )); then
                echo ""
                fail "$svc did not become healthy after ${MAX_TRIES}×2s"
                break
            fi
            echo -n "."
            sleep 2
        done
        if (( TRIES < MAX_TRIES )); then
            echo ""
            ok "$svc healthy"
        fi
    done
else
    info "No Docker services enabled"
fi

# ── External service health checks (not Docker-managed) ─────────
if [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
    # Ensure hailo-ollama service is started
    if systemctl is-active rpicoffee-hailo-ollama &>/dev/null 2>&1; then
        ok "hailo-ollama service already running"
    else
        info "Starting hailo-ollama service..."
        sudo systemctl start rpicoffee-hailo-ollama 2>/dev/null || true
    fi

    LLM_URL="${LLM_OLLAMA_ENDPOINT:-http://localhost:8000}"
    echo -n "  Waiting for ollama (${LLM_URL}) "
    TRIES=0; MAX_TRIES=30
    while ! curl -sf --max-time 2 "${LLM_URL}/api/tags" > /dev/null 2>&1; do
        ((TRIES++))
        if (( TRIES >= MAX_TRIES )); then
            echo ""
            fail "ollama did not respond at ${LLM_URL}/api/tags after ${MAX_TRIES}×2s"
            fail "Make sure hailo-ollama is running on the device"
            break
        fi
        echo -n "."
        sleep 2
    done
    if (( TRIES < MAX_TRIES )); then
        echo ""
        ok "ollama healthy (${LLM_URL})"
    fi
fi

# ── Start app natively ───────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    fail ".venv not found — run ./setup.sh first"
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PI_IP="${PI_IP:-localhost}"

echo ""
ok "All services up — starting application"
echo ""
echo -e "  ${BOLD}Admin UI:${NC}  http://${PI_IP}:${APP_PORT:-8080}/admin/"
echo ""

cd app
exec uvicorn main:app --host 0.0.0.0 --port "${APP_PORT:-8080}"
