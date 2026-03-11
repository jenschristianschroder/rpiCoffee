"""Tests for app/services/ollama_client.py."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from services.ollama_client import OllamaClient


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    with patch("services.ollama_client.config") as cfg:
        cfg.LLM_OLLAMA_SERVICE_ENDPOINT = "http://llm-ollama:8003"
        cfg.LLM_ENABLED = True
        cfg.LLM_BACKEND = "ollama"
        yield cfg


class TestOllamaClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self):
        respx.get("http://llm-ollama:8003/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await OllamaClient.health()
        assert result["healthy"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_down(self):
        respx.get("http://llm-ollama:8003/health").mock(
            side_effect=httpx.ConnectError("refused")
        )
        result = await OllamaClient.health()
        assert result["healthy"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_generate_success(self):
        respx.post("http://llm-ollama:8003/generate").mock(
            return_value=httpx.Response(200, json={
                "response": "Nice espresso!", "tokens": 5, "elapsed_s": 0.5, "tokens_per_s": 10.0
            })
        )
        result = await OllamaClient.generate("espresso")
        assert result["response"] == "Nice espresso!"

    @respx.mock
    @pytest.mark.asyncio
    async def test_generate_failure(self):
        respx.post("http://llm-ollama:8003/generate").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await OllamaClient.generate("espresso")
        assert result is None

    @pytest.mark.asyncio
    async def test_generate_disabled(self, _mock_config):
        _mock_config.LLM_ENABLED = False
        result = await OllamaClient.generate("espresso")
        assert result is None
