#!/usr/bin/env bash
#
# rpiCoffee – Stop all Docker services
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
info() { echo -e "  ${CYAN}▸${NC} $*"; }

echo ""
info "Stopping rpiCoffee..."

# ── Load .env ────────────────────────────────────────────────────
if [[ -f .env ]]; then
    set -a; source .env; set +a
fi

# ── Build profile flags ─────────────────────────────────────────
PROFILES=""
[[ "${CLASSIFIER_ENABLED:-false}"  == "true" ]] && PROFILES="$PROFILES --profile classifier"
[[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]] && PROFILES="$PROFILES --profile llm"
[[ "${TTS_ENABLED:-false}"         == "true" ]] && PROFILES="$PROFILES --profile tts"
[[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]] && PROFILES="$PROFILES --profile remote-save"

# ── Stop Docker services ─────────────────────────────────────────
# shellcheck disable=SC2086
docker compose $PROFILES down
ok "All services stopped"

echo ""
