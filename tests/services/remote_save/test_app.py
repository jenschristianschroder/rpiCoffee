"""Tests for services/remote-save/app.py — Dataverse upload service."""

from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

_SVC_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "services" / "remote-save")


def _import_svc_app():
    """Import the remote-save app module under a unique name."""
    if _SVC_DIR not in sys.path:
        sys.path.insert(0, _SVC_DIR)
    mod_key = "svc_remote_save_app"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    spec = importlib.util.spec_from_file_location(mod_key, Path(_SVC_DIR) / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _runtime_override(svc, overrides: dict) -> Iterator[None]:
    """Temporarily set keys in *svc._runtime*, restoring originals on exit."""
    orig = {k: svc._runtime.get(k, "") for k in overrides}
    svc._runtime.update(overrides)
    try:
        yield
    finally:
        svc._runtime.update(orig)


@pytest.fixture()
async def client(tmp_path):
    with patch.dict("os.environ", {
        "SETTINGS_DIR": str(tmp_path),
        "DATAVERSE_ENV_URL": "https://test.crm.dynamics.com",
        "DATAVERSE_TABLE": "test_table",
        "DATAVERSE_COLUMN": "test_column",
        "DATAVERSE_TENANT_ID": "tenant",
        "DATAVERSE_CLIENT_ID": "client",
        "DATAVERSE_CLIENT_SECRET": "secret",
    }):
        svc = _import_svc_app()
        # Recompute SETTINGS_PATH so persisted-settings logic points at this
        # test's isolated tmp_path, not the path captured at first module load.
        svc.SETTINGS_PATH = tmp_path / "settings.json"
        # Reload runtime settings from the patched environment and fresh path.
        svc._load_settings()
        transport = ASGITransport(app=svc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestManifest:
    @pytest.mark.asyncio
    async def test_manifest(self, client):
        resp = await client.get("/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "remote-save"


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_settings_returns_list(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_get_settings_contains_all_keys(self, client):
        """All registry keys must appear in the GET /settings response."""
        resp = await client.get("/settings")
        assert resp.status_code == 200
        keys = {entry["key"] for entry in resp.json()}
        expected = {
            "DATAVERSE_ENV_URL", "DATAVERSE_TABLE", "DATAVERSE_COLUMN",
            "DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET",
            "DATAVERSE_COL_NAME", "DATAVERSE_COL_DATA", "DATAVERSE_COL_TEXT",
            "DATAVERSE_COL_CONFIDENCE", "DATAVERSE_COL_COFFEE_TYPE",
        }
        assert expected == keys

    @pytest.mark.asyncio
    async def test_secrets_are_masked_when_set(self, client):
        """Secret keys must return '***set***' when they have a value, never the real value."""
        resp = await client.get("/settings")
        assert resp.status_code == 200
        secret_entries = [e for e in resp.json() if e.get("secret")]
        assert len(secret_entries) == 3  # TENANT_ID, CLIENT_ID, CLIENT_SECRET
        for entry in secret_entries:
            assert entry["value"] == "***set***", (
                f"Secret key {entry['key']} must be masked; got: {entry['value']!r}"
            )
            # The real values (e.g. "tenant", "client", "secret") must not appear
            assert entry["value"] not in ("tenant", "client", "secret")

    @pytest.mark.asyncio
    async def test_secrets_are_empty_when_not_set(self, tmp_path):
        """Secret keys return '' when no value is configured."""
        svc = _import_svc_app()
        secret_keys = ("DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET")
        with _runtime_override(svc, {k: "" for k in secret_keys}):
            transport = ASGITransport(app=svc.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/settings")
        assert resp.status_code == 200
        for entry in resp.json():
            if entry["key"] in secret_keys:
                assert entry["value"] == "", f"Unset secret {entry['key']} should return ''"

    @pytest.mark.asyncio
    async def test_non_secret_settings_have_secret_flag_false(self, client):
        """Non-secret entries must carry secret=False."""
        resp = await client.get("/settings")
        for entry in resp.json():
            if entry["key"] not in ("DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET"):
                assert entry.get("secret") is False, (
                    f"Non-secret key {entry['key']} has unexpected secret flag"
                )

    @pytest.mark.asyncio
    async def test_patch_non_secret_setting(self, client):
        """PATCH /settings updates a non-secret setting and returns the updated key."""
        resp = await client.patch("/settings", json={"settings": {"DATAVERSE_TABLE": "new_table"}})
        assert resp.status_code == 200
        assert "DATAVERSE_TABLE" in resp.json()["updated"]

        # Verify the change is reflected in GET /settings
        get_resp = await client.get("/settings")
        table_entry = next(e for e in get_resp.json() if e["key"] == "DATAVERSE_TABLE")
        assert table_entry["value"] == "new_table"

    @pytest.mark.asyncio
    async def test_patch_secret_setting_accepted(self, client):
        """PATCH /settings must accept secret keys and confirm the update."""
        resp = await client.patch("/settings", json={"settings": {
            "DATAVERSE_CLIENT_SECRET": "brand-new-secret",
        }})
        assert resp.status_code == 200
        assert "DATAVERSE_CLIENT_SECRET" in resp.json()["updated"]

    @pytest.mark.asyncio
    async def test_patch_secret_not_exposed_after_update(self, client):
        """After a secret is updated via PATCH, GET /settings still returns the mask."""
        await client.patch("/settings", json={"settings": {
            "DATAVERSE_CLIENT_SECRET": "brand-new-secret",
        }})
        get_resp = await client.get("/settings")
        secret_entry = next(e for e in get_resp.json() if e["key"] == "DATAVERSE_CLIENT_SECRET")
        assert secret_entry["value"] == "***set***"
        assert "brand-new-secret" not in str(get_resp.json())

    @pytest.mark.asyncio
    async def test_patch_unknown_key_ignored(self, client):
        """Unknown keys in PATCH /settings payload must be silently ignored."""
        resp = await client.patch("/settings", json={"settings": {"UNKNOWN_KEY": "value"}})
        assert resp.status_code == 200
        assert "UNKNOWN_KEY" not in resp.json()["updated"]

    @pytest.mark.asyncio
    async def test_switch_environment_and_app_reg_via_settings(self, client):
        """End-to-end: switch both Dataverse env and Azure app reg via PATCH /settings.

        This test confirms that the service can be reconfigured to a completely
        different environment and app registration without code changes — only by
        updating the /settings endpoint.
        """
        patch_resp = await client.patch("/settings", json={"settings": {
            "DATAVERSE_ENV_URL": "https://neworg.crm.dynamics.com",
            "DATAVERSE_TABLE": "new_table",
            "DATAVERSE_TENANT_ID": "new-tenant",
            "DATAVERSE_CLIENT_ID": "new-client-id",
            "DATAVERSE_CLIENT_SECRET": "new-client-secret",
        }})
        assert patch_resp.status_code == 200
        updated = set(patch_resp.json()["updated"])
        assert updated == {
            "DATAVERSE_ENV_URL", "DATAVERSE_TABLE",
            "DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET",
        }

        # Verify non-secret values are persisted and readable
        get_resp = await client.get("/settings")
        settings_by_key = {e["key"]: e for e in get_resp.json()}
        assert settings_by_key["DATAVERSE_ENV_URL"]["value"] == "https://neworg.crm.dynamics.com"
        assert settings_by_key["DATAVERSE_TABLE"]["value"] == "new_table"

        # Verify secrets are masked (not exposed) even after the update
        for secret_key in ("DATAVERSE_TENANT_ID", "DATAVERSE_CLIENT_ID", "DATAVERSE_CLIENT_SECRET"):
            assert settings_by_key[secret_key]["value"] == "***set***"
            assert settings_by_key[secret_key]["secret"] is True

        # Verify the raw values in _runtime were actually updated (used by /save)
        svc = _import_svc_app()
        assert svc._runtime["DATAVERSE_ENV_URL"] == "https://neworg.crm.dynamics.com"
        assert svc._runtime["DATAVERSE_TENANT_ID"] == "new-tenant"
        assert svc._runtime["DATAVERSE_CLIENT_ID"] == "new-client-id"
        assert svc._runtime["DATAVERSE_CLIENT_SECRET"] == "new-client-secret"

    @pytest.mark.asyncio
    async def test_column_overrides_exposed_and_updatable(self, client):
        """Column name override settings must be readable and updatable."""
        # Defaults should be present
        resp = await client.get("/settings")
        settings_by_key = {e["key"]: e for e in resp.json()}
        assert settings_by_key["DATAVERSE_COL_NAME"]["value"] == "jenssch_name"

        # Update a column override
        patch_resp = await client.patch("/settings", json={"settings": {
            "DATAVERSE_COL_NAME": "custom_name_col",
        }})
        assert patch_resp.status_code == 200
        assert "DATAVERSE_COL_NAME" in patch_resp.json()["updated"]

        # Verify updated value is returned
        get_resp = await client.get("/settings")
        settings_by_key = {e["key"]: e for e in get_resp.json()}
        assert settings_by_key["DATAVERSE_COL_NAME"]["value"] == "custom_name_col"

    @pytest.mark.asyncio
    async def test_settings_persist_across_restart(self, client, tmp_path):
        """Settings written via PATCH must survive a simulated container restart.

        This test verifies the full persistence round-trip:
        1. Update a setting via PATCH — triggers atomic write to settings.json.
        2. Reset the in-memory runtime dict (simulating a fresh container start).
        3. Reload settings from disk via _load_settings().
        4. Confirm the previously persisted value is restored.
        """
        svc = _import_svc_app()
        svc.SETTINGS_PATH = tmp_path / "settings.json"

        # Step 1 — persist a new value
        patch_resp = await client.patch("/settings", json={"settings": {
            "DATAVERSE_TABLE": "persisted_table",
            "DATAVERSE_ENV_URL": "https://persist.crm.dynamics.com",
        }})
        assert patch_resp.status_code == 200
        assert svc.SETTINGS_PATH.exists(), "settings.json must be written by PATCH"

        # Step 2 — wipe the in-memory state (simulate container restart)
        original_runtime = dict(svc._runtime)
        svc._runtime.clear()

        # Step 3 — reload from disk
        svc._load_settings()

        # Step 4 — confirm persisted values were restored
        assert svc._runtime["DATAVERSE_TABLE"] == "persisted_table"
        assert svc._runtime["DATAVERSE_ENV_URL"] == "https://persist.crm.dynamics.com"

        # Restore runtime so other tests are unaffected
        svc._runtime.update(original_runtime)

    @pytest.mark.asyncio
    async def test_settings_json_is_written_atomically(self, client, tmp_path):
        """PATCH /settings must write settings.json atomically (no leftover .tmp files)."""
        svc = _import_svc_app()
        svc.SETTINGS_PATH = tmp_path / "settings.json"

        resp = await client.patch("/settings", json={"settings": {
            "DATAVERSE_TABLE": "atomic_table",
        }})
        assert resp.status_code == 200

        # The final file must exist
        assert svc.SETTINGS_PATH.exists()
        # No leftover temp files should remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Leftover temp files found: {tmp_files}"

    @pytest.mark.asyncio
    async def test_corrupt_settings_json_is_ignored_on_load(self, tmp_path):
        """A corrupt settings.json must not crash the service; env vars still load."""
        (tmp_path / "settings.json").write_text("{invalid json}")
        svc = _import_svc_app()
        svc.SETTINGS_PATH = tmp_path / "settings.json"
        with patch.dict("os.environ", {
            "DATAVERSE_TABLE": "from_env",
            "SETTINGS_DIR": str(tmp_path),
        }):
            svc._load_settings()
        # Environment variable value must still be present despite corrupt JSON
        assert svc._runtime.get("DATAVERSE_TABLE") == "from_env"


class TestSave:
    @pytest.mark.asyncio
    @patch("svc_remote_save_app.upload_file")
    @patch("svc_remote_save_app.create_record", return_value="id123")
    @patch("svc_remote_save_app.get_token", return_value="fake-token")
    async def test_save_success(self, mock_token, mock_create, mock_upload, client):
        resp = await client.post("/save", json={
            "name": "test-brew",
            "data": "test data content",
            "coffee_type": "espresso",
            "confidence": 0.95,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_save_missing_config_returns_500(self, tmp_path):
        """POST /save must return 500 when required configuration is missing."""
        svc = _import_svc_app()
        missing_keys = {
            "DATAVERSE_ENV_URL": "", "DATAVERSE_TABLE": "", "DATAVERSE_COLUMN": "",
            "DATAVERSE_TENANT_ID": "", "DATAVERSE_CLIENT_ID": "", "DATAVERSE_CLIENT_SECRET": "",
        }
        with _runtime_override(svc, missing_keys):
            transport = ASGITransport(app=svc.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post("/save", json={
                    "name": "test-brew",
                    "coffee_type": "espresso",
                    "confidence": 0.9,
                })
        assert resp.status_code == 500
        assert "Missing required configuration" in resp.json()["detail"]
