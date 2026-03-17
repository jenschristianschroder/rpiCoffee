#!/usr/bin/env python3
"""Integration test: POST a real brew result to a running remote-save service.

Simulates exactly what the pipeline does — reads a .csv.sample file,
builds the payload, and POSTs to /save against real Dataverse.

Usage:
    # Against local Docker container (default):
    python tests/integration/test_remote_save_live.py

    # Against the Raspberry Pi:
    python tests/integration/test_remote_save_live.py --endpoint http://<pi-ip>:7000

    # Specify a sample file and coffee type:
    python tests/integration/test_remote_save_live.py --csv data/cappuccino-20260209-080802.csv.sample --coffee-type cappuccino

    # Dry-run (print payload without sending):
    python tests/integration/test_remote_save_live.py --dry-run
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent

DEFAULT_ENDPOINT = "http://localhost:7000"
DEFAULT_CSV = REPO_ROOT / "data" / "espresso-20260209-080702.csv.sample"

_CSV_COLUMNS = ["label", "elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]


def load_sensor_data(csv_path: Path) -> tuple[list[dict], str]:
    """Read a .csv.sample file and return (rows, label).

    Returns the sensor data as a list of dicts (matching what the pipeline
    sends) and the label from the first row.
    """
    rows: list[dict] = []
    label = "unknown"
    with csv_path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not label or label == "unknown":
                label = row.get("label", "unknown")
            rows.append({
                "elapsed_s": float(row["elapsed_s"]),
                "acc_x": float(row["acc_x"]),
                "acc_y": float(row["acc_y"]),
                "acc_z": float(row["acc_z"]),
                "gyro_x": float(row["gyro_x"]),
                "gyro_y": float(row["gyro_y"]),
                "gyro_z": float(row["gyro_z"]),
            })
    return rows, label


def sensor_data_to_csv(sensor_data: list[dict], label: str) -> str:
    """Convert sensor dicts back to CSV (mirrors RemoteSaveClient logic)."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in sensor_data:
        writer.writerow({"label": label, **row})
    return buf.getvalue()


def build_payload(sensor_data: list[dict], label: str, coffee_type: str | None = None) -> dict:
    """Build the exact JSON payload the pipeline sends to remote-save."""
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ct = (coffee_type or label).strip().lower()
    csv_str = sensor_data_to_csv(sensor_data, ct)

    return {
        "name": f"{ct}-{now_str}",
        "data": csv_str,
        "text": f"Integration test brew - {ct} at {now_str}",
        "coffee_type": ct,
        "confidence": 0.99,
        "file_content": base64.b64encode(csv_str.encode("utf-8")).decode("ascii"),
        "file_name": f"{ct}-{now_str}.csv",
    }


def check_health(endpoint: str) -> bool:
    """Check if the remote-save service is reachable."""
    try:
        r = requests.get(f"{endpoint}/health", timeout=5)
        r.raise_for_status()
        print(f"  [OK] Health check passed: {r.json()}")
        return True
    except Exception as exc:
        print(f"  [FAIL] Health check failed: {exc}")
        return False


def check_settings(endpoint: str) -> bool:
    """Fetch and display current settings (secrets are masked by the service)."""
    try:
        r = requests.get(f"{endpoint}/settings", timeout=5)
        r.raise_for_status()
        settings = r.json()
        print("  Current settings:")
        for s in settings:
            print(f"    {s['key']:30s} = {s['value']}")

        # Check for missing required config
        required = ["DATAVERSE_ENV_URL", "DATAVERSE_TABLE", "DATAVERSE_COLUMN",
                     "DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET"]
        missing = []
        for s in settings:
            if s["key"] in required:
                val = s["value"]
                if not val or val == "":
                    missing.append(s["key"])
        if missing:
            print(f"\n  [WARN] Missing required settings: {', '.join(missing)}")
            print("  Configure via PATCH /settings or environment variables before saving.")
            return False
        print("\n  [OK] All required settings are configured")
        return True
    except Exception as exc:
        print(f"  [FAIL] Could not fetch settings: {exc}")
        return False


def post_save(endpoint: str, payload: dict) -> bool:
    """POST the payload to /save and report the result."""
    try:
        r = requests.post(f"{endpoint}/save", json=payload, timeout=60)
        if r.status_code == 200:
            body = r.json()
            print(f"  [OK] Record created!")
            print(f"    Record ID: {body['record_id']}")
            print(f"    Message:   {body['message']}")
            return True
        else:
            print(f"  [FAIL] HTTP {r.status_code}")
            try:
                detail = r.json()
                print(f"    Detail: {json.dumps(detail, indent=2)}")
            except Exception:
                print(f"    Body: {r.text[:500]}")
            return False
    except requests.ConnectionError:
        print(f"  [FAIL] Cannot connect to {endpoint}")
        return False
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test: POST a brew result to a running remote-save service with real Dataverse.",
    )
    parser.add_argument(
        "--endpoint", default=DEFAULT_ENDPOINT,
        help=f"Remote-save service URL (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--csv", default=str(DEFAULT_CSV), dest="csv_path",
        help=f"Path to a .csv.sample file (default: {DEFAULT_CSV.name})",
    )
    parser.add_argument(
        "--coffee-type",
        help="Override coffee type (default: auto-detect from CSV label column)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the payload without sending it",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"Error: CSV file not found: {csv_path}")
        return 1

    print(f"\n{'='*60}")
    print(f"  rpiCoffee Remote-Save Integration Test")
    print(f"{'='*60}")
    print(f"  Endpoint:  {args.endpoint}")
    print(f"  CSV file:  {csv_path.name}")

    # 1. Load sensor data
    print(f"\n-- Loading sensor data --")
    sensor_data, label = load_sensor_data(csv_path)
    coffee_type = args.coffee_type or label
    print(f"  Loaded {len(sensor_data)} samples, label='{label}', coffee_type='{coffee_type}'")

    # 2. Build payload
    print(f"\n-- Building payload --")
    payload = build_payload(sensor_data, label, coffee_type)
    csv_size = len(payload["data"])
    b64_size = len(payload["file_content"])
    print(f"  name:         {payload['name']}")
    print(f"  coffee_type:  {payload['coffee_type']}")
    print(f"  confidence:   {payload['confidence']}")
    print(f"  CSV size:     {csv_size:,} bytes")
    print(f"  base64 size:  {b64_size:,} bytes")
    print(f"  text:         {payload['text']}")

    if args.dry_run:
        print(f"\n-- Dry run - payload (truncated) --")
        display = {**payload}
        display["data"] = display["data"][:200] + "..." if len(display["data"]) > 200 else display["data"]
        display["file_content"] = display["file_content"][:80] + "..." if len(display["file_content"]) > 80 else display["file_content"]
        print(json.dumps(display, indent=2))
        print("\n  [DRY RUN] No request sent.")
        return 0

    # 3. Health check
    print(f"\n-- Health check --")
    if not check_health(args.endpoint):
        return 1

    # 4. Settings check
    print(f"\n-- Settings check --")
    settings_ok = check_settings(args.endpoint)
    if not settings_ok:
        print("\n  Proceed anyway? (y/N) ", end="")
        if input().strip().lower() != "y":
            return 1

    # 5. POST /save
    print(f"\n-- Posting to /save --")
    success = post_save(args.endpoint, payload)

    print(f"\n{'='*60}")
    if success:
        print("  RESULT: PASS — record created in Dataverse")
    else:
        print("  RESULT: FAIL — see errors above")
    print(f"{'='*60}\n")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
