"""Tests for app/services/tts_client.py."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx
from services.tts_client import TTSClient


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    with patch("services.tts_client.config") as cfg:
        cfg.TTS_ENDPOINT = "http://tts:5050"
        cfg.TTS_ENABLED = True
        yield cfg


class TestTTSClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self):
        respx.get("http://tts:5050/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await TTSClient.health()
        assert result["healthy"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_down(self):
        respx.get("http://tts:5050/health").mock(side_effect=httpx.ConnectError("refused"))
        result = await TTSClient.health()
        assert result["healthy"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_synthesize_success(self):
        audio_bytes = b"RIFF\x00\x00\x00\x00WAVEfmt "
        respx.post("http://tts:5050/synthesize").mock(
            return_value=httpx.Response(200, content=audio_bytes, headers={"content-type": "audio/wav"})
        )
        result = await TTSClient.synthesize("Hello world")
        assert isinstance(result, bytes)
        assert len(result) > 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_synthesize_failure(self):
        respx.post("http://tts:5050/synthesize").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await TTSClient.synthesize("Hello world")
        assert result is None

    @pytest.mark.asyncio
    async def test_synthesize_disabled(self, _mock_config):
        _mock_config.TTS_ENABLED = False
        result = await TTSClient.synthesize("Hello world")
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_settings(self):
        respx.get("http://tts:5050/settings").mock(
            return_value=httpx.Response(200, json=[{"key": "speed", "value": 1.0}])
        )
        result = await TTSClient.get_settings()
        assert isinstance(result, list)
