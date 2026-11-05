"""Exercise manual registration and pipeline wiring with the bundled generator."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from models.manifest import ServiceManifest
from models.registry import PipelineStep, ServiceRegistration
from pipeline_engine import PipelineContext, PipelineEngine
from registry import ServiceRegistry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def comments_app(tmp_path, monkeypatch):
    service = ROOT / "services" / "coffee-comments"
    monkeypatch.syspath_prepend(str(service))
    spec = importlib.util.spec_from_file_location("coffee_comments_api", service / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_app(history_db=tmp_path / "history.sqlite3")


@pytest.mark.parametrize("coffee", ["black", "cappuccino", "espresso", "other"])
async def test_registered_service_reaches_tts_save_and_kiosk(
    comments_app, coffee, sample_sensor_data, tmp_path, monkeypatch,
):
    reg = ServiceRegistry()
    reg.load(tmp_path / "pipeline.json")
    assert reg.list_all() == []  # Registration and pipeline selection remain manual.
    original = json.loads((ROOT / "data" / "pipeline.json").read_text())
    for name in ("classifier", "tts", "remote-save"):
        reg._config.services[name] = ServiceRegistration.model_validate(original["services"][name])

    endpoint = "http://127.0.0.1:8004"
    transport = httpx.ASGITransport(app=comments_app)
    captured: dict[str, dict] = {}
    async with httpx.AsyncClient(transport=transport, base_url=endpoint) as comments:
        manifest_response = await comments.get("/manifest")
        ServiceManifest.model_validate(manifest_response.json())

        async def generate(request: httpx.Request) -> httpx.Response:
            captured["generate"] = json.loads(request.content)
            response = await comments.post("/generate", json=captured["generate"])
            return httpx.Response(response.status_code, json=response.json())

        def synthesize(request: httpx.Request) -> httpx.Response:
            captured["tts"] = json.loads(request.content)
            return httpx.Response(200, content=b"RIFF-test-audio", headers={"Content-Type": "audio/wav"})

        def save(request: httpx.Request) -> httpx.Response:
            captured["save"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok"})

        with respx.mock() as router:
            router.get(endpoint + "/manifest").respond(200, json=manifest_response.json())
            await reg.register("coffee-comments", endpoint)
            router.post(endpoint + "/generate").mock(side_effect=generate)
            router.post("http://127.0.0.1:8001/classify").respond(
                200, json={"label": coffee, "confidence": 0.9},
            )
            router.post("http://127.0.0.1:5050/synthesize").mock(side_effect=synthesize)
            router.post("http://127.0.0.1:7000/save").mock(side_effect=save)
            steps = [PipelineStep.model_validate(step) for step in original["pipeline"]]
            steps[1] = PipelineStep(
                service="coffee-comments",
                input_map={"coffee_label": "$classifier.label", "timestamp": "$sensor.timestamp"},
            )
            steps[2].input_map["text"] = "$coffee-comments.response"
            steps[3].input_map["text"] = "$coffee-comments.response"
            reg.set_pipeline(steps)
            assert reg.validate_pipeline() == []
            monkeypatch.setattr("pipeline_engine.AUDIO_DIR", tmp_path / "audio")
            moment = datetime(2026, 11, 5, 8, 30, tzinfo=timezone.utc)
            events = [event async for event in PipelineEngine(reg).execute_streaming(sample_sensor_data, moment)]

    summary = events[-1]["data"]
    text = summary["text"]
    assert summary["error"] is None
    assert summary["steps_completed"] == ["classifier", "coffee-comments", "tts", "remote-save"]
    assert summary["label"] == coffee
    assert "9:30 a.m." in text
    assert {"event": "text", "data": {"text": text}} in events
    assert captured["generate"] == {"coffee_label": coffee, "timestamp": moment.isoformat()}
    assert captured["tts"]["text"] == captured["save"]["text"] == text
    assert captured["save"]["coffee_type"] == coffee
    assert summary["audio_url"].startswith("/audio/")
    assert next((tmp_path / "audio").glob("*.wav")).read_bytes() == b"RIFF-test-audio"

    # Manual configuration survives an app restart.
    reloaded = ServiceRegistry()
    reloaded.load(tmp_path / "pipeline.json")
    assert reloaded.get("coffee-comments").endpoint == endpoint
    assert reloaded.get_pipeline()[1].service == "coffee-comments"


@pytest.mark.parametrize("provider", ["llm", "llm-ollama", "coffee-comments"])
def test_all_comment_providers_keep_the_kiosk_contract(provider):
    ctx = PipelineContext([], datetime.now(timezone.utc))
    ctx.results[provider] = {"response": "Your comment."}
    engine = PipelineEngine(ServiceRegistry())
    assert engine._build_summary(ctx)["text"] == "Your comment."
    assert engine._legacy_events(provider, ctx.results[provider], ctx) == [
        {"event": "text", "data": {"text": "Your comment."}},
    ]
