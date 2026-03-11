"""Tests for app/main.py API routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import main as main_mod
import pytest
from httpx import ASGITransport


@pytest.fixture()
async def client(mock_config):
    """Create a test client with mocked startup dependencies."""
    with (
        patch.object(main_mod, "registry") as mock_reg,
        patch.object(main_mod, "_start_sensor"),
        patch.object(main_mod, "_ensure_auto_trigger"),
        patch.object(main_mod, "run_pipeline", new_callable=AsyncMock) as mock_pipeline,
        patch.object(main_mod, "ClassifierClient") as mock_classifier,
        patch.object(main_mod, "LLMClient") as mock_llm,
        patch.object(main_mod, "OllamaClient") as mock_ollama,
        patch.object(main_mod, "TTSClient") as mock_tts,
        patch.object(main_mod, "RemoteSaveClient") as mock_remote,
        patch.object(main_mod, "training_data") as mock_td,
    ):
        mock_reg.load = MagicMock()
        mock_reg.list_all = MagicMock(return_value=[])
        mock_reg.get_pipeline = MagicMock(return_value=[])
        mock_reg.refresh_all_manifests = AsyncMock()
        mock_reg.health_check_all = AsyncMock(return_value={})

        transport = ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield {
                "client": c,
                "pipeline": mock_pipeline,
                "classifier": mock_classifier,
                "llm": mock_llm,
                "ollama": mock_ollama,
                "tts": mock_tts,
                "remote": mock_remote,
                "training_data": mock_td,
                "registry": mock_reg,
            }


class TestHealthAndIndex:
    @pytest.mark.asyncio
    async def test_health(self, client):
        resp = await client["client"].get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_index_returns_html(self, client):
        resp = await client["client"].get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestBrewAPI:
    @pytest.mark.asyncio
    async def test_brew_returns_pipeline_result(self, client):
        client["pipeline"].return_value = {"label": "espresso", "text": "Nice!"}
        resp = await client["client"].post("/api/brew")
        assert resp.status_code == 200
        assert resp.json()["label"] == "espresso"


class TestServicesStatus:
    @pytest.mark.asyncio
    async def test_services_status_mock_mode(self, client):
        # Patch config attributes read by services_status
        with patch("main.config") as cfg:
            cfg.CLASSIFIER_ENABLED = False
            cfg.LLM_ENABLED = False
            cfg.TTS_ENABLED = False
            cfg.REMOTE_SAVE_ENABLED = False
            cfg.SENSOR_MODE = "mock"
            client["registry"].health_check_all = AsyncMock(return_value={})
            resp = await client["client"].get("/api/services/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["classifier"]["enabled"] is False
        assert data["sensor"]["mode"] == "mock"


class TestServiceSettingsProxy:
    @pytest.mark.asyncio
    async def test_get_settings_unknown_service(self, client):
        resp = await client["client"].get("/api/services/unknown/settings")
        assert resp.status_code == 200
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_get_settings_classifier(self, client):
        client["classifier"].get_settings = AsyncMock(
            return_value=[{"key": "n_estimators", "value": 200}]
        )
        resp = await client["client"].get("/api/services/classifier/settings")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_settings_unknown(self, client):
        resp = await client["client"].patch(
            "/api/services/unknown/settings", json={"settings": {"key": "value"}}
        )
        assert "error" in resp.json()


class TestDataCollectAPI:
    @pytest.mark.asyncio
    async def test_collect_start(self, client):
        resp = await client["client"].post(
            "/api/collect/start", json={"label": "espresso"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "collecting"

    @pytest.mark.asyncio
    async def test_collect_start_empty_label(self, client):
        resp = await client["client"].post(
            "/api/collect/start", json={"label": ""}
        )
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_collect_stop(self, client):
        resp = await client["client"].post("/api/collect/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"


class TestTrainingDataAPI:
    @pytest.mark.asyncio
    async def test_list_training_data(self, client):
        client["training_data"].list_training_data = MagicMock(return_value={"espresso": ["f.csv"]})
        resp = await client["client"].get("/api/training-data")
        assert resp.status_code == 200
        assert "training_data" in resp.json()

    @pytest.mark.asyncio
    async def test_delete_training_file_ok(self, client):
        client["training_data"].delete_training_file = MagicMock(return_value=True)
        resp = await client["client"].delete("/api/training-data/espresso/test.csv")
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_training_file_not_found(self, client):
        client["training_data"].delete_training_file = MagicMock(return_value=False)
        resp = await client["client"].delete("/api/training-data/espresso/missing.csv")
        assert "error" in resp.json()


class TestModelTrainingAPI:
    @pytest.mark.asyncio
    async def test_train_success(self, client):
        client["classifier"].train = AsyncMock(return_value={"accuracy": 0.92})
        resp = await client["client"].post("/api/train", json={})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_train_unreachable(self, client):
        client["classifier"].train = AsyncMock(return_value=None)
        resp = await client["client"].post("/api/train", json={})
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_model_info(self, client):
        client["classifier"].model_info = AsyncMock(return_value={"model_name": "rf"})
        resp = await client["client"].get("/api/model/info")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_model_info_unreachable(self, client):
        client["classifier"].model_info = AsyncMock(return_value=None)
        resp = await client["client"].get("/api/model/info")
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_train_status(self, client):
        client["classifier"].train_status = AsyncMock(return_value={"is_training": False})
        resp = await client["client"].get("/api/train/status")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_train_status_unreachable(self, client):
        client["classifier"].train_status = AsyncMock(return_value=None)
        resp = await client["client"].get("/api/train/status")
        assert "error" in resp.json()


class TestTrainingDataDeleteAPIs:
    @pytest.mark.asyncio
    async def test_delete_training_label(self, client):
        client["training_data"].delete_all_training_data = MagicMock(return_value=3)
        resp = await client["client"].delete("/api/training-data/espresso")
        assert resp.json()["status"] == "deleted"
        assert resp.json()["count"] == 3

    @pytest.mark.asyncio
    async def test_delete_all_training(self, client):
        client["training_data"].delete_all_training_data = MagicMock(return_value=5)
        resp = await client["client"].delete("/api/training-data")
        assert resp.json()["count"] == 5


class TestDataFilesAPI:
    @pytest.mark.asyncio
    async def test_list_data_files(self, client):
        client["training_data"].list_sample_files = MagicMock(return_value=[{"filename": "test.csv.sample"}])
        resp = await client["client"].get("/api/data-files")
        assert resp.status_code == 200
        assert "files" in resp.json()

    @pytest.mark.asyncio
    async def test_delete_data_file_ok(self, client):
        client["training_data"].delete_sample_file = MagicMock(return_value=True)
        resp = await client["client"].delete("/api/data-files/test.csv.sample")
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_data_file_not_found(self, client):
        client["training_data"].delete_sample_file = MagicMock(return_value=False)
        resp = await client["client"].delete("/api/data-files/missing.csv.sample")
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_promote_to_sample(self, client):
        client["training_data"].promote_training_to_sample = MagicMock(return_value="espresso-test.csv.sample")
        resp = await client["client"].post(
            "/api/data-files/promote", json={"label": "espresso", "filename": "test.csv"}
        )
        assert resp.json()["status"] == "promoted"

    @pytest.mark.asyncio
    async def test_promote_missing_params(self, client):
        resp = await client["client"].post("/api/data-files/promote", json={"label": ""})
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_promote_not_found(self, client):
        client["training_data"].promote_training_to_sample = MagicMock(return_value=None)
        resp = await client["client"].post(
            "/api/data-files/promote", json={"label": "espresso", "filename": "missing.csv"}
        )
        assert "error" in resp.json()


class TestSensorRestart:
    @pytest.mark.asyncio
    async def test_sensor_restart(self, client):
        with patch.object(main_mod, "restart_sensor", new_callable=AsyncMock) as mock_restart:
            mock_restart.return_value = {"mode": "mock", "restarted": True}
            resp = await client["client"].post("/api/sensor/restart")
            assert resp.status_code == 200


class TestPatchSettings:
    @pytest.mark.asyncio
    async def test_patch_settings_success(self, client):
        client["classifier"].update_settings = AsyncMock(return_value={"ok": True})
        resp = await client["client"].patch(
            "/api/services/classifier/settings",
            json={"settings": {"n_estimators": 100}},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_settings_failure(self, client):
        client["classifier"].update_settings = AsyncMock(return_value=None)
        resp = await client["client"].patch(
            "/api/services/classifier/settings",
            json={"settings": {"n_estimators": 100}},
        )
        assert "error" in resp.json()
