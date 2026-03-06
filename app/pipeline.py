"""
Pipeline orchestrator.

Executes the coffee pipeline in two phases:
  1. **Sensor** (fixed first stage) — collect IMU data via mock/picoquake/serial
  2. **Dynamic pipeline** — execute the configured service chain via
     ``PipelineEngine``, which reads steps from the service registry.

Special modes handled before the dynamic pipeline:
  - **Data collection** — if ``DATA_COLLECT_ENABLED``, save sensor data and skip
    all downstream stages.

Public functions ``run_pipeline()`` and ``run_pipeline_streaming()`` are
backward-compatible wrappers used by ``main.py`` and the auto-trigger loop.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from config import config
from pipeline_engine import PipelineEngine
from registry import registry
from sensor.mock import mock_sensor
from sensor.reader import read_sensor, read_sensor_streaming
from services.training_data import save_recording

logger = logging.getLogger("rpicoffee.pipeline")

AUDIO_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "audio"


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event message."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def run_pipeline(
    sensor_data: list[dict[str, float]] | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """
    Run the full brew pipeline and return a result dict.

    Parameters
    ----------
    sensor_data : list of dicts, optional
        Pre-collected sensor data (e.g. from auto-trigger streaming).
        When provided the sensor-read step is skipped.

    Returns
    -------
    dict with keys:
        steps_completed : list[str]
        steps_skipped   : list[str]
        sensor_samples  : int
        label           : str | None
        confidence      : float | None
        text            : str | None
        audio_url       : str | None
        error           : str | None
    """
    now = datetime.now().astimezone()

    # ── Step 0: Collect sensor data ──────────────────────────────
    try:
        if sensor_data is None:
            port = None
            if config.SENSOR_MODE == "mock":
                port = mock_sensor.start()
                logger.info("Using mock sensor on %s", port)

            sensor_data = await read_sensor(port=port)

        if not sensor_data:
            return _empty_result(error="No sensor data collected")

        raw_sensor_data = sensor_data
    except Exception as exc:
        logger.exception("Sensor read failed")
        return _empty_result(error=f"Sensor read failed: {exc}")

    # ── Data Collection Mode ──────────────────────────────────────
    if config.DATA_COLLECT_ENABLED and config.DATA_COLLECT_LABEL:
        return await _data_collect(raw_sensor_data)

    # ── Dynamic pipeline stages ──────────────────────────────────
    engine = PipelineEngine(registry)
    ctx = await engine.execute(raw_sensor_data, now)

    # Build backward-compatible result dict
    result = engine._build_summary(ctx)
    result["sensor_samples"] = len(raw_sensor_data)
    result["steps_completed"].insert(0, "sensor")

    # Downsample for chart
    step = max(1, len(raw_sensor_data) // 300)
    result["sensor_data"] = raw_sensor_data[::step]

    logger.info("Pipeline complete: %s → %s", result["label"], result["audio_url"])
    return result


async def run_pipeline_streaming(
    force_mock: bool = False,
    skip_save: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Run the brew pipeline and yield SSE events.

    Parameters
    ----------
    force_mock : bool
        When True, always use mock sensor (replay CSV) regardless of
        config.SENSOR_MODE.  Used by the Test button.
    skip_save : bool
        When True, skip the remote-save step.

    Events emitted:
        sensor          – batch of sensor data points (many times)
        status          – { message: str } progress updates
        step_start      – { service: name }
        step_complete   – { service: name, result: ... }
        step_error      – { service: name, error: ... }
        step_skip       – { service: name, reason: ... }
        classify        – legacy: { label, confidence }
        text            – legacy: { text }
        audio           – legacy: { audio_url }
        pipeline_complete – full result summary
        result          – final pipeline result dict (once, at end)
    """
    now = datetime.now().astimezone()

    # ── Step 0: Stream sensor data ───────────────────────────────
    yield _sse("status", {"message": "Replaying test data…" if force_mock else "Collecting sensor data…"})

    all_sensor_data: list[dict[str, float]] = []
    try:
        port = None
        if force_mock or config.SENSOR_MODE == "mock":
            port = mock_sensor.start()
            logger.info("Using mock sensor on %s", port)

        async for batch in read_sensor_streaming(port=port):
            all_sensor_data.extend(batch)
            step = max(1, len(batch) // 20)
            yield _sse("sensor", batch[::step])

        if not all_sensor_data:
            yield _sse("result", _empty_result(error="No sensor data collected"))
            return

    except Exception as exc:
        logger.exception("Sensor read failed")
        yield _sse("result", _empty_result(error=f"Sensor read failed: {exc}"))
        return

    # ── Data Collection Mode ──────────────────────────────────────
    if config.DATA_COLLECT_ENABLED and config.DATA_COLLECT_LABEL:
        result = await _data_collect(all_sensor_data)
        yield _sse("data_collected", {
            "label": config.DATA_COLLECT_LABEL,
            "samples": len(all_sensor_data),
            "file": result.get("data_file"),
        })
        yield _sse("result", result)
        return

    # ── Dynamic pipeline stages ──────────────────────────────────
    engine = PipelineEngine(registry)

    # If skip_save is requested, temporarily disable the remote-save step
    if skip_save:
        registry.set_enabled("remote-save", False)

    final_summary: dict[str, Any] | None = None
    try:
        async for event in engine.execute_streaming(all_sensor_data, now):
            yield _sse(event["event"], event["data"])
            if event["event"] == "pipeline_complete":
                final_summary = event["data"]
    finally:
        if skip_save:
            registry.set_enabled("remote-save", True)

    # Emit legacy result event for full backward compatibility
    if final_summary is not None:
        final_summary["sensor_samples"] = len(all_sensor_data)
        final_summary.setdefault("steps_completed", []).insert(0, "sensor")
        yield _sse("result", final_summary)

    logger.info("Pipeline streaming complete")


def _empty_result(error: str | None = None) -> dict[str, Any]:
    """Return an empty result dict with optional error."""
    return {
        "steps_completed": [],
        "steps_skipped": [],
        "sensor_samples": 0,
        "sensor_data": None,
        "label": None,
        "confidence": None,
        "text": None,
        "audio_url": None,
        "error": error,
    }


async def _data_collect(raw_sensor_data: list[dict[str, float]]) -> dict[str, Any]:
    """Handle data collection mode: save sensor data and skip pipeline."""
    result = _empty_result()
    try:
        filepath = save_recording(config.DATA_COLLECT_LABEL, raw_sensor_data)
        result["label"] = config.DATA_COLLECT_LABEL
        result["sensor_samples"] = len(raw_sensor_data)
        result["steps_completed"] = ["sensor", "data_collected"]
        result["data_collected"] = True
        result["data_file"] = filepath
        result["steps_skipped"] = ["classifier", "llm", "tts", "remote-save"]
        logger.info("Data collection: saved %d samples as '%s' → %s",
                    len(raw_sensor_data), config.DATA_COLLECT_LABEL, filepath)
    except Exception as exc:
        logger.exception("Data collection save failed")
        result["error"] = f"Data collection save failed: {exc}"
    return result
