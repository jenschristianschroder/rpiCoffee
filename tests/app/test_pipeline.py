"""Tests for app/pipeline.py — run_pipeline and run_pipeline_streaming."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pipeline import _data_collect, _empty_result, _sse, run_pipeline, run_pipeline_streaming


class TestSse:
    def test_sse_dict(self):
        result = _sse("status", {"message": "hello"})
        assert result.startswith("event: status\n")
        assert '"message": "hello"' in result
        assert result.endswith("\n\n")

    def test_sse_string(self):
        result = _sse("ping", "pong")
        assert "data: pong" in result


class TestEmptyResult:
    def test_structure(self):
        r = _empty_result()
        assert r["steps_completed"] == []
        assert r["label"] is None
        assert r["error"] is None

    def test_with_error(self):
        r = _empty_result(error="something broke")
        assert r["error"] == "something broke"

    def test_all_keys_present(self):
        r = _empty_result()
        for key in ("steps_completed", "steps_skipped", "sensor_samples", "sensor_data",
                     "label", "confidence", "text", "audio_url", "error"):
            assert key in r


class TestDataCollect:
    @pytest.mark.asyncio
    @patch("pipeline.config")
    @patch("pipeline.save_recording", return_value="/data/training/espresso/test.csv")
    async def test_data_collect_success(self, mock_save, mock_config):
        mock_config.DATA_COLLECT_LABEL = "espresso"
        data = [{"acc_x": 1.0, "acc_y": 0.0, "acc_z": 9.8, "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0}]
        result = await _data_collect(data)
        assert result["label"] == "espresso"
        assert result["data_collected"] is True
        assert "sensor" in result["steps_completed"]
        mock_save.assert_called_once_with("espresso", data)

    @pytest.mark.asyncio
    @patch("pipeline.config")
    @patch("pipeline.save_recording", side_effect=OSError("disk full"))
    async def test_data_collect_failure(self, mock_save, mock_config):
        mock_config.DATA_COLLECT_LABEL = "espresso"
        result = await _data_collect([])
        assert "error" in result
        assert "disk full" in result["error"]


class TestRunPipeline:
    @pytest.mark.asyncio
    @patch("pipeline.registry")
    @patch("pipeline.PipelineEngine")
    @patch("pipeline.read_sensor", new_callable=AsyncMock)
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_run_pipeline_with_sensor_data(
        self, mock_config, mock_mock_sensor, mock_read_sensor,
        MockEngine, mock_registry, sample_sensor_data
    ):
        mock_config.SENSOR_MODE = "mock"
        mock_config.DATA_COLLECT_ENABLED = False
        mock_config.DATA_COLLECT_LABEL = ""

        # Mock engine
        mock_ctx = MagicMock()
        mock_ctx.results = {"classifier": {"label": "espresso", "confidence": 0.95}}
        mock_ctx.errors = {}
        mock_ctx.skipped = []
        mock_ctx.halted = False

        engine_instance = MockEngine.return_value
        engine_instance.execute = AsyncMock(return_value=mock_ctx)
        engine_instance._build_summary.return_value = {
            "steps_completed": ["classifier"],
            "steps_skipped": [],
            "label": "espresso",
            "confidence": 0.95,
            "text": None,
            "audio_url": None,
            "error": None,
        }

        result = await run_pipeline(sensor_data=sample_sensor_data)
        assert result["label"] == "espresso"
        assert "sensor" in result["steps_completed"]

    @pytest.mark.asyncio
    async def test_run_pipeline_empty_sensor_data(self):
        result = await run_pipeline(sensor_data=[])
        assert result["error"] is not None
        assert "No sensor data" in result["error"]

    @pytest.mark.asyncio
    @patch("pipeline.registry")
    @patch("pipeline.PipelineEngine")
    @patch("pipeline.read_sensor", new_callable=AsyncMock)
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_run_pipeline_reads_sensor_when_no_data(
        self, mock_config, mock_mock_sensor, mock_read_sensor,
        MockEngine, mock_registry, sample_sensor_data
    ):
        mock_config.SENSOR_MODE = "mock"
        mock_config.DATA_COLLECT_ENABLED = False
        mock_config.DATA_COLLECT_LABEL = ""
        mock_mock_sensor.start.return_value = "__mock__"
        mock_read_sensor.return_value = sample_sensor_data

        engine_instance = MockEngine.return_value
        engine_instance.execute = AsyncMock(return_value=MagicMock(
            results={}, errors={}, skipped=[], halted=False,
        ))
        engine_instance._build_summary.return_value = {
            "steps_completed": [], "steps_skipped": [],
            "label": None, "confidence": None, "text": None, "audio_url": None, "error": None,
        }

        result = await run_pipeline(sensor_data=None)
        mock_mock_sensor.start.assert_called_once()
        mock_read_sensor.assert_called_once()
        assert "sensor" in result["steps_completed"]

    @pytest.mark.asyncio
    @patch("pipeline.read_sensor", new_callable=AsyncMock, side_effect=RuntimeError("sensor crash"))
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_run_pipeline_sensor_exception(self, mock_config, mock_mock_sensor, mock_read_sensor):
        mock_config.SENSOR_MODE = "serial"
        result = await run_pipeline(sensor_data=None)
        assert "Sensor read failed" in result["error"]

    @pytest.mark.asyncio
    @patch("pipeline.registry")
    @patch("pipeline.PipelineEngine")
    @patch("pipeline.read_sensor", new_callable=AsyncMock)
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_run_pipeline_force_mock_uses_sample_only(
        self, mock_config, mock_mock_sensor, mock_read_sensor,
        MockEngine, mock_registry, sample_sensor_data
    ):
        """force_mock=True must start mock sensor with sample_only=True."""
        mock_config.SENSOR_MODE = "serial"  # confirm force_mock overrides config
        mock_config.DATA_COLLECT_ENABLED = False
        mock_config.DATA_COLLECT_LABEL = ""
        mock_mock_sensor.start.return_value = "__mock__"
        mock_read_sensor.return_value = sample_sensor_data

        engine_instance = MockEngine.return_value
        engine_instance.execute = AsyncMock(return_value=MagicMock(
            results={}, errors={}, skipped=[], halted=False,
        ))
        engine_instance._build_summary.return_value = {
            "steps_completed": [], "steps_skipped": [],
            "label": None, "confidence": None, "text": None, "audio_url": None, "error": None,
        }

        result = await run_pipeline(sensor_data=None, force_mock=True)
        mock_mock_sensor.start.assert_called_once_with(sample_only=True)
        assert "sensor" in result["steps_completed"]

    @pytest.mark.asyncio
    @patch("pipeline.save_recording", return_value="/data/training/espresso/x.csv")
    @patch("pipeline.config")
    async def test_run_pipeline_data_collect_mode(self, mock_config, mock_save, sample_sensor_data):
        mock_config.SENSOR_MODE = "mock"
        mock_config.DATA_COLLECT_ENABLED = True
        mock_config.DATA_COLLECT_LABEL = "espresso"
        result = await run_pipeline(sensor_data=sample_sensor_data)
        assert result["data_collected"] is True
        assert result["label"] == "espresso"


class TestRunPipelineStreaming:
    @pytest.mark.asyncio
    @patch("pipeline.registry")
    @patch("pipeline.PipelineEngine")
    @patch("pipeline.read_sensor_streaming")
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_streaming_normal(
        self, mock_config, mock_mock_sensor, mock_read_stream,
        MockEngine, mock_registry, sample_sensor_data
    ):
        mock_config.SENSOR_MODE = "mock"
        mock_config.DATA_COLLECT_ENABLED = False
        mock_config.DATA_COLLECT_LABEL = ""
        mock_mock_sensor.start.return_value = "__mock__"

        async def fake_stream(port=None):
            yield sample_sensor_data[:50]
            yield sample_sensor_data[50:]

        mock_read_stream.side_effect = fake_stream

        engine_instance = MockEngine.return_value

        async def fake_execute_streaming(data, now):
            yield {"event": "pipeline_complete", "data": {
                "steps_completed": ["classifier"], "steps_skipped": [],
                "label": "espresso", "confidence": 0.95,
                "text": None, "audio_url": None, "error": None,
            }}

        engine_instance.execute_streaming = fake_execute_streaming

        events = []
        async for sse in run_pipeline_streaming(force_mock=False, skip_save=False):
            events.append(sse)

        event_types = [e.split("\n")[0] for e in events]
        assert any("status" in et for et in event_types)
        assert any("result" in e for e in events)

    @pytest.mark.asyncio
    @patch("pipeline.read_sensor_streaming")
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_streaming_empty_data(self, mock_config, mock_mock_sensor, mock_read_stream):
        mock_config.SENSOR_MODE = "mock"
        mock_mock_sensor.start.return_value = "__mock__"

        async def empty_stream(port=None):
            return
            yield  # noqa

        mock_read_stream.side_effect = empty_stream

        events = []
        async for sse in run_pipeline_streaming():
            events.append(sse)

        last_event = events[-1]
        data_json = last_event.split("data: ", 1)[1].strip()
        data = json.loads(data_json)
        assert "No sensor data" in data["error"]

    @pytest.mark.asyncio
    @patch("pipeline.read_sensor_streaming")
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_streaming_sensor_exception(self, mock_config, mock_mock_sensor, mock_read_stream):
        mock_config.SENSOR_MODE = "mock"
        mock_mock_sensor.start.return_value = "__mock__"

        async def error_stream(port=None):
            raise RuntimeError("sensor exploded")
            yield  # noqa

        mock_read_stream.side_effect = error_stream

        events = []
        async for sse in run_pipeline_streaming():
            events.append(sse)

        last_event = events[-1]
        data_json = last_event.split("data: ", 1)[1].strip()
        data = json.loads(data_json)
        assert "Sensor read failed" in data["error"]

    @pytest.mark.asyncio
    @patch("pipeline.save_recording", return_value="/data/training/espresso/x.csv")
    @patch("pipeline.read_sensor_streaming")
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_streaming_data_collect_mode(
        self, mock_config, mock_mock_sensor, mock_read_stream, mock_save, sample_sensor_data
    ):
        mock_config.SENSOR_MODE = "mock"
        mock_config.DATA_COLLECT_ENABLED = True
        mock_config.DATA_COLLECT_LABEL = "espresso"
        mock_mock_sensor.start.return_value = "__mock__"

        async def fake_stream(port=None):
            yield sample_sensor_data

        mock_read_stream.side_effect = fake_stream

        events = []
        async for sse in run_pipeline_streaming():
            events.append(sse)

        # Should have data_collected event and result event
        combined = "".join(events)
        assert "data_collected" in combined

    @pytest.mark.asyncio
    @patch("pipeline.registry")
    @patch("pipeline.PipelineEngine")
    @patch("pipeline.read_sensor_streaming")
    @patch("pipeline.mock_sensor")
    @patch("pipeline.config")
    async def test_streaming_force_mock_uses_sample_only(
        self, mock_config, mock_mock_sensor, mock_read_stream,
        MockEngine, mock_registry, sample_sensor_data
    ):
        """force_mock=True must start mock sensor with sample_only=True."""
        mock_config.SENSOR_MODE = "serial"  # confirm force_mock overrides config
        mock_config.DATA_COLLECT_ENABLED = False
        mock_config.DATA_COLLECT_LABEL = ""
        mock_mock_sensor.start.return_value = "__mock__"

        async def fake_stream(port=None):
            yield sample_sensor_data

        mock_read_stream.side_effect = fake_stream

        engine_instance = MockEngine.return_value

        async def fake_execute_streaming(data, now):
            yield {"event": "pipeline_complete", "data": {
                "steps_completed": ["classifier"], "steps_skipped": [],
                "label": "espresso", "confidence": 0.95,
                "text": None, "audio_url": None, "error": None,
            }}

        engine_instance.execute_streaming = fake_execute_streaming

        events = []
        async for sse in run_pipeline_streaming(force_mock=True, skip_save=True):
            events.append(sse)

        mock_mock_sensor.start.assert_called_once_with(sample_only=True)
        assert any("result" in e for e in events)
