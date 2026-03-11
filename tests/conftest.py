"""Root-level shared fixtures for all rpiCoffee tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add app/ to sys.path so imports like ``from config import config`` work
_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


@pytest.fixture()
def sample_sensor_data() -> list[dict[str, float]]:
    """A small list of fake 6-axis IMU readings."""
    return [
        {"elapsed_s": i * 0.01, "acc_x": 0.1 * i, "acc_y": -0.05 * i,
         "acc_z": 9.81 + 0.01 * i, "gyro_x": 0.5, "gyro_y": -0.3, "gyro_z": 0.1}
        for i in range(100)
    ]


@pytest.fixture()
def tmp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with default pipeline.json and settings.json."""
    pipeline = {
        "services": {},
        "pipeline": [],
    }
    settings = {
        "SENSOR_MODE": "mock",
        "LLM_ENABLED": False,
        "TTS_ENABLED": False,
        "CLASSIFIER_ENABLED": False,
        "REMOTE_SAVE_ENABLED": False,
    }
    (tmp_path / "pipeline.json").write_text(json.dumps(pipeline), encoding="utf-8")
    (tmp_path / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    (tmp_path / "audio").mkdir()
    return tmp_path


@pytest.fixture()
def sample_manifest_dict() -> dict:
    """A valid service manifest as a raw dict."""
    return {
        "name": "test-service",
        "version": "1.0.0",
        "description": "A test service",
        "inputs": [
            {"name": "sensor_data", "type": "array", "required": True, "description": "Sensor readings"},
        ],
        "outputs": [
            {"name": "label", "type": "string", "description": "Classification label"},
            {"name": "confidence", "type": "float", "description": "Confidence score"},
        ],
        "endpoints": {
            "execute": {"method": "POST", "path": "/classify"},
            "health": {"method": "GET", "path": "/health"},
        },
        "failure_modes": ["skip", "halt"],
    }
