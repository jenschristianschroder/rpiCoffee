#!/usr/bin/env bash
#
# rpiCoffee setup script
#
# Reads .env to determine which services are enabled,
# then builds and starts only those containers.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  rpiCoffee – Setup & Deploy"
echo "========================================"
echo ""

# Source .env
if [ -f .env ]; then
    echo "[*] Loading .env configuration..."
    set -a
    source .env
    set +a
else
    echo "[!] No .env file found. Using defaults."
fi

# Copy CSV data files into the data volume on first run
echo "[*] Ensuring data directory exists..."
mkdir -p data
for csv in data/*.csv; do
    if [ -f "$csv" ]; then
        echo "    Found $csv"
    fi
done

# Build profile flags based on enabled services
PROFILES=""

if [ "${CLASSIFIER_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile classifier"
    echo "[+] classifier  : ENABLED (${CLASSIFIER_ENDPOINT:-http://classifier:8001})"
else
    echo "[-] classifier  : disabled"
fi

if [ "${LLM_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile llm"
    echo "[+] llm         : ENABLED (${LLM_ENDPOINT:-http://llm:8000})"
else
    echo "[-] llm         : disabled"
fi

if [ "${TTS_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile tts"
    echo "[+] tts         : ENABLED (${TTS_ENDPOINT:-http://tts:5000})"
else
    echo "[-] tts         : disabled"
fi

if [ "${REMOTE_SAVE_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile remote-save"
    echo "[+] remote-save : ENABLED (${REMOTE_SAVE_ENDPOINT:-http://remote-save:7000})"
else
    echo "[-] remote-save : disabled"
fi

echo ""
echo "[*] Building and starting containers..."
echo "    docker compose $PROFILES up -d --build"
echo ""

# shellcheck disable=SC2086
docker compose $PROFILES up -d --build

echo ""
echo "========================================"
echo "  Deployment complete!"
echo "========================================"
echo ""
echo "  Admin UI:  http://localhost:8080/admin/"
echo ""
docker compose $PROFILES ps
