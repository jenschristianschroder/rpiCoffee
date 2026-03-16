"""Tests for Pydantic models in app/models/."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestManifestModels:
    """Tests for ServiceManifest and related models."""

    def test_valid_manifest(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        m = ServiceManifest.model_validate(sample_manifest_dict)
        assert m.name == "test-service"
        assert m.version == "1.0.0"
        assert len(m.inputs) == 1
        assert len(m.outputs) == 2
        assert m.endpoints.execute.method == "POST"
        assert m.endpoints.execute.path == "/classify"

    def test_manifest_missing_name(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        del sample_manifest_dict["name"]
        with pytest.raises(ValidationError):
            ServiceManifest.model_validate(sample_manifest_dict)

    def test_manifest_missing_endpoints(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        del sample_manifest_dict["endpoints"]
        with pytest.raises(ValidationError):
            ServiceManifest.model_validate(sample_manifest_dict)

    def test_manifest_defaults(self):
        from models.manifest import ServiceManifest
        m = ServiceManifest(
            name="svc",
            version="0.1.0",
            description="desc",
            endpoints={
                "execute": {"method": "POST", "path": "/run"},
                "health": {"method": "GET", "path": "/health"},
            },
        )
        assert m.inputs == []
        assert m.outputs == []
        assert m.failure_modes == ["skip", "halt"]

    def test_manifest_input(self):
        from models.manifest import ManifestInput
        inp = ManifestInput(name="data", type="array", required=True, description="sensor data")
        assert inp.name == "data"
        assert inp.required is True

    def test_manifest_output(self):
        from models.manifest import ManifestOutput
        out = ManifestOutput(name="label", type="string", description="predicted label")
        assert out.name == "label"
        assert out.type == "string"

    def test_manifest_roundtrip(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        m = ServiceManifest.model_validate(sample_manifest_dict)
        dumped = m.model_dump()
        m2 = ServiceManifest.model_validate(dumped)
        assert m == m2


class TestRegistryModels:
    """Tests for ServiceRegistration, PipelineStep, PipelineConfig."""

    def test_service_registration(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        from models.registry import ServiceRegistration
        manifest = ServiceManifest.model_validate(sample_manifest_dict)
        reg = ServiceRegistration(name="test", endpoint="http://localhost:8001", manifest=manifest)
        assert reg.name == "test"
        assert reg.enabled is True
        assert reg.manifest is not None

    def test_service_registration_no_manifest(self):
        from models.registry import ServiceRegistration
        reg = ServiceRegistration(name="test", endpoint="http://localhost:8001")
        assert reg.manifest is None
        assert reg.enabled is True

    def test_pipeline_step_defaults(self):
        from models.registry import PipelineStep
        step = PipelineStep(service="classifier")
        assert step.on_failure == "skip"
        assert step.retry_count == 1
        assert step.timeout is None
        assert step.enabled is True
        assert step.input_map == {}

    def test_pipeline_step_with_timeout(self):
        from models.registry import PipelineStep
        step = PipelineStep(service="llm", timeout=120.0)
        assert step.timeout == 120.0

    def test_pipeline_step_timeout_none(self):
        from models.registry import PipelineStep
        step = PipelineStep(service="classifier", timeout=None)
        assert step.timeout is None

    def test_pipeline_step_with_input_map(self):
        from models.registry import PipelineStep
        step = PipelineStep(
            service="classifier",
            input_map={"data": "$sensor.data"},
            on_failure="halt",
        )
        assert step.input_map["data"] == "$sensor.data"
        assert step.on_failure == "halt"

    def test_pipeline_config_empty(self):
        from models.registry import PipelineConfig
        cfg = PipelineConfig()
        assert cfg.services == {}
        assert cfg.pipeline == []

    def test_pipeline_config_roundtrip(self, sample_manifest_dict):
        from models.manifest import ServiceManifest
        from models.registry import PipelineConfig, PipelineStep, ServiceRegistration
        manifest = ServiceManifest.model_validate(sample_manifest_dict)
        reg = ServiceRegistration(name="test", endpoint="http://localhost:8001", manifest=manifest)
        step = PipelineStep(service="test", input_map={"data": "$sensor.data"})
        cfg = PipelineConfig(services={"test": reg}, pipeline=[step])

        dumped = cfg.model_dump()
        cfg2 = PipelineConfig.model_validate(dumped)
        assert len(cfg2.services) == 1
        assert len(cfg2.pipeline) == 1
        assert cfg2.pipeline[0].service == "test"
