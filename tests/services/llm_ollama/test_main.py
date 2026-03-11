"""Tests for services/llm-ollama/main.py — Ollama proxy FastAPI endpoints."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx
from httpx import ASGITransport

_SVC_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "services" / "llm-ollama")


def _import_svc_main():
    """Import the llm-ollama main module under a unique name."""
    if _SVC_DIR not in sys.path:
        sys.path.insert(0, _SVC_DIR)
    # Use a namespaced key so it doesn't collide with app/main.py
    mod_key = "svc_llm_ollama_main"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    spec = importlib.util.spec_from_file_location(mod_key, Path(_SVC_DIR) / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
async def client(tmp_path):
    with patch.dict("os.environ", {"SETTINGS_DIR": str(tmp_path)}):
        svc = _import_svc_main()
        svc._load_settings()

        transport = ASGITransport(app=svc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


class TestHealth:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        # The llm-ollama health endpoint pings the upstream Ollama API
        respx.get("http://localhost:8000/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


class TestManifest:
    @pytest.mark.asyncio
    async def test_manifest_returns_name(self, client):
        resp = await client.get("/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert "name" in data
        assert "endpoints" in data


class TestSettings:
    @pytest.mark.asyncio
    async def test_get_settings(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_update_settings(self, client):
        resp = await client.patch("/settings", json={"settings": {"LLM_MAX_TOKENS": 128}})
        assert resp.status_code == 200
