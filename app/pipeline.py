"""
Pipeline orchestrator.

Executes the full coffee pipeline:
  1. Sensor      → collect 30s of IMU data
  2. classifier  → classify coffee type
  3. llm         → generate a witty statement
  4. tts         → synthesize speech (WAV)
  5. remote-save → save results + CSV to remote service (non-fatal)

Each step passes its output to the next.  If a service is unavailable the
pipeline stops at that step and reports what succeeded and what was skipped.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

from config import config
from sensor.mock import mock_sensor
from sensor.reader import read_sensor, read_sensor_streaming
from services.classifier_client import ClassifierClient
from services.llm_client import LLMClient
from services.tts_client import TTSClient
from services.remote_save_client import RemoteSaveClient

logger = logging.getLogger("rpicoffee.pipeline")

AUDIO_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "audio"


async def run_pipeline(
    sensor_data: list[dict[str, float]] | None = None,
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
    result: dict[str, Any] = {
        "steps_completed": [],
        "steps_skipped": [],
        "sensor_samples": 0,
        "sensor_data": None,
        "label": None,
        "confidence": None,
        "text": None,
        "audio_url": None,
        "error": None,
    }

    now = datetime.now(timezone.utc)

    # ── Step 0: Collect sensor data ──────────────────────────────
    try:
        if sensor_data is None:
            port = None
            if config.SENSOR_MODE == "mock":
                port = mock_sensor.start()
                logger.info("Using mock sensor on %s", port)

            sensor_data = await read_sensor(port=port)

        result["sensor_samples"] = len(sensor_data)

        if not sensor_data:
            result["error"] = "No sensor data collected"
            return result

        # Keep full raw data for remote save, downsample for the chart
        raw_sensor_data = sensor_data
        step = max(1, len(sensor_data) // 300)
        result["sensor_data"] = sensor_data[::step]

        result["steps_completed"].append("sensor")
    except Exception as exc:
        logger.exception("Sensor read failed")
        result["error"] = f"Sensor read failed: {exc}"
        return result

    # ── Step 1: Classify via classifier ───────────────────────────
    classification = await ClassifierClient.classify(sensor_data)

    if classification is None:
        result["steps_skipped"].append("classifier")
        result["steps_skipped"].append("llm")
        result["steps_skipped"].append("tts")
        result["steps_skipped"].append("remote-save")
        result["error"] = "Classification unavailable – pipeline stopped"
        return result

    result["label"] = classification["label"]
    result["confidence"] = classification["confidence"]
    result["steps_completed"].append("classifier")

    # ── Step 2: Generate text via llm ────────────────────────────
    llm_result = await LLMClient.generate(
        coffee_label=classification["label"],
        timestamp=now,
    )

    if llm_result is None:
        result["steps_skipped"].append("llm")
        result["steps_skipped"].append("tts")
        result["steps_skipped"].append("remote-save")
        result["error"] = "Text generation unavailable – pipeline stopped after classification"
        return result

    result["text"] = llm_result["response"]
    result["steps_completed"].append("llm")

    # ── Step 3: Synthesize speech via tts ─────────────────────────
    audio_bytes = await TTSClient.synthesize(llm_result["response"])

    if audio_bytes is None:
        result["steps_skipped"].append("tts")
        result["steps_skipped"].append("remote-save")
        result["error"] = "Speech synthesis unavailable – pipeline stopped after text generation"
        return result

    # Save WAV to disk
    audio_id = uuid.uuid4().hex[:12]
    audio_path = AUDIO_DIR / f"{audio_id}.wav"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_bytes)
    result["audio_url"] = f"/audio/{audio_id}.wav"
    result["steps_completed"].append("tts")

    # ── Step 4: Save results via remote save (non-fatal) ─────────
    save_ok = await RemoteSaveClient.save(result, raw_sensor_data)
    if save_ok:
        result["steps_completed"].append("remote-save")
    else:
        result["steps_skipped"].append("remote-save")
        logger.warning("Remote save skipped or failed – brew result still returned")

    logger.info("Pipeline complete: %s → %s", result["label"], result["audio_url"])
    return result


def _sse(event: str, data: Any) -> str:
    """Format a Server-Sent Event message."""
    payload = json.dumps(data) if not isinstance(data, str) else data
    return f"event: {event}\ndata: {payload}\n\n"


async def run_pipeline_streaming() -> AsyncGenerator[str, None]:
    """
    Run the brew pipeline and yield SSE events.

    Events emitted:
        sensor   – batch of sensor data points (many times)
        status   – { message: str } progress updates
        result   – final pipeline result dict (once, at end)
    """
    result: dict[str, Any] = {
        "steps_completed": [],
        "steps_skipped": [],
        "sensor_samples": 0,
        "label": None,
        "confidence": None,
        "text": None,
        "audio_url": None,
        "error": None,
    }

    now = datetime.now(timezone.utc)

    # ── Step 0: Stream sensor data ───────────────────────────────
    yield _sse("status", {"message": "Collecting sensor data…"})

    all_sensor_data: list[dict[str, float]] = []
    try:
        port = None
        if config.SENSOR_MODE == "mock":
            port = mock_sensor.start()
            logger.info("Using mock sensor on %s", port)

        async for batch in read_sensor_streaming(port=port):
            all_sensor_data.extend(batch)
            # Downsample batch for chart if large
            step = max(1, len(batch) // 20)
            yield _sse("sensor", batch[::step])

        result["sensor_samples"] = len(all_sensor_data)

        if not all_sensor_data:
            result["error"] = "No sensor data collected"
            yield _sse("result", result)
            return

        result["steps_completed"].append("sensor")
    except Exception as exc:
        logger.exception("Sensor read failed")
        result["error"] = f"Sensor read failed: {exc}"
        yield _sse("result", result)
        return

    # ── Step 1: Classify via classifier ───────────────────────────
    yield _sse("status", {"message": "Classifying coffee type…"})

    classification = await ClassifierClient.classify(all_sensor_data)

    if classification is None:
        result["steps_skipped"].extend(["classifier", "llm", "tts", "remote-save"])
        result["error"] = "Classification unavailable – pipeline stopped"
        yield _sse("result", result)
        return

    result["label"] = classification["label"]
    result["confidence"] = classification["confidence"]
    result["steps_completed"].append("classifier")
    yield _sse("classify", {"label": classification["label"], "confidence": classification["confidence"]})

    # ── Step 2: Generate text via llm ────────────────────────────
    yield _sse("status", {"message": f"Generating text for {classification['label']}…"})

    llm_result = await LLMClient.generate(
        coffee_label=classification["label"],
        timestamp=now,
    )

    if llm_result is None:
        result["steps_skipped"].extend(["llm", "tts", "remote-save"])
        result["error"] = "Text generation unavailable – pipeline stopped after classification"
        yield _sse("result", result)
        return

    result["text"] = llm_result["response"]
    result["steps_completed"].append("llm")
    yield _sse("text", {"text": llm_result["response"]})

    # ── Step 3: Synthesize speech via tts ─────────────────────────
    yield _sse("status", {"message": "Synthesizing speech…"})

    audio_bytes = await TTSClient.synthesize(llm_result["response"])

    if audio_bytes is None:
        result["steps_skipped"].extend(["tts", "remote-save"])
        result["error"] = "Speech synthesis unavailable – pipeline stopped after text generation"
        yield _sse("result", result)
        return

    audio_id = uuid.uuid4().hex[:12]
    audio_path = AUDIO_DIR / f"{audio_id}.wav"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(audio_bytes)
    result["audio_url"] = f"/audio/{audio_id}.wav"
    result["steps_completed"].append("tts")
    yield _sse("audio", {"audio_url": result["audio_url"]})

    # ── Step 4: Save results via remote save (non-fatal) ─────────
    yield _sse("status", {"message": "Saving results…"})
    save_ok = await RemoteSaveClient.save(result, all_sensor_data)
    if save_ok:
        result["steps_completed"].append("remote-save")
    else:
        result["steps_skipped"].append("remote-save")
        logger.warning("Remote save skipped or failed – brew result still returned")

    logger.info("Pipeline complete: %s → %s", result["label"], result["audio_url"])
    yield _sse("result", result)
