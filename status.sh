#!/usr/bin/env bash
#
# rpiCoffee – Health & status dashboard
#
# Checks all services, Docker containers, systemd units, and sensor
# and prints a summary table with health, IP, port, and uptime.
#
# Usage:
#   ./status.sh          # coloured table
#   ./status.sh --json   # machine-readable JSON output
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colours ──────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

JSON_MODE=false
[[ "${1:-}" == "--json" ]] && JSON_MODE=true

# ── Load configuration (mirrors app/config.py layering) ──────────
# Layer 1: .env file (base)
if [[ -f .env ]]; then
    set -a; source .env; set +a
else
    echo -e "${RED}No .env found — run ./setup.sh first.${NC}"
    exit 1
fi

# Layer 2: data/settings.json overrides (admin panel persisted settings)
# These take priority over .env, matching the app's ConfigManager behaviour.
SETTINGS_FILE="${SCRIPT_DIR}/data/settings.json"
if [[ -f "$SETTINGS_FILE" ]] && command -v python3 &>/dev/null; then
    eval "$(python3 -c "
import json, sys, shlex
with open(sys.argv[1]) as f:
    settings = json.load(f)
# Only export config keys the status script cares about
keys = [
    'CLASSIFIER_ENABLED', 'CLASSIFIER_ENDPOINT',
    'LLM_ENABLED', 'LLM_BACKEND', 'LLM_ENDPOINT', 'LLM_MODEL',
    'TTS_ENABLED', 'TTS_ENDPOINT',
    'REMOTE_SAVE_ENABLED', 'REMOTE_SAVE_ENDPOINT',
    'SENSOR_MODE', 'SENSOR_DEVICE_ID', 'SENSOR_SERIAL_PORT',
    'SENSOR_AUTO_TRIGGER',
]
for k in keys:
    if k in settings:
        v = settings[k]
        # Normalise booleans to true/false strings for bash
        if isinstance(v, bool):
            v = 'true' if v else 'false'
        print(f'export {k}={shlex.quote(str(v))}')
" "$SETTINGS_FILE" 2>/dev/null)" || true
fi

# ── Helpers ──────────────────────────────────────────────────────

# Extract host and port from an endpoint URL
parse_endpoint() {
    local url="$1"
    # Strip protocol
    local hostport="${url#*://}"
    # Strip path
    hostport="${hostport%%/*}"
    local host="${hostport%:*}"
    local port="${hostport##*:}"
    echo "$host" "$port"
}

# HTTP health check — returns "ok", "unhealthy", or "unreachable"
http_health() {
    local url="$1"
    local code
    code=$(curl -sf --max-time 3 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null) || code="000"
    if [[ "$code" == "200" ]]; then
        echo "ok"
    elif [[ "$code" == "000" ]]; then
        echo "unreachable"
    else
        echo "unhealthy ($code)"
    fi
}

# Docker container status: "running (Up 2h)", "exited", "not found"
container_status() {
    local name="$1"
    if ! command -v docker &>/dev/null; then
        echo "docker-not-installed"
        return
    fi
    local state
    state=$(docker inspect --format='{{.State.Status}}' "$name" 2>/dev/null) || { echo "not found"; return; }
    if [[ "$state" == "running" ]]; then
        local started
        started=$(docker inspect --format='{{.State.StartedAt}}' "$name" 2>/dev/null)
        local uptime
        uptime=$(docker_uptime "$started")
        echo "running ($uptime)"
    else
        echo "$state"
    fi
}

# Convert ISO timestamp to human-friendly uptime
docker_uptime() {
    local started="$1"
    if command -v python3 &>/dev/null; then
        python3 -c "
from datetime import datetime, timezone
import sys
s = datetime.fromisoformat(sys.argv[1].replace('Z','+00:00'))
d = datetime.now(timezone.utc) - s
h, r = divmod(int(d.total_seconds()), 3600)
m, _ = divmod(r, 60)
if h >= 24:
    print(f'{h // 24}d {h % 24}h')
elif h > 0:
    print(f'{h}h {m}m')
else:
    print(f'{m}m')
" "$started" 2>/dev/null || echo "?"
    else
        echo "?"
    fi
}

