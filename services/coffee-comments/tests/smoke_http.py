"""Exercise a running server over a real HTTP socket; standard library only."""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

base = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
for attempt in range(100):
    try:
        with urllib.request.urlopen(base + "/health", timeout=1) as response:
            assert json.load(response) == {"status": "ok"}
        break
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        if attempt == 99:
            raise
        time.sleep(0.2)

with urllib.request.urlopen(base + "/coffee-types", timeout=5) as response:
    assert json.load(response)["coffee_types"] == ["black", "cappuccino", "espresso", "other"]

with urllib.request.urlopen(base + "/manifest", timeout=5) as response:
    manifest = json.load(response)
    assert manifest["name"] == "coffee-comments"
    assert manifest["endpoints"]["execute"] == {"method": "POST", "path": "/generate"}
    assert manifest["endpoints"]["health"] == {"method": "GET", "path": "/health"}
    assert {item["name"] for item in manifest["inputs"]} == {"coffee_label", "timestamp"}

model = json.loads((Path(__file__).resolve().parents[1] / "weights.json").read_text())
other_lines = [item["text"] for item in model["punchlines"] if item.get("coffees") == ["other"]]
routes = [
    ("/comment", "coffee_type", "datetime", "comment"),
    (manifest["endpoints"]["execute"]["path"], "coffee_label", "timestamp", "response"),
]
for route, coffee_field, time_field, text_field in routes:
    for coffee in ["black", "cappuccino", "espresso", "other", "unknown"]:
        payload = {coffee_field: coffee, time_field: "2026-11-04T16:30:00+01:00"}
        request = urllib.request.Request(
            base + route, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            result = json.load(response)
            expected_fields = {"comment"} if route == "/comment" else {item["name"] for item in manifest["outputs"]}
            assert set(result) == expected_fields
            assert "4:30 p.m." in result[text_field]
            if coffee in ("other", "unknown"):
                assert any(result[text_field].endswith(line) for line in other_lines)
            else:
                assert coffee in result[text_field].lower()
            if route == "/generate":
                assert isinstance(result["elapsed_s"], float) and result["elapsed_s"] >= 0
            print(route, json.dumps(result))

    payload = {coffee_field: "espresso", time_field: "2026-11-04T16:30:00"}
    request = urllib.request.Request(
        base + route, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 422
    else:
        raise AssertionError(f"{route} accepted a date-time without an offset.")
print("HTTP smoke checks passed.")
