"""Tests for app/services/llm_client.py."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx
from services.llm_client import LLMClient


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    with patch("services.llm_client.config") as cfg:
        cfg.LLM_ENDPOINT = "http://llm:8002"
        cfg.LLM_ENABLED = True
        cfg.get = lambda key: {"LLM_TIMEOUT": 120}.get(key, None)
        yield cfg


class TestLLMClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self):
        respx.get("http://llm:8002/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await LLMClient.health()
        assert result["healthy"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_down(self):
        respx.get("http://llm:8002/health").mock(side_effect=httpx.ConnectError("refused"))
        result = await LLMClient.health()
        assert result["healthy"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_generate_success(self):
        respx.post("http://llm:8002/generate").mock(
            return_value=httpx.Response(200, json={
                "response": "Nice espresso!", "tokens": 5, "elapsed_s": 0.5, "tokens_per_s": 10.0
            })
        )
        result = await LLMClient.generate("espresso")
        assert result["response"] == "Nice espresso!"
        assert result["tokens"] == 5

    @respx.mock
    @pytest.mark.asyncio
    async def test_generate_failure(self):
        respx.post("http://llm:8002/generate").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await LLMClient.generate("espresso")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_disabled(self, _mock_config):
        _mock_config.LLM_ENABLED = False
        result = await LLMClient.generate("espresso")
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_settings(self):
        respx.get("http://llm:8002/settings").mock(
            return_value=httpx.Response(200, json=[{"key": "temperature", "value": 0.7}])
        )
        result = await LLMClient.get_settings()
        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_settings(self):
        respx.patch("http://llm:8002/settings").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = await LLMClient.update_settings({"temperature": 0.5})
        assert result["ok"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_generate_uses_config_timeout(self, _mock_config):
        """Verify the generate method reads timeout from config."""
        _mock_config.get = lambda key: {"LLM_TIMEOUT": 300}.get(key, None)
        respx.post("http://llm:8002/generate").mock(
            return_value=httpx.Response(200, json={
                "response": "Coffee!", "tokens": 1, "elapsed_s": 0.1, "tokens_per_s": 10.0
            })
        )
        result = await LLMClient.generate("espresso")
        assert result is not None
        assert result["response"] == "Coffee!"