# Systemd unit status: "active (running) since ...", "inactive", "not found"
systemd_status() {
    local unit="$1"
    if ! command -v systemctl &>/dev/null; then
        echo "n/a"
        return
    fi
    local active
    active=$(systemctl is-active "$unit" 2>/dev/null) || active="not-found"
    if [[ "$active" == "active" ]]; then
        local since
        since=$(systemctl show "$unit" --property=ActiveEnterTimestamp --value 2>/dev/null)
        echo "active (since ${since:-?})"
    else
        echo "$active"
    fi
}

# ── Detect host IP ───────────────────────────────────────────────
PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PI_IP="${PI_IP:-127.0.0.1}"

# ── Collect status for each component ────────────────────────────

declare -A STATUS        # component → status string
declare -A HEALTH        # component → ok / unhealthy / unreachable / disabled
declare -A ENDPOINT_INFO # component → host:port
declare -A EXTRA         # component → extra info

# --- App (uvicorn) ---
APP_PORT="${APP_PORT:-8080}"
APP_URL="http://localhost:${APP_PORT}"
STATUS[app]="$(systemd_status rpicoffee-app)"
HEALTH[app]="$(http_health "${APP_URL}/health")"
ENDPOINT_INFO[app]="${PI_IP}:${APP_PORT}"
EXTRA[app]="sensor_mode=${SENSOR_MODE:-mock}"

# --- Docker services unit ---
STATUS[docker-services]="$(systemd_status rpicoffee-services)"
HEALTH[docker-services]="n/a"
ENDPOINT_INFO[docker-services]="—"
EXTRA[docker-services]=""

