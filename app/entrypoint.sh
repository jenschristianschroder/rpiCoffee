#!/bin/bash
set -e

# On first run, if no CSV files exist in /data, copy defaults
if ! ls /data/*.csv 1>/dev/null 2>&1; then
    echo "[entrypoint] No CSV files in /data – copying defaults..."
    cp /data/csv-defaults/*.csv /data/ 2>/dev/null || true
fi

# Ensure audio directory exists
mkdir -p /data/audio

exec uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1
