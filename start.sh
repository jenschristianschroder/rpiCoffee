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
[[ "${LLM_ENABLED:-false}"         == "true" ]] && PROFILES="$PROFILES --profile llm"
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
    docker compose $PROFILES up -d

    # ── Health-check loop ────────────────────────────────────────
    declare -A SVC_PORTS
    [[ "${CLASSIFIER_ENABLED:-false}"  == "true" ]] && SVC_PORTS[classifier]=8001
    [[ "${LLM_ENABLED:-false}"         == "true" ]] && SVC_PORTS[llm]=8000
    [[ "${TTS_ENABLED:-false}"         == "true" ]] && SVC_PORTS[tts]=5050
    [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]] && SVC_PORTS[remote-save]=7000

    for svc in "${!SVC_PORTS[@]}"; do
        port="${SVC_PORTS[$svc]}"
        info "Waiting for $svc (port $port)..."
        TRIES=0
        MAX_TRIES=30
        while ! curl -sf "http://localhost:${port}/health" > /dev/null 2>&1; do
            ((TRIES++))
            if (( TRIES >= MAX_TRIES )); then
                fail "$svc did not become healthy after ${MAX_TRIES}×2s"
                break
            fi
            sleep 2
        done
        if (( TRIES < MAX_TRIES )); then
            ok "$svc healthy"
        fi
    done
else
    info "No Docker services enabled"
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
echo -e "  ${BOLD}Admin UI:${NC}  http://${PI_IP}:8080/admin/"
echo ""

cd app
exec uvicorn main:app --host 0.0.0.0 --port 8080
