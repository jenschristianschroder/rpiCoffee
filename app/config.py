"""
Layered configuration manager.

Load order (highest priority last):
  1. Hardcoded defaults
  2. .env file values
  3. /data/settings.json persisted overrides
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from dotenv import load_dotenv

# Resolve a sensible local data dir for non-Docker development
_APP_DIR = Path(__file__).resolve().parent
_LOCAL_DATA_DIR = _APP_DIR.parent / "data"

_DEFAULTS: dict[str, Any] = {
    "ADMIN_PASSWORD": "1234",
    "SECRET_KEY": "change-me-to-a-random-string",
    # Services
    "LOCALLM_ENABLED": True,
    "LOCALLM_ENDPOINT": "http://locallm:8000",
    "LOCALTTS_ENABLED": True,
    "LOCALTTS_ENDPOINT": "http://localtts:5000",
    "LOCALML_ENABLED": True,
    "LOCALML_ENDPOINT": "http://localml:8001",
    # Sensor
    "SENSOR_MOCK_ENABLED": True,
    "SENSOR_SERIAL_PORT": "/dev/ttyUSB0",
    "SENSOR_SAMPLE_RATE_HZ": 100,
    "SENSOR_DURATION_S": 30,
    # LLM generation parameters
    "LLM_MAX_TOKENS": 256,
    "LLM_TEMPERATURE": 0.7,
    "LLM_TOP_P": 0.9,
    "LLM_TTS": True,
    # Remote save
    "REMOTE_SAVE_ENABLED": True,
    "REMOTE_SAVE_ENDPOINT": "http://remotesave:7000",
}

_BOOL_KEYS = {"LOCALLM_ENABLED", "LOCALTTS_ENABLED", "LOCALML_ENABLED", "SENSOR_MOCK_ENABLED", "LLM_TTS", "REMOTE_SAVE_ENABLED"}
_INT_KEYS = {"SENSOR_SAMPLE_RATE_HZ", "SENSOR_DURATION_S", "LLM_MAX_TOKENS"}
_FLOAT_KEYS = {"LLM_TEMPERATURE", "LLM_TOP_P"}

SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", str(_LOCAL_DATA_DIR))) / "settings.json"


def _cast(key: str, value: Any) -> Any:
    """Cast a raw value to the expected type for *key*."""
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "yes")
    if key in _INT_KEYS:
        try:
            return int(value)
        except (ValueError, TypeError):
            return _DEFAULTS.get(key, value)
    if key in _FLOAT_KEYS:
        try:
            return float(value)
        except (ValueError, TypeError):
            return _DEFAULTS.get(key, value)
    return value


class ConfigManager:
    """Thread-safe, layered config store with JSON persistence."""

    def __init__(self, env_file: str | Path | None = None) -> None:
        self._lock = Lock()
        self._data: dict[str, Any] = {}
        self._env_file = env_file
        self.load()

    # ── Loading ────────────────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            # Layer 1 – defaults
            self._data = dict(_DEFAULTS)

            # Layer 2 – .env file (override=False so process env vars take priority)
            if self._env_file:
                load_dotenv(self._env_file, override=False)
            else:
                load_dotenv(override=False)

            for key in _DEFAULTS:
                env_val = os.environ.get(key)
                if env_val is not None:
                    self._data[key] = _cast(key, env_val)

            # Layer 3 – persisted settings.json
            if SETTINGS_PATH.exists():
                try:
                    with open(SETTINGS_PATH, "r") as f:
                        persisted = json.load(f)
                    for key, value in persisted.items():
                        if key in _DEFAULTS:
                            self._data[key] = _cast(key, value)
                except (json.JSONDecodeError, OSError):
                    pass  # Corrupted file – fall back to env/defaults

    # ── Persistence ────────────────────────────────────────────────

    def save(self) -> None:
        with self._lock:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_PATH, "w") as f:
                json.dump(self._data, f, indent=2)

    # ── Accessors ──────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, key: str, value: Any) -> None:
        with self._lock:
            if key in _DEFAULTS:
                self._data[key] = _cast(key, value)
        self.save()

    def update_many(self, updates: dict[str, Any]) -> None:
        with self._lock:
            for key, value in updates.items():
                if key in _DEFAULTS:
                    self._data[key] = _cast(key, value)
        self.save()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in ("load", "save", "get", "update", "update_many", "to_dict"):
            raise AttributeError(name)
        with self._lock:
            if name in self._data:
                return self._data[name]
        raise AttributeError(f"No config key {name!r}")


# Singleton instance – imported by the rest of the app
config = ConfigManager()
