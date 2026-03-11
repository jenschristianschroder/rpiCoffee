"""Tests for services/classifier/main.py — FastAPI endpoints."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

_SVC_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "services" / "classifier")


def _import_svc_main():
    """Import the classifier main module under a unique name."""
    if _SVC_DIR not in sys.path:
        sys.path.insert(0, _SVC_DIR)
    mod_key = "svc_classifier_main"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    spec = importlib.util.spec_from_file_location(mod_key, Path(_SVC_DIR) / "main.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mock_model_manager():
    """Provide a mocked model_manager before importing the app."""
    mm = MagicMock()
    mm.is_ready = True
    mm.training_status.is_training = False
    mm.training_status.to_dict.return_value = {"is_training": False, "progress": "done"}
    mm.predict.return_value = {"label": "espresso", "confidence": 0.95}
    mm.get_info.return_value = {"loaded": True, "model_name": "test"}
    return mm


@pytest.fixture()
async def client(mock_model_manager, tmp_path):
    with patch.dict("os.environ", {
        "SETTINGS_DIR": str(tmp_path),
        "MODEL_DIR": str(tmp_path / "models"),
        "TRAINING_DIR": str(tmp_path / "training"),
    }):
        svc = _import_svc_main()
        svc.model_manager = mock_model_manager

        transport = ASGITransport(app=svc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield {"client": c, "model_manager": mock_model_manager}


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        resp = await client["client"].get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestManifest:
    @pytest.mark.asyncio
    async def test_manifest(self, client):
        resp = await client["client"].get("/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "classifier"
        assert "inputs" in data
        assert "endpoints" in data


class TestClassify:
    @pytest.mark.asyncio
    async def test_classify_success(self, client):
        payload = {
            "data": [
                {"acc_x": 0.1, "acc_y": 0.2, "acc_z": 9.8, "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0}
                for _ in range(10)
            ]
        }
        resp = await client["client"].post("/classify", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "espresso"
        assert data["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_classify_empty_data(self, client):
        resp = await client["client"].post("/classify", json={"data": []})
        assert resp.status_code == 422  # validation error


class TestTrain:
    @pytest.mark.asyncio
    async def test_train_starts(self, client):
        resp = await client["client"].post("/train")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    @pytest.mark.asyncio
    async def test_train_status(self, client):
        resp = await client["client"].get("/train/status")
        assert resp.status_code == 200


class TestModelInfo:
    @pytest.mark.asyncio
    async def test_model_info(self, client):
        resp = await client["client"].get("/model/info")
        assert resp.status_code == 200
