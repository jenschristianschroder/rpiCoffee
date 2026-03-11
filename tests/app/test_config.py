"""Tests for app/config.py — ConfigManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestConfigManager:
    """Test the layered configuration manager."""

    def test_defaults_loaded(self, mock_config):
        """Config should expose hardcoded defaults."""
        assert mock_config.get("SENSOR_MODE") == "mock"
        assert mock_config.get("LLM_TEMPERATURE") == 0.7
        assert mock_config.get("SENSOR_DURATION_S") == 30

    def test_get_missing_key_returns_default(self, mock_config):
        assert mock_config.get("NONEXISTENT_KEY") is None
        assert mock_config.get("NONEXISTENT_KEY", "fallback") == "fallback"

    def test_update_persists(self, mock_config, tmp_path):
        mock_config.update("LLM_TEMPERATURE", 0.5)
        assert mock_config.get("LLM_TEMPERATURE") == 0.5

        # Verify it was persisted to settings.json
        settings = json.loads((tmp_path / "settings.json").read_text())
        assert settings["LLM_TEMPERATURE"] == 0.5

    def test_update_many(self, mock_config):
        mock_config.update_many({"LLM_TEMPERATURE": 0.3, "SENSOR_DURATION_S": 10})
        assert mock_config.get("LLM_TEMPERATURE") == 0.3
        assert mock_config.get("SENSOR_DURATION_S") == 10

    def test_bool_casting(self, mock_config):
        mock_config.update("LLM_ENABLED", "false")
        assert mock_config.get("LLM_ENABLED") is False
        mock_config.update("LLM_ENABLED", "true")
        assert mock_config.get("LLM_ENABLED") is True
        mock_config.update("LLM_ENABLED", True)
        assert mock_config.get("LLM_ENABLED") is True

    def test_int_casting(self, mock_config):
        mock_config.update("SENSOR_DURATION_S", "45")
        assert mock_config.get("SENSOR_DURATION_S") == 45

    def test_float_casting(self, mock_config):
        mock_config.update("LLM_TEMPERATURE", "0.9")
        assert mock_config.get("LLM_TEMPERATURE") == 0.9

    def test_getattr_access(self, mock_config):
        assert mock_config.SENSOR_MODE == "mock"
        assert isinstance(mock_config.SENSOR_DURATION_S, int)

    def test_getattr_missing_raises(self, mock_config):
        with pytest.raises(AttributeError):
            _ = mock_config.TOTALLY_FAKE_KEY

    def test_to_dict(self, mock_config):
        d = mock_config.to_dict()
        assert isinstance(d, dict)
        assert "SENSOR_MODE" in d
        assert "LLM_TEMPERATURE" in d

    def test_verify_password(self, mock_config):
        assert mock_config.verify_password("testpin") is True
        assert mock_config.verify_password("wrongpin") is False

    def test_set_password(self, mock_config):
        mock_config.set_password("newpin")
        assert mock_config.verify_password("newpin") is True
        assert mock_config.verify_password("testpin") is False

    def test_env_override(self, tmp_path, monkeypatch):
        """Environment variables should override defaults."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
        monkeypatch.setenv("SENSOR_DURATION_S", "99")
        monkeypatch.setenv("ADMIN_PASSWORD", "1234")

        import config as config_mod
        config_mod.SETTINGS_PATH = settings_path
        cfg = config_mod.ConfigManager()
        assert cfg.get("SENSOR_DURATION_S") == 99

    def test_settings_json_override(self, tmp_path, monkeypatch):
        """Values persisted in settings.json should override defaults."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"LLM_TEMPERATURE": 0.2}), encoding="utf-8")
        monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
        monkeypatch.setenv("ADMIN_PASSWORD", "1234")

        import config as config_mod
        config_mod.SETTINGS_PATH = settings_path
        cfg = config_mod.ConfigManager()
        assert cfg.get("LLM_TEMPERATURE") == 0.2
