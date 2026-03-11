"""Tests for services/remote-save/app.py — Dataverse upload service."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    async def test_get_settings(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


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
