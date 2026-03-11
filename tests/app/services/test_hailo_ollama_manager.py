"""Tests for app/services/hailo_ollama_manager.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from services.hailo_ollama_manager import is_active, is_enabled, stop_and_disable


class TestHailoOllamaManager:
    @pytest.mark.asyncio
    @patch("services.hailo_ollama_manager._systemctl", new_callable=AsyncMock)
    async def test_is_active_true(self, mock_systemctl):
        mock_systemctl.return_value = (0, "active")
        assert await is_active() is True

    @pytest.mark.asyncio
    @patch("services.hailo_ollama_manager._systemctl", new_callable=AsyncMock)
    async def test_is_active_false(self, mock_systemctl):
        mock_systemctl.return_value = (3, "inactive")
        assert await is_active() is False

    @pytest.mark.asyncio
    @patch("services.hailo_ollama_manager._systemctl", new_callable=AsyncMock)
    async def test_is_enabled_true(self, mock_systemctl):
        mock_systemctl.return_value = (0, "enabled")
        assert await is_enabled() is True

    @pytest.mark.asyncio
    @patch("services.hailo_ollama_manager._systemctl", new_callable=AsyncMock)
    async def test_is_enabled_false(self, mock_systemctl):
        mock_systemctl.return_value = (1, "disabled")
        assert await is_enabled() is False

    @pytest.mark.asyncio
    @patch("services.hailo_ollama_manager._systemctl", new_callable=AsyncMock)
    async def test_stop_and_disable(self, mock_systemctl):
        mock_systemctl.return_value = (0, "")
        await stop_and_disable()
        assert mock_systemctl.call_count == 2
