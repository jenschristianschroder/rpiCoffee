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

import bcrypt
from dotenv import load_dotenv

# Resolve a sensible local data dir for non-Docker development
_APP_DIR = Path(__file__).resolve().parent
_LOCAL_DATA_DIR = _APP_DIR.parent / "data"

# Secrets – loaded from environment / .env only, never persisted to settings.json
_ENV_ONLY: dict[str, str] = {
    "SECRET_KEY": "change-me-to-a-random-string",
}

# Keys that are persisted to settings.json but are NOT part of _DEFAULTS
_PERSISTED_SECRETS = {"ADMIN_PASSWORD_HASH"}

_DEFAULTS: dict[str, Any] = {
    # Services
    "LLM_ENABLED": True,
    "LLM_ENDPOINT": "http://llm:8000",
    "TTS_ENABLED": True,
    "TTS_ENDPOINT": "http://tts:5000",
    "CLASSIFIER_ENABLED": True,
    "CLASSIFIER_ENDPOINT": "http://classifier:8001",
    # Sensor
    "SENSOR_MODE": "mock",  # "mock", "picoquake", or "serial"
    "SENSOR_DEVICE_ID": "cf79",
    "SENSOR_SERIAL_PORT": "/dev/ttyUSB0",
    "SENSOR_SAMPLE_RATE_HZ": 100,
    "SENSOR_DURATION_S": 30,
    "SENSOR_VIBRATION_THRESHOLD": 2.0,
    "SENSOR_RMS_WINDOW_S": 1.0,
    "SENSOR_AUTO_TRIGGER": False,
    "SENSOR_ACC_RANGE_G": 4,
    "SENSOR_GYRO_RANGE_DPS": 500,
    "SENSOR_FILTER_HZ": 42,
    "SENSOR_CHART_WINDOW_S": 30,
    # LLM generation parameters
    "LLM_MAX_TOKENS": 256,
    "LLM_TEMPERATURE": 0.7,
    "LLM_TOP_P": 0.9,
    "LLM_TTS": True,
    # Remote save
    "REMOTE_SAVE_ENABLED": True,
    "REMOTE_SAVE_ENDPOINT": "http://remote-save:7000",
}

_BOOL_KEYS = {"LLM_ENABLED", "TTS_ENABLED", "CLASSIFIER_ENABLED", "SENSOR_AUTO_TRIGGER", "LLM_TTS", "REMOTE_SAVE_ENABLED"}
_INT_KEYS = {"SENSOR_SAMPLE_RATE_HZ", "SENSOR_DURATION_S", "LLM_MAX_TOKENS",
             "SENSOR_ACC_RANGE_G", "SENSOR_GYRO_RANGE_DPS", "SENSOR_FILTER_HZ",
             "SENSOR_CHART_WINDOW_S"}
_FLOAT_KEYS = {"LLM_TEMPERATURE", "LLM_TOP_P", "SENSOR_VIBRATION_THRESHOLD", "SENSOR_RMS_WINDOW_S"}

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
            # Layer 0 – env-only secrets (from .env / environment, never persisted)
            if self._env_file:
                load_dotenv(self._env_file, override=False)
            else:
                load_dotenv(override=False)

            self._data = {}
            for key, default in _ENV_ONLY.items():
                self._data[key] = os.environ.get(key, default)

            # Layer 1 – defaults
            self._data.update(_DEFAULTS)

            # Layer 2 – .env overrides for app config keys
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
                        if key in _DEFAULTS or key in _PERSISTED_SECRETS:
                            self._data[key] = _cast(key, value)
                except (json.JSONDecodeError, OSError):
                    pass  # Corrupted file – fall back to env/defaults

            # Bootstrap admin password: if no hash exists yet, hash the
            # ADMIN_PASSWORD env var (or default "1234") and persist it.
            if "ADMIN_PASSWORD_HASH" not in self._data:
                plain = os.environ.get("ADMIN_PASSWORD", "1234")
                self._data["ADMIN_PASSWORD_HASH"] = bcrypt.hashpw(
                    plain.encode(), bcrypt.gensalt()
                ).decode()
                self._save_unlocked()

    # ── Persistence ────────────────────────────────────────────────

    def save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        """Internal save – caller must already hold self._lock."""
        persistable = {
            k: v for k, v in self._data.items()
            if k in _DEFAULTS or k in _PERSISTED_SECRETS
        }
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(persistable, f, indent=2)

    # ── Password management ───────────────────────────────────

    def verify_password(self, plain_password: str) -> bool:
        """Check a plaintext password against the stored bcrypt hash."""
        stored = self._data.get("ADMIN_PASSWORD_HASH", "")
        if not stored:
            return False
        return bcrypt.checkpw(plain_password.encode(), stored.encode())

    def set_password(self, new_password: str) -> None:
        """Hash and persist a new admin password."""
        hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        with self._lock:
            self._data["ADMIN_PASSWORD_HASH"] = hashed
        self.save()

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
        if name.startswith("_") or name in (
            "load", "save", "get", "update", "update_many",
            "to_dict", "verify_password", "set_password",
        ):
            raise AttributeError(name)
        with self._lock:
            if name in self._data:
                return self._data[name]
        raise AttributeError(f"No config key {name!r}")


# Singleton instance – imported by the rest of the app
config = ConfigManager()
