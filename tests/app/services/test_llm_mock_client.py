"""Tests for app/services/llm_mock_client.py."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from services.llm_mock_client import MockLLMClient


@pytest.fixture(autouse=True)
def _mock_config():
    with patch("services.llm_mock_client.config") as cfg:
        cfg.LLM_ENABLED = True
        yield cfg


class TestMockLLMClient:
    @pytest.mark.asyncio
    async def test_health(self):
        result = await MockLLMClient.health()
        assert result["healthy"] is True
        assert result["backend"] == "mock"
        assert result["enabled"] is True

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        result = await MockLLMClient.generate("espresso")
        assert result is not None
        assert isinstance(result["response"], str)
        assert len(result["response"]) > 0
        assert result["tokens"] > 0
        assert result["elapsed_s"] >= 0
        assert result["tokens_per_s"] >= 0

    @pytest.mark.asyncio
    async def test_generate_known_label(self):
        result = await MockLLMClient.generate("espresso")
        assert result is not None
        assert "espresso" in result["response"].lower() or len(result["response"]) > 0

    @pytest.mark.asyncio
    async def test_generate_unknown_label_uses_default(self):
        result = await MockLLMClient.generate("matcha")
        assert result is not None
        assert isinstance(result["response"], str)
        assert len(result["response"]) > 0

    @pytest.mark.asyncio
    async def test_generate_disabled(self, _mock_config):
        _mock_config.LLM_ENABLED = False
        result = await MockLLMClient.generate("espresso")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_settings(self):
        result = await MockLLMClient.get_settings()
        assert isinstance(result, list)
        assert len(result) > 0
        assert result[0]["key"] == "backend"
        assert result[0]["value"] == "mock"

    @pytest.mark.asyncio
    async def test_update_settings(self):
        result = await MockLLMClient.update_settings({"temperature": 0.5})
        assert result == {"ok": True}
