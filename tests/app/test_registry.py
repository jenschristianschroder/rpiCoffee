"""Tests for app/registry.py — ServiceRegistry."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx
from models.manifest import ServiceManifest
from models.registry import PipelineStep, ServiceRegistration
from registry import ServiceRegistry


@pytest.fixture()
def reg(tmp_path) -> ServiceRegistry:
    """Create a fresh ServiceRegistry backed by a temporary pipeline.json."""
    pipeline_path = tmp_path / "pipeline.json"
    pipeline_path.write_text(json.dumps({"services": {}, "pipeline": []}), encoding="utf-8")
    r = ServiceRegistry()
    with patch("registry.config") as mock_cfg:
        mock_cfg.get.return_value = str(pipeline_path)
        r.load(pipeline_path)
    return r


@pytest.fixture()
def manifest_dict() -> dict:
    return {
        "name": "classifier",
        "version": "1.0.0",
        "description": "Coffee classifier",
        "inputs": [{"name": "sensor_data", "type": "array", "required": True, "description": "data"}],
        "outputs": [
            {"name": "label", "type": "string", "description": "label"},
            {"name": "confidence", "type": "float", "description": "score"},
        ],
        "endpoints": {
            "execute": {"method": "POST", "path": "/classify"},
            "health": {"method": "GET", "path": "/health"},
        },
        "failure_modes": ["skip", "halt"],
    }


class TestServiceRegistry:
    @respx.mock
    @pytest.mark.asyncio
    async def test_register_fetches_manifest(self, reg, manifest_dict):
        respx.get("http://localhost:8001/manifest").mock(
            return_value=httpx.Response(200, json=manifest_dict)
        )
        result = await reg.register("classifier", "http://localhost:8001")
        assert result.name == "classifier"
        assert result.manifest is not None
        assert result.manifest.name == "classifier"
        assert result.enabled is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_register_unreachable_service(self, reg):
        respx.get("http://bad:9999/manifest").mock(side_effect=httpx.ConnectError("refused"))
        result = await reg.register("bad-svc", "http://bad:9999")
        assert result.name == "bad-svc"
        assert result.manifest is None

    def test_unregister(self, reg):
        # Manually add a service
        reg._config.services["test"] = ServiceRegistration(
            name="test", endpoint="http://test:1234"
        )
        reg._config.pipeline = [PipelineStep(service="test")]
        reg.unregister("test")
        assert reg.get("test") is None
        assert len(reg.get_pipeline()) == 0

    def test_get_returns_none_for_missing(self, reg):
        assert reg.get("nonexistent") is None

    def test_list_all_empty(self, reg):
        assert reg.list_all() == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_list_all_with_services(self, reg, manifest_dict):
        respx.get("http://localhost:8001/manifest").mock(
            return_value=httpx.Response(200, json=manifest_dict)
        )
        await reg.register("classifier", "http://localhost:8001")
        services = reg.list_all()
        assert len(services) == 1
        assert services[0].name == "classifier"

    def test_set_enabled(self, reg):
        reg._config.services["svc"] = ServiceRegistration(name="svc", endpoint="http://x")
        reg.set_enabled("svc", False)
        assert reg.get("svc").enabled is False

    def test_get_set_pipeline(self, reg):
        steps = [PipelineStep(service="a"), PipelineStep(service="b")]
        reg.set_pipeline(steps)
        result = reg.get_pipeline()
        assert len(result) == 2
        assert result[0].service == "a"

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_healthy(self, reg, manifest_dict):
        respx.get("http://localhost:8001/manifest").mock(
            return_value=httpx.Response(200, json=manifest_dict)
        )
        await reg.register("classifier", "http://localhost:8001")

        respx.get("http://localhost:8001/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await reg.health_check("classifier")
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_not_registered(self, reg):
        result = await reg.health_check("missing")
        assert result["status"] == "unknown"

    @respx.mock
    @pytest.mark.asyncio
    async def test_health_check_unreachable(self, reg):
        reg._config.services["dead"] = ServiceRegistration(name="dead", endpoint="http://dead:9999")
        respx.get("http://dead:9999/health").mock(side_effect=httpx.ConnectError("refused"))
        result = await reg.health_check("dead")
        assert result["status"] == "unreachable"

    def test_validate_pipeline_empty(self, reg):
        issues = reg.validate_pipeline()
        assert issues == []

    def test_validate_pipeline_missing_service(self, reg):
        reg.set_pipeline([PipelineStep(service="nonexistent")])
        issues = reg.validate_pipeline()
        assert any("not registered" in i for i in issues)

    def test_validate_pipeline_missing_required_input(self, reg, manifest_dict):
        manifest = ServiceManifest.model_validate(manifest_dict)
        reg._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://localhost:8001", manifest=manifest
        )
        reg.set_pipeline([PipelineStep(service="classifier", input_map={})])
        issues = reg.validate_pipeline()
        assert any("required input 'sensor_data' is not mapped" in i for i in issues)

    def test_validate_pipeline_valid_wiring(self, reg, manifest_dict):
        manifest = ServiceManifest.model_validate(manifest_dict)
        reg._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://localhost:8001", manifest=manifest
        )
        reg.set_pipeline([PipelineStep(
            service="classifier",
            input_map={"sensor_data": "$sensor.data"},
        )])
        issues = reg.validate_pipeline()
        assert issues == []

    def test_validate_pipeline_invalid_source_ref(self, reg, manifest_dict):
        manifest = ServiceManifest.model_validate(manifest_dict)
        reg._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://localhost:8001", manifest=manifest
        )
        reg.set_pipeline([PipelineStep(
            service="classifier",
            input_map={"sensor_data": "$nonexistent.data"},
        )])
        issues = reg.validate_pipeline()
        assert any("hasn't produced output" in i for i in issues)

    def test_persistence_roundtrip(self, reg, tmp_path, manifest_dict):
        manifest = ServiceManifest.model_validate(manifest_dict)
        reg._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://localhost:8001", manifest=manifest
        )
        reg.set_pipeline([PipelineStep(service="classifier")])
        reg.save()

        # Load into a new registry
        reg2 = ServiceRegistry()
        reg2.load(reg._path)
        assert reg2.get("classifier") is not None
        assert len(reg2.get_pipeline()) == 1
