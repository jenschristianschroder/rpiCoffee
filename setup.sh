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
for csv in *.csv; do
    if [ -f "$csv" ] && [ ! -f "data/$csv" ]; then
        echo "    Copying $csv → data/"
        cp "$csv" "data/"
    fi
done

# Build profile flags based on enabled services
PROFILES=""

if [ "${LOCALML_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile localml"
    echo "[+] localml   : ENABLED (${LOCALML_ENDPOINT:-http://localml:8001})"
else
    echo "[-] localml   : disabled"
fi

if [ "${LOCALLM_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile locallm"
    echo "[+] locallm   : ENABLED (${LOCALLM_ENDPOINT:-http://locallm:8000})"
else
    echo "[-] locallm   : disabled"
fi

if [ "${LOCALTTS_ENABLED:-false}" = "true" ]; then
    PROFILES="$PROFILES --profile localtts"
    echo "[+] localtts  : ENABLED (${LOCALTTS_ENDPOINT:-http://localtts:5000})"
else
    echo "[-] localtts  : disabled"
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
echo "  Password:  ${ADMIN_PASSWORD:-1234}"
echo ""
docker compose $PROFILES ps