# --- Classifier ---
if [[ "${CLASSIFIER_ENABLED:-false}" == "true" ]]; then
    read -r c_host c_port <<< "$(parse_endpoint "${CLASSIFIER_ENDPOINT:-http://localhost:8001}")"
    STATUS[classifier]="$(container_status rpicoffee-classifier)"
    HEALTH[classifier]="$(http_health "${CLASSIFIER_ENDPOINT:-http://localhost:8001}/health")"
    ENDPOINT_INFO[classifier]="${PI_IP}:${c_port}"
    EXTRA[classifier]=""
else
    STATUS[classifier]="disabled"
    HEALTH[classifier]="disabled"
    ENDPOINT_INFO[classifier]="—"
    EXTRA[classifier]=""
fi

# --- LLM ---
if [[ "${LLM_ENABLED:-false}" == "true" ]]; then
    if [[ "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
        # External service — use the dedicated Ollama endpoint
        _OLLAMA_URL="${LLM_OLLAMA_ENDPOINT:-http://localhost:8000}"
        read -r l_host l_port <<< "$(parse_endpoint "$_OLLAMA_URL")"
        STATUS[llm]="external (ollama)"
        HEALTH[llm]="$(http_health "${_OLLAMA_URL}/api/tags")"
        EXTRA[llm]="backend=ollama model=${LLM_MODEL:-qwen2:1.5b}"
    else
        read -r l_host l_port <<< "$(parse_endpoint "${LLM_ENDPOINT:-http://localhost:8002}")"
        STATUS[llm]="$(container_status rpicoffee-llm)"
        HEALTH[llm]="$(http_health "${LLM_ENDPOINT:-http://localhost:8002}/health")"
        EXTRA[llm]="backend=llama-cpp"
    fi
    ENDPOINT_INFO[llm]="${PI_IP}:${l_port}"
else
    STATUS[llm]="disabled"
    HEALTH[llm]="disabled"
    ENDPOINT_INFO[llm]="—"
    EXTRA[llm]=""
fi

# --- TTS ---
if [[ "${TTS_ENABLED:-false}" == "true" ]]; then
    read -r t_host t_port <<< "$(parse_endpoint "${TTS_ENDPOINT:-http://localhost:5050}")"
    STATUS[tts]="$(container_status rpicoffee-tts)"
    HEALTH[tts]="$(http_health "${TTS_ENDPOINT:-http://localhost:5050}/health")"
    ENDPOINT_INFO[tts]="${PI_IP}:${t_port}"
    EXTRA[tts]=""
else
    STATUS[tts]="disabled"
    HEALTH[tts]="disabled"
    ENDPOINT_INFO[tts]="—"
    EXTRA[tts]=""
fi

# --- Remote Save ---
if [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]]; then
    read -r r_host r_port <<< "$(parse_endpoint "${REMOTE_SAVE_ENDPOINT:-http://localhost:7000}")"
    STATUS[remote-save]="$(container_status rpicoffee-remote-save)"
    HEALTH[remote-save]="$(http_health "${REMOTE_SAVE_ENDPOINT:-http://localhost:7000}/health")"
    ENDPOINT_INFO[remote-save]="${PI_IP}:${r_port}"
    EXTRA[remote-save]=""
else
    STATUS[remote-save]="disabled"
    HEALTH[remote-save]="disabled"
    ENDPOINT_INFO[remote-save]="—"
    EXTRA[remote-save]=""
fi

# --- Sensor ---
if [[ "${SENSOR_MODE:-mock}" == "picoquake" ]]; then
    # Query the app's API for sensor status (if app is reachable)
    if [[ "${HEALTH[app]}" == "ok" ]]; then
        SENSOR_JSON=$(curl -sf --max-time 3 "${APP_URL}/api/services/status" 2>/dev/null) || SENSOR_JSON=""
        if [[ -n "$SENSOR_JSON" ]] && command -v python3 &>/dev/null; then
            SENSOR_HEALTHY=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('sensor',{}).get('healthy','?'))" "$SENSOR_JSON" 2>/dev/null) || SENSOR_HEALTHY="?"
            SENSOR_SAMPLES=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('sensor',{}).get('sample_counter','?'))" "$SENSOR_JSON" 2>/dev/null) || SENSOR_SAMPLES="?"
            SENSOR_DROPS=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('sensor',{}).get('drop_counter','?'))" "$SENSOR_JSON" 2>/dev/null) || SENSOR_DROPS="?"
            SENSOR_ERR=$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('sensor',{}).get('error',''))" "$SENSOR_JSON" 2>/dev/null) || SENSOR_ERR=""
            if [[ "$SENSOR_HEALTHY" == "True" ]]; then
                HEALTH[sensor]="ok"
            else
                HEALTH[sensor]="unhealthy"
            fi
            EXTRA[sensor]="device=${SENSOR_DEVICE_ID:-?} samples=${SENSOR_SAMPLES} drops=${SENSOR_DROPS}"
            [[ -n "$SENSOR_ERR" ]] && EXTRA[sensor]="${EXTRA[sensor]} error=${SENSOR_ERR}"
        else
            HEALTH[sensor]="unknown (app unreachable)"
            EXTRA[sensor]="device=${SENSOR_DEVICE_ID:-?}"
        fi
    else
        HEALTH[sensor]="unknown (app not running)"
        EXTRA[sensor]="device=${SENSOR_DEVICE_ID:-?}"
    fi
    STATUS[sensor]="picoquake"
    ENDPOINT_INFO[sensor]="/dev/ttyACM* (USB)"
    # Check if USB device is present
    if ls /dev/ttyACM* 1>/dev/null 2>&1; then
        STATUS[sensor]="picoquake (USB present)"
    else
        STATUS[sensor]="picoquake (USB NOT found)"
    fi
elif [[ "${SENSOR_MODE:-mock}" == "mock" ]]; then
    STATUS[sensor]="mock"
    HEALTH[sensor]="ok"
    ENDPOINT_INFO[sensor]="in-memory"
    EXTRA[sensor]=""
else
    STATUS[sensor]="serial"
    HEALTH[sensor]="n/a"
    ENDPOINT_INFO[sensor]="${SENSOR_SERIAL_PORT:-/dev/ttyUSB0}"
    EXTRA[sensor]=""
fi

# ── Output ───────────────────────────────────────────────────────

COMPONENTS=(app docker-services classifier llm tts remote-save sensor)

if $JSON_MODE; then
    # JSON output
    echo "{"
    first=true
    for comp in "${COMPONENTS[@]}"; do
        $first || echo ","
        first=false
        printf '  "%s": {"status": "%s", "health": "%s", "endpoint": "%s", "info": "%s"}' \
            "$comp" "${STATUS[$comp]}" "${HEALTH[$comp]}" "${ENDPOINT_INFO[$comp]}" "${EXTRA[$comp]}"
    done
    echo ""
    echo "}"
    exit 0
fi

# ── Pretty table output ─────────────────────────────────────────

# Health colour helper
health_badge() {
    local h="$1"
    case "$h" in
        ok)          echo -e "${GREEN}● healthy${NC}" ;;
        disabled)    echo -e "${DIM}○ disabled${NC}" ;;
        n/a)         echo -e "${DIM}— n/a${NC}" ;;
        unreachable) echo -e "${RED}● unreachable${NC}" ;;
        unknown*)    echo -e "${YELLOW}● unknown${NC}" ;;
        *)           echo -e "${RED}● ${h}${NC}" ;;
    esac
}

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║              rpiCoffee – Service Status                         ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${DIM}Host:${NC} ${BOLD}${PI_IP}${NC}    ${DIM}Time:${NC} $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo ""

