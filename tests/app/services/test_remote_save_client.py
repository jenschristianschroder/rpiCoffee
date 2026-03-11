"""Tests for app/services/remote_save_client.py."""

from __future__ import annotations

import base64
from unittest.mock import patch

import httpx
import pytest
import respx

from services.remote_save_client import RemoteSaveClient, _csv_to_base64, _sensor_data_to_csv


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    with patch("services.remote_save_client.config") as cfg:
        cfg.REMOTE_SAVE_ENDPOINT = "http://remote-save:7000"
        cfg.REMOTE_SAVE_ENABLED = True
        yield cfg


class TestCSVHelpers:
    def test_sensor_data_to_csv(self, sample_sensor_data):
        csv_str = _sensor_data_to_csv(sample_sensor_data[:3], "espresso")
        lines = csv_str.strip().split("\n")
        assert "label" in lines[0]
        assert len(lines) == 4  # header + 3 data rows
        assert "espresso" in lines[1]

    def test_csv_to_base64(self):
        csv_str = "a,b,c\n1,2,3\n"
        b64 = _csv_to_base64(csv_str)
        decoded = base64.b64decode(b64).decode("utf-8")
        assert decoded == csv_str


class TestRemoteSaveClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self):
        respx.get("http://remote-save:7000/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await RemoteSaveClient.health()
        assert result["healthy"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_down(self):
        respx.get("http://remote-save:7000/health").mock(side_effect=httpx.ConnectError("refused"))
        result = await RemoteSaveClient.health()
        assert result["healthy"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_save_success(self, sample_sensor_data):
        respx.post("http://remote-save:7000/save").mock(
            return_value=httpx.Response(200, json={"record_id": "abc123", "message": "ok"})
        )
        result_dict = {"label": "espresso", "confidence": 0.95, "text": "Nice brew"}
        result = await RemoteSaveClient.save(result_dict, sample_sensor_data)
        assert result is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_save_failure(self, sample_sensor_data):
        respx.post("http://remote-save:7000/save").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await RemoteSaveClient.save({"label": "espresso"}, sample_sensor_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_save_disabled(self, _mock_config, sample_sensor_data):
        _mock_config.REMOTE_SAVE_ENABLED = False
        result = await RemoteSaveClient.save({"label": "espresso"}, sample_sensor_data)
        assert result is None
