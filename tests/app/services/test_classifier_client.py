"""Tests for app/services/classifier_client.py."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from services.classifier_client import ClassifierClient


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch):
    with patch("services.classifier_client.config") as cfg:
        cfg.CLASSIFIER_ENDPOINT = "http://classifier:8001"
        cfg.CLASSIFIER_ENABLED = True
        yield cfg


class TestClassifierClient:
    @respx.mock
    @pytest.mark.asyncio
    async def test_health_ok(self):
        respx.get("http://classifier:8001/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await ClassifierClient.health()
        assert result["healthy"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_unreachable(self):
        respx.get("http://classifier:8001/health").mock(side_effect=httpx.ConnectError("refused"))
        result = await ClassifierClient.health()
        assert result["healthy"] is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_classify_success(self, sample_sensor_data):
        respx.post("http://classifier:8001/classify").mock(
            return_value=httpx.Response(200, json={"label": "espresso", "confidence": 0.95})
        )
        result = await ClassifierClient.classify(sample_sensor_data)
        assert result["label"] == "espresso"
        assert result["confidence"] == 0.95

    @respx.mock
    @pytest.mark.asyncio
    async def test_classify_failure(self, sample_sensor_data):
        respx.post("http://classifier:8001/classify").mock(
            return_value=httpx.Response(500, text="error")
        )
        result = await ClassifierClient.classify(sample_sensor_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_classify_disabled(self, _mock_config, sample_sensor_data):
        _mock_config.CLASSIFIER_ENABLED = False
        result = await ClassifierClient.classify(sample_sensor_data)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_settings(self):
        respx.get("http://classifier:8001/settings").mock(
            return_value=httpx.Response(200, json=[{"key": "n_estimators", "value": 200}])
        )
        result = await ClassifierClient.get_settings()
        assert isinstance(result, list)

    @respx.mock
    @pytest.mark.asyncio
    async def test_update_settings(self):
        respx.patch("http://classifier:8001/settings").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = await ClassifierClient.update_settings({"n_estimators": 100})
        assert result["ok"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_train(self):
        respx.post("http://classifier:8001/train").mock(
            return_value=httpx.Response(200, json={"accuracy": 0.92})
        )
        result = await ClassifierClient.train()
        assert result["accuracy"] == 0.92

    @respx.mock
    @pytest.mark.asyncio
    async def test_model_info(self):
        respx.get("http://classifier:8001/model/info").mock(
            return_value=httpx.Response(200, json={"model_name": "test"})
        )
        result = await ClassifierClient.model_info()
        assert result["model_name"] == "test"

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_labels(self):
        respx.get("http://classifier:8001/labels").mock(
            return_value=httpx.Response(200, json={"labels": ["espresso", "black"]})
        )
        result = await ClassifierClient.get_labels()
        assert "espresso" in result
