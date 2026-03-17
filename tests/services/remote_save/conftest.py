"""Shared fixtures for remote-save service tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

# Load the remote-save app.py as a dedicated module name to avoid
# collision with the top-level ``app/`` package already on sys.path.
_SERVICE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "services" / "remote-save"
_APP_PY = _SERVICE_DIR / "app.py"

_MODULE_NAME = "remote_save_app"


def _load_remote_save_module() -> ModuleType:
    """Import services/remote-save/app.py as 'remote_save_app'."""
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _APP_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


# Fake Dataverse credentials used across all tests.
_FAKE_ENV = {
    "DATAVERSE_ENV_URL": "https://fake-org.crm.dynamics.com",
    "DATAVERSE_TABLE": "fake_table",
    "DATAVERSE_COLUMN": "fake_filecol",
    "DATAVERSE_TENANT_ID": "00000000-0000-0000-0000-000000000001",
    "DATAVERSE_CLIENT_ID": "00000000-0000-0000-0000-000000000002",
    "DATAVERSE_CLIENT_SECRET": "fake-secret",
    "DATAVERSE_COL_NAME": "test_name",
    "DATAVERSE_COL_DATA": "test_data",
    "DATAVERSE_COL_TEXT": "test_text",
    "DATAVERSE_COL_CONFIDENCE": "test_confidence",
    "DATAVERSE_COL_COFFEE_TYPE": "test_type",
    "SETTINGS_DIR": "",  # will be overridden per-test
}


@pytest.fixture()
def fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set fake Dataverse env vars and redirect SETTINGS_DIR to a temp dir."""
    env = {**_FAKE_ENV, "SETTINGS_DIR": str(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture()
def remote_save_mod(fake_env) -> ModuleType:
    """Load (or reload) the remote-save app module with fake env vars."""
    # Remove cached module so it picks up patched env vars
    sys.modules.pop(_MODULE_NAME, None)
    mod = _load_remote_save_module()
    mod._load_settings()
    return mod


@pytest.fixture()
def client(remote_save_mod):
    """Return a FastAPI TestClient for the remote-save app."""
    from fastapi.testclient import TestClient

    return TestClient(remote_save_mod.app)


@pytest.fixture()
def save_payload() -> dict:
    """Minimal valid payload for POST /save."""
    return {
        "name": "test-run-20260317",
        "data": "acc_x,acc_y,acc_z\n0.1,0.2,9.8",
        "text": "Nice espresso!",
        "confidence": 0.95,
        "coffee_type": "espresso",
    }


@pytest.fixture()
def mock_dataverse(remote_save_mod):
    """Patch Dataverse HTTP calls (get_token, create_record, upload_file).

    Yields a dict of the mocks so tests can assert on calls or override
    return values.
    """
    target = _MODULE_NAME
    with (
        patch(f"{target}.get_token", return_value="fake-access-token") as mock_token,
        patch(f"{target}.create_record", return_value="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") as mock_create,
        patch(f"{target}.upload_file") as mock_upload,
    ):
        yield {
            "get_token": mock_token,
            "create_record": mock_create,
            "upload_file": mock_upload,
        }
