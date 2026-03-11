"""App-specific test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure app/ is on sys.path
_APP_DIR = Path(__file__).resolve().parent.parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


@pytest.fixture()
def mock_config(tmp_path, monkeypatch):
    """Patch the config singleton with test-friendly defaults.

    Uses a temporary settings.json so tests don't mutate real data.
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
    monkeypatch.setenv("SENSOR_MODE", "mock")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpin")

    # Re-import to pick up patched env
    import config as config_mod
    config_mod.SETTINGS_PATH = settings_path
    cfg = config_mod.ConfigManager()
    monkeypatch.setattr(config_mod, "config", cfg)
    return cfg


@pytest.fixture()
def app_client(mock_config):
    """Create an httpx AsyncClient for the FastAPI app with mocked startup."""
    import httpx

    # Patch heavy startup operations
    with patch("main.registry") as mock_reg, \
         patch("main._start_sensor"), \
         patch("main._ensure_auto_trigger"), \
         patch("main.run_pipeline", new_callable=AsyncMock) as mock_pipeline:

        mock_reg.load = MagicMock()
        mock_reg.list_all = MagicMock(return_value=[])
        mock_reg.get_pipeline = MagicMock(return_value=[])
        mock_reg.refresh_all_manifests = AsyncMock()

        from main import app
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test")
        yield client
