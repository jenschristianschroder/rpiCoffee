"""Tests for app/pipeline_engine.py — PipelineEngine and PipelineContext."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from models.manifest import (
    ManifestEndpoint,
    ManifestEndpoints,
    ManifestInput,
    ManifestOutput,
    ServiceManifest,
)
from models.registry import PipelineStep, ServiceRegistration
from pipeline_engine import PipelineContext, PipelineEngine
from registry import ServiceRegistry


def _make_manifest(name: str, inputs: list[dict], outputs: list[dict]) -> ServiceManifest:
    return ServiceManifest(
        name=name,
        version="1.0.0",
        description=f"{name} service",
        inputs=[ManifestInput(**i) for i in inputs],
        outputs=[ManifestOutput(**o) for o in outputs],
        endpoints=ManifestEndpoints(
            execute=ManifestEndpoint(method="POST", path=f"/{name}"),
            health=ManifestEndpoint(method="GET", path="/health"),
        ),
    )


def _make_registry(services: dict, steps: list[PipelineStep]) -> ServiceRegistry:
    reg = ServiceRegistry()
    for name, (endpoint, manifest) in services.items():
        reg._config.services[name] = ServiceRegistration(
            name=name, endpoint=endpoint, manifest=manifest
        )
    reg._config.pipeline = steps
    return reg


@pytest.fixture()
def sensor_ts() -> datetime:
    return datetime(2026, 3, 11, 10, 0, 0, tzinfo=timezone.utc)


class TestPipelineContext:
    def test_resolve_sensor_data(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        assert ctx.resolve_ref("$sensor.data") == sample_sensor_data

    def test_resolve_sensor_timestamp(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        assert ctx.resolve_ref("$sensor.timestamp") == sensor_ts.isoformat()

    def test_resolve_step_result(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        ctx.results["classifier"] = {"label": "espresso", "confidence": 0.95}
        assert ctx.resolve_ref("$classifier.label") == "espresso"
        assert ctx.resolve_ref("$classifier.confidence") == 0.95

    def test_resolve_missing_ref(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        assert ctx.resolve_ref("$nonexistent.key") is None

    def test_resolve_non_ref(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        assert ctx.resolve_ref("plain_value") == "plain_value"

    def test_resolve_invalid_format(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        assert ctx.resolve_ref("$noDot") is None

    def test_resolve_config_ref(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        with patch("pipeline_engine.config") as mock_cfg:
            mock_cfg.get.return_value = "You are a witty coffee commentator."
            value = ctx.resolve_ref("$config.LLM_SYSTEM_MESSAGE")
        assert value == "You are a witty coffee commentator."
        mock_cfg.get.assert_called_once_with("LLM_SYSTEM_MESSAGE")

    def test_resolve_config_ref_missing_key(self, sample_sensor_data, sensor_ts):
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        with patch("pipeline_engine.config") as mock_cfg:
            mock_cfg.get.return_value = None
            value = ctx.resolve_ref("$config.NO_SUCH_KEY")
        assert value is None


class TestPipelineEngine:
    @pytest.mark.asyncio
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_execute_happy_path(self, mock_call, sample_sensor_data, sensor_ts):
        manifest = _make_manifest(
            "classifier",
            inputs=[{"name": "data", "type": "array", "required": True, "description": ""}],
            outputs=[{"name": "label", "type": "string", "description": ""},
                     {"name": "confidence", "type": "float", "description": ""}],
        )
        mock_call.return_value = {"label": "espresso", "confidence": 0.95}

        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [PipelineStep(service="classifier", input_map={"data": "$sensor.data"})],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)

        assert "classifier" in ctx.results
        assert ctx.results["classifier"]["label"] == "espresso"
        assert not ctx.halted
        assert ctx.errors == {}

    @pytest.mark.asyncio
    async def test_execute_disabled_step(self, sample_sensor_data, sensor_ts):
        manifest = _make_manifest("classifier", [], [])
        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [PipelineStep(service="classifier", enabled=False)],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)
        assert "classifier" in ctx.skipped

    @pytest.mark.asyncio
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_execute_skip_on_failure(self, mock_call, sample_sensor_data, sensor_ts):
        from pipeline_executor import ServiceCallError
        mock_call.side_effect = ServiceCallError("svc", "timeout")

        manifest = _make_manifest("classifier",
            inputs=[{"name": "data", "type": "array", "required": True, "description": ""}],
            outputs=[])
        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [PipelineStep(service="classifier", input_map={"data": "$sensor.data"}, on_failure="skip")],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)
        assert "classifier" in ctx.errors
        assert not ctx.halted

    @pytest.mark.asyncio
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_execute_halt_on_failure(self, mock_call, sample_sensor_data, sensor_ts):
        from pipeline_executor import ServiceCallError
        mock_call.side_effect = ServiceCallError("svc", "timeout")

        manifest = _make_manifest("classifier",
            inputs=[{"name": "data", "type": "array", "required": True, "description": ""}],
            outputs=[])
        second_manifest = _make_manifest("llm", [], [])
        reg = _make_registry(
            {
                "classifier": ("http://localhost:8001", manifest),
                "llm": ("http://localhost:8002", second_manifest),
            },
            [
                PipelineStep(service="classifier", input_map={"data": "$sensor.data"}, on_failure="halt"),
                PipelineStep(service="llm"),
            ],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)
        assert ctx.halted
        assert "llm" in ctx.skipped

    @pytest.mark.asyncio
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_execute_retry(self, mock_call, sample_sensor_data, sensor_ts):
        from pipeline_executor import ServiceCallError
        mock_call.side_effect = [
            ServiceCallError("svc", "fail1"),
            ServiceCallError("svc", "fail2"),
            {"label": "espresso", "confidence": 0.9},
        ]
        manifest = _make_manifest("classifier",
            inputs=[{"name": "data", "type": "array", "required": True, "description": ""}],
            outputs=[{"name": "label", "type": "string", "description": ""}])
        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [PipelineStep(service="classifier", input_map={"data": "$sensor.data"},
                          on_failure="retry", retry_count=3)],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)
        assert "classifier" in ctx.results
        assert mock_call.call_count == 3

    @pytest.mark.asyncio
    async def test_execute_missing_required_input(self, sample_sensor_data, sensor_ts):
        manifest = _make_manifest("llm",
            inputs=[{"name": "coffee_label", "type": "string", "required": True, "description": ""}],
            outputs=[])
        reg = _make_registry(
            {"llm": ("http://localhost:8002", manifest)},
            [PipelineStep(service="llm", input_map={"coffee_label": "$classifier.label"})],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)
        assert "llm" in ctx.errors

    @pytest.mark.asyncio
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_execute_streaming_yields_events(self, mock_call, sample_sensor_data, sensor_ts):
        mock_call.return_value = {"label": "espresso", "confidence": 0.9}
        manifest = _make_manifest("classifier",
            inputs=[{"name": "data", "type": "array", "required": True, "description": ""}],
            outputs=[{"name": "label", "type": "string", "description": ""},
                     {"name": "confidence", "type": "float", "description": ""}])
        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [PipelineStep(service="classifier", input_map={"data": "$sensor.data"})],
        )
        engine = PipelineEngine(reg)
        events = []
        async for event in engine.execute_streaming(sample_sensor_data, sensor_ts):
            events.append(event)
        event_types = [e["event"] for e in events]
        assert "step_start" in event_types
        assert "step_complete" in event_types
        assert "pipeline_complete" in event_types

    def test_build_summary(self, sample_sensor_data, sensor_ts):
        manifest = _make_manifest("classifier", [], [])
        reg = _make_registry(
            {"classifier": ("http://localhost:8001", manifest)},
            [],
        )
        engine = PipelineEngine(reg)
        ctx = PipelineContext(sample_sensor_data, sensor_ts)
        ctx.results["classifier"] = {"label": "espresso", "confidence": 0.9}
        summary = engine._build_summary(ctx)
        assert "label" in summary or "steps_completed" in summary

    @pytest.mark.asyncio
    @patch("pipeline_engine.config")
    @patch("pipeline_engine.call_service", new_callable=AsyncMock)
    async def test_config_ref_passed_to_service(self, mock_call, mock_cfg,
                                                 sample_sensor_data, sensor_ts):
        """Pipeline resolves $config.LLM_SYSTEM_MESSAGE and sends it in the payload."""
        mock_cfg.get.return_value = "Custom system prompt"
        mock_call.return_value = {"response": "Funny comment", "tokens": 10}

        manifest = _make_manifest(
            "llm",
            inputs=[
                {"name": "coffee_label", "type": "string", "required": True, "description": ""},
                {"name": "system", "type": "string", "required": False, "description": ""},
            ],
            outputs=[{"name": "response", "type": "string", "description": ""}],
        )
        reg = _make_registry(
            {"llm": ("http://localhost:8002", manifest)},
            [PipelineStep(
                service="llm",
                input_map={
                    "coffee_label": "$sensor.timestamp",  # dummy; just needs a value
                    "system": "$config.LLM_SYSTEM_MESSAGE",
                },
            )],
        )
        engine = PipelineEngine(reg)
        ctx = await engine.execute(sample_sensor_data, sensor_ts)

        assert "llm" in ctx.results
        # Verify the payload sent to call_service included the system prompt
        call_kwargs = mock_call.call_args
        payload = call_kwargs.kwargs.get("payload") or call_kwargs[1].get("payload")
        if payload is None:
            # positional args: endpoint, method, path, payload
            payload = call_kwargs[0][3]
        assert payload["system"] == "Custom system prompt"
