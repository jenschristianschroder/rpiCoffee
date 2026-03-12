"""Tests for API routes in app/api/registry_routes.py."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx
from models.manifest import (
    ManifestEndpoint,
    ManifestEndpoints,
    ServiceManifest,
)
from models.registry import ServiceRegistration
from registry import ServiceRegistry


def _make_manifest() -> ServiceManifest:
    return ServiceManifest(
        name="classifier",
        version="1.0.0",
        description="test",
        inputs=[],
        outputs=[],
        endpoints=ManifestEndpoints(
            execute=ManifestEndpoint(method="POST", path="/classify"),
            health=ManifestEndpoint(method="GET", path="/health"),
        ),
    )


@pytest.fixture()
def mock_registry():
    """Patch the global registry singleton used by registry_routes."""
    reg = ServiceRegistry()
    reg._loaded = True
    with patch("api.registry_routes.registry", reg):
        yield reg


@pytest.fixture()
def test_client(mock_registry):
    from api.registry_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestServiceCRUD:
    @pytest.mark.asyncio
    async def test_list_services_empty(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.get("/api/registry/services")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_register_service(self, test_client, mock_registry):
        respx.get("http://localhost:8001/manifest").mock(
            return_value=httpx.Response(200, json={
                "name": "classifier", "version": "1.0.0", "description": "test",
                "inputs": [], "outputs": [],
                "endpoints": {"execute": {"method": "POST", "path": "/classify"},
                              "health": {"method": "GET", "path": "/health"}},
            })
        )
        async with test_client as client:
            resp = await client.post("/api/registry/services", json={
                "name": "classifier", "endpoint": "http://localhost:8001"
            })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "classifier"

    @pytest.mark.asyncio
    async def test_register_duplicate(self, test_client, mock_registry):
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://x"
        )
        async with test_client as client:
            resp = await client.post("/api/registry/services", json={
                "name": "classifier", "endpoint": "http://localhost:8001"
            })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_unregister_service(self, test_client, mock_registry):
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://x"
        )
        async with test_client as client:
            resp = await client.delete("/api/registry/services/classifier")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_unregister_missing(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.delete("/api/registry/services/nonexistent")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_set_enabled(self, test_client, mock_registry):
        mock_registry._config.services["svc"] = ServiceRegistration(
            name="svc", endpoint="http://x"
        )
        async with test_client as client:
            resp = await client.patch("/api/registry/services/svc/enabled",
                                      json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


class TestPipelineEndpoints:
    @pytest.mark.asyncio
    async def test_get_pipeline(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.get("/api/registry/pipeline")
        assert resp.status_code == 200
        assert "pipeline" in resp.json()

    @pytest.mark.asyncio
    async def test_set_pipeline(self, test_client, mock_registry):
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://x"
        )
        async with test_client as client:
            resp = await client.put("/api/registry/pipeline", json={
                "pipeline": [{"service": "classifier", "input_map": {}, "on_failure": "skip",
                             "retry_count": 1, "enabled": True}]
            })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_set_pipeline_unregistered_service(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.put("/api/registry/pipeline", json={
                "pipeline": [{"service": "fake", "input_map": {}, "on_failure": "skip",
                             "retry_count": 1, "enabled": True}]
            })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_validate_pipeline(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.post("/api/registry/pipeline/validate")
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data
        assert "issues" in data

    @pytest.mark.asyncio
    async def test_validate_pipeline_with_body_valid(self, test_client, mock_registry):
        """Validate a pipeline supplied in the request body (current canvas state)."""
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier",
            endpoint="http://x",
            manifest=_make_manifest(),
        )
        async with test_client as client:
            resp = await client.post(
                "/api/registry/pipeline/validate",
                json={"pipeline": [{"service": "classifier", "input_map": {}, "on_failure": "skip",
                                    "retry_count": 1, "enabled": True}]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data
        assert "issues" in data

    @pytest.mark.asyncio
    async def test_validate_pipeline_with_body_missing_required_input(self, test_client, mock_registry):
        """Validate endpoint returns issue when required input is absent in the supplied pipeline."""
        from models.manifest import ManifestInput

        manifest = _make_manifest()
        manifest.inputs = [ManifestInput(name="sensor_data", type="array", required=True, description="data")]
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://x", manifest=manifest
        )
        async with test_client as client:
            resp = await client.post(
                "/api/registry/pipeline/validate",
                json={"pipeline": [{"service": "classifier", "input_map": {}, "on_failure": "skip",
                                    "retry_count": 1, "enabled": True}]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert any("sensor_data" in issue for issue in data["issues"])

    @pytest.mark.asyncio
    async def test_validate_pipeline_body_overrides_stored(self, test_client, mock_registry):
        """Supplying a pipeline body validates it instead of the stored pipeline."""
        from models.manifest import ManifestInput

        manifest = _make_manifest()
        manifest.inputs = [ManifestInput(name="sensor_data", type="array", required=True, description="data")]
        mock_registry._config.services["classifier"] = ServiceRegistration(
            name="classifier", endpoint="http://x", manifest=manifest
        )
        # Store a broken pipeline (missing required input)
        from models.registry import PipelineStep as _PipelineStep
        mock_registry.set_pipeline([_PipelineStep(service="classifier", input_map={})])

        # Provide a correct pipeline in the body — should pass
        async with test_client as client:
            resp = await client.post(
                "/api/registry/pipeline/validate",
                json={"pipeline": [{"service": "classifier",
                                    "input_map": {"sensor_data": "$sensor.data"},
                                    "on_failure": "skip", "retry_count": 1, "enabled": True}]},
            )
        assert resp.status_code == 200
        assert resp.json()["valid"] is True


class TestHealthEndpoints:
    @pytest.mark.asyncio
    async def test_health_all(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.get("/api/registry/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_single_not_found(self, test_client, mock_registry):
        async with test_client as client:
            resp = await client.get("/api/registry/health/nonexistent")
        assert resp.status_code == 404