# Column headers
printf "  ${BOLD}%-18s %-14s %-22s %-30s${NC}\n" "COMPONENT" "HEALTH" "ENDPOINT" "STATUS"
printf "  ${DIM}%-18s %-14s %-22s %-30s${NC}\n" "─────────────────" "─────────────" "─────────────────────" "─────────────────────────────"

for comp in "${COMPONENTS[@]}"; do
    health="${HEALTH[$comp]}"
    badge=$(health_badge "$health")
    extra="${EXTRA[$comp]}"

    printf "  %-18s " "$comp"
    # health_badge outputs colour codes, so we can't use printf width easily.
    # Print badge then pad manually.
    echo -ne "$badge"
    # Pad to 14 visible chars (badge is ≤12 visible chars)
    pad=$((14 - ${#health} - 2))  # "● " = 2 chars + health text
    (( pad > 0 )) && printf "%${pad}s" "" || printf " "
    printf "%-22s " "${ENDPOINT_INFO[$comp]}"
    echo -e "${STATUS[$comp]}"
    if [[ -n "$extra" ]]; then
        echo -e "  ${DIM}                                                 └─ ${extra}${NC}"
    fi
done

echo ""

# ── Quick summary line ───────────────────────────────────────────
total=0; healthy=0; unhealthy=0; disabled=0
for comp in "${COMPONENTS[@]}"; do
    ((total++))
    case "${HEALTH[$comp]}" in
        ok)       ((healthy++)) ;;
        disabled) ((disabled++)) ;;
        n/a)      ;;  # don't count docker-services
        *)        ((unhealthy++)) ;;
    esac
done

echo -ne "  ${BOLD}Summary:${NC} "
echo -ne "${GREEN}${healthy} healthy${NC}"
(( unhealthy > 0 )) && echo -ne "  ${RED}${unhealthy} unhealthy${NC}"
(( disabled > 0 ))  && echo -ne "  ${DIM}${disabled} disabled${NC}"
echo ""
echo ""
