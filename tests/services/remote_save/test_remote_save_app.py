"""Tests for the remote-save FastAPI service.

Covers: /health, /manifest, /save, /settings, PATCH /settings.
Dataverse API calls are mocked — no real credentials required.
"""

from __future__ import annotations

import base64


# ── Health & manifest ────────────────────────────────────────────────────────


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_manifest(client):
    resp = client.get("/manifest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "remote-save"
    assert "endpoints" in body
    assert body["endpoints"]["execute"]["path"] == "/save"


# ── POST /save — happy paths ────────────────────────────────────────────────


def test_save_basic(client, save_payload, mock_dataverse):
    """Basic save with minimal payload creates a record and returns its ID."""
    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert "Record created" in body["message"]

    # Verify Dataverse mocks were called
    mock_dataverse["get_token"].assert_called_once()
    mock_dataverse["create_record"].assert_called_once()
    # No file_content → upload_file should NOT be called
    mock_dataverse["upload_file"].assert_not_called()


def test_save_with_file_content(client, save_payload, mock_dataverse):
    """When file_content is supplied, upload_file is called."""
    save_payload["file_content"] = base64.b64encode(b"hello,world").decode()
    save_payload["file_name"] = "test.csv"

    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 200
    mock_dataverse["upload_file"].assert_called_once()


def test_save_with_sensor_data(client, mock_dataverse):
    """When sensor_data is provided, service auto-generates CSV + base64."""
    payload = {
        "name": "sensor-test",
        "coffee_type": "black",
        "confidence": 0.88,
        "sensor_data": [
            {"elapsed_s": 0.0, "acc_x": 0.1, "acc_y": 0.0, "acc_z": 9.8,
             "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0},
            {"elapsed_s": 0.01, "acc_x": 0.2, "acc_y": 0.1, "acc_z": 9.7,
             "gyro_x": 0.1, "gyro_y": 0.0, "gyro_z": 0.0},
        ],
    }
    resp = client.post("/save", json=payload)
    assert resp.status_code == 200
    # sensor_data triggers auto CSV → upload_file is called
    mock_dataverse["upload_file"].assert_called_once()


def test_save_all_coffee_types(client, save_payload, mock_dataverse):
    """Verify all three coffee types are accepted (case-insensitive)."""
    for coffee_type in ["Black", "ESPRESSO", "cappuccino"]:
        save_payload["coffee_type"] = coffee_type
        resp = client.post("/save", json=save_payload)
        assert resp.status_code == 200, f"Failed for coffee_type={coffee_type}"


def test_save_with_record_data(client, save_payload, mock_dataverse):
    """Additional record_data fields are forwarded to create_record."""
    save_payload["record_data"] = {"custom_field": "extra_value"}
    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 200

    call_kwargs = mock_dataverse["create_record"].call_args
    record_data = call_kwargs.kwargs.get("record_data") or call_kwargs[1].get("record_data")
    assert "custom_field" in record_data


# ── POST /save — error paths ────────────────────────────────────────────────


def test_save_invalid_coffee_type(client, save_payload, mock_dataverse):
    """Invalid coffee_type returns 400."""
    save_payload["coffee_type"] = "mocha"
    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 400
    assert "Invalid coffee_type" in resp.json()["detail"]


def test_save_missing_required_field(client, mock_dataverse):
    """Missing required fields return 422 (Pydantic validation)."""
    resp = client.post("/save", json={"data": "some data"})
    assert resp.status_code == 422


def test_save_invalid_base64(client, save_payload, mock_dataverse):
    """Invalid base64 in file_content returns 400."""
    save_payload["file_content"] = "not-valid-base64!!!"
    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 400
    assert "base64" in resp.json()["detail"].lower()


def test_save_missing_config(client, save_payload, remote_save_mod):
    """When Dataverse env vars are missing, /save returns 500."""
    # Clear the runtime config to simulate missing vars
    remote_save_mod._runtime["DATAVERSE_ENV_URL"] = ""
    remote_save_mod._runtime["DATAVERSE_TENANT_ID"] = ""

    resp = client.post("/save", json=save_payload)
    assert resp.status_code == 500
    assert "Missing required configuration" in resp.json()["detail"]


# ── GET /settings ────────────────────────────────────────────────────────────


def test_get_settings(client):
    """GET /settings returns all registered keys with masked secrets."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    settings = resp.json()
    assert isinstance(settings, list)
    assert len(settings) > 0

    keys = {s["key"] for s in settings}
    assert "DATAVERSE_ENV_URL" in keys
    assert "DATAVERSE_TENANT_ID" in keys

    # Secrets must be masked
    for s in settings:
        if s["key"] in ("DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET"):
            assert s["value"] in ("***set***", ""), f"Secret {s['key']} leaked: {s['value']}"


def test_settings_secrets_never_leak(client):
    """Verify secret values are never returned as plain text."""
    resp = client.get("/settings")
    settings = resp.json()
    for s in settings:
        if s.get("secret"):
            assert s["value"] != "fake-secret", f"Secret {s['key']} leaked!"
            assert s["value"] in ("***set***", "")


# ── PATCH /settings ──────────────────────────────────────────────────────────


def test_update_secret_with_masked_placeholder_is_ignored(client, remote_save_mod):
    """PATCH /settings with '***set***' for a secret must NOT overwrite the real value.

    This is the admin-panel round-trip bug: GET returns '***set***' for
    secrets, the JS sends it back in PATCH, and the real credential gets
    replaced by the literal placeholder string.
    """
    # The real secret loaded from fake_env
    real_secret = remote_save_mod._runtime["DATAVERSE_CLIENT_SECRET"]
    assert real_secret == "fake-secret"

    # Simulate what the admin panel does: send all settings back including
    # the masked placeholder for secrets
    resp = client.patch("/settings", json={
        "settings": {"DATAVERSE_CLIENT_SECRET": "***set***"},
    })
    assert resp.status_code == 200
    # The key should NOT be in the updated list
    assert "DATAVERSE_CLIENT_SECRET" not in resp.json()["updated"]

    # The real value must be preserved
    assert remote_save_mod._runtime["DATAVERSE_CLIENT_SECRET"] == "fake-secret"


def test_update_secret_with_real_value_accepted(client, remote_save_mod):
    """PATCH /settings with a real (non-placeholder) secret value is accepted."""
    resp = client.patch("/settings", json={
        "settings": {"DATAVERSE_CLIENT_SECRET": "new-real-secret"},
    })
    assert resp.status_code == 200
    assert "DATAVERSE_CLIENT_SECRET" in resp.json()["updated"]
    assert remote_save_mod._runtime["DATAVERSE_CLIENT_SECRET"] == "new-real-secret"


def test_update_settings(client):
    """PATCH /settings updates runtime values and reports updated keys."""
    resp = client.patch("/settings", json={
        "settings": {"DATAVERSE_ENV_URL": "https://new-org.crm.dynamics.com"},
    })
    assert resp.status_code == 200
    assert "DATAVERSE_ENV_URL" in resp.json()["updated"]

    # Verify the value actually changed
    resp = client.get("/settings")
    for s in resp.json():
        if s["key"] == "DATAVERSE_ENV_URL":
            assert s["value"] == "https://new-org.crm.dynamics.com"


def test_update_settings_unknown_key_ignored(client):
    """Unknown keys are silently ignored."""
    resp = client.patch("/settings", json={
        "settings": {"UNKNOWN_KEY": "whatever"},
    })
    assert resp.status_code == 200
    assert "UNKNOWN_KEY" not in resp.json()["updated"]


def test_update_secret_setting(client):
    """Secrets can be updated but still won't appear in GET."""
    resp = client.patch("/settings", json={
        "settings": {"DATAVERSE_CLIENT_SECRET": "new-super-secret"},
    })
    assert resp.status_code == 200
    assert "DATAVERSE_CLIENT_SECRET" in resp.json()["updated"]

    # Verify it's still masked in GET
    resp = client.get("/settings")
    for s in resp.json():
        if s["key"] == "DATAVERSE_CLIENT_SECRET":
            assert s["value"] == "***set***"


# ── Settings persistence ────────────────────────────────────────────────────


def test_settings_persisted_to_json(client, remote_save_mod, tmp_path):
    """PATCH /settings persists changes to settings.json."""
    client.patch("/settings", json={
        "settings": {"DATAVERSE_TABLE": "new_table_name"},
    })

    # Read the persisted file
    settings_path = remote_save_mod.SETTINGS_PATH
    assert settings_path.exists()
    import json
    data = json.loads(settings_path.read_text())
    assert data["DATAVERSE_TABLE"] == "new_table_name"
