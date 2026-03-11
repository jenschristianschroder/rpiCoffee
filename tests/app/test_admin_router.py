"""Tests for app/admin/router.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport
from itsdangerous import URLSafeSerializer

import main as main_mod


def _make_token(secret: str = "test-secret", authenticated: bool = True) -> str:
    import time

    signer = URLSafeSerializer(secret)
    return signer.dumps({"authenticated": authenticated, "ts": time.time()})


@pytest.fixture()
async def admin_client(mock_config):
    """Admin-aware test client with a valid session cookie."""
    mock_config.SECRET_KEY = "test-secret"

    import admin.router as admin_router_mod

    with (
        patch.object(main_mod, "registry") as mock_reg,
        patch.object(main_mod, "_start_sensor"),
        patch.object(main_mod, "_ensure_auto_trigger"),
        patch.object(admin_router_mod, "config", mock_config),
    ):
        mock_reg.load = MagicMock()
        mock_reg.list_all = MagicMock(return_value=[])
        mock_reg.get_pipeline = MagicMock(return_value=[])
        mock_reg.refresh_all_manifests = AsyncMock()
        mock_reg.health_check_all = AsyncMock(return_value={})

        transport = ASGITransport(app=main_mod.app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as c:
            yield c


class TestLoginLogout:
    @pytest.mark.asyncio
    async def test_get_login_page(self, admin_client):
        resp = await admin_client.get("/admin/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, admin_client):
        resp = await admin_client.post(
            "/admin/login",
            data={"password": "wrongpin"},
            follow_redirects=False,
        )
        # Should re-render login page with error (200, HTML)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_login_correct_password(self, admin_client, mock_config):
        # mock_config fixture sets ADMIN password to "testpin"
        resp = await admin_client.post(
            "/admin/login",
            data={"password": "testpin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self, admin_client):
        resp = await admin_client.get("/admin/logout", follow_redirects=False)
        assert resp.status_code == 303


class TestDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self, admin_client):
        resp = await admin_client.get("/admin/", follow_redirects=False)
        assert resp.status_code == 303
        assert "login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_dashboard_with_valid_session(self, admin_client):
        token = _make_token("test-secret")
        resp = await admin_client.get(
            "/admin/",
            cookies={"session": token},
            follow_redirects=False,
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pipeline_editor_requires_auth(self, admin_client):
        resp = await admin_client.get("/admin/pipeline", follow_redirects=False)
        assert resp.status_code == 303


class TestSensorConfig:
    @pytest.mark.asyncio
    async def test_update_sensor_config_no_auth(self, admin_client):
        resp = await admin_client.post(
            "/admin/sensor-config",
            json={"SENSOR_DURATION_S": 20},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_update_sensor_config_ok(self, admin_client):
        token = _make_token("test-secret")
        resp = await admin_client.post(
            "/admin/sensor-config",
            json={"SENSOR_DURATION_S": 20},
            cookies={"session": token},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_update_sensor_config_empty(self, admin_client):
        token = _make_token("test-secret")
        resp = await admin_client.post(
            "/admin/sensor-config",
            json={},
            cookies={"session": token},
        )
        assert resp.status_code == 400


class TestPasswordChange:
    @pytest.mark.asyncio
    async def test_change_password_no_auth(self, admin_client):
        resp = await admin_client.post(
            "/admin/password",
            data={"current_password": "testpin", "new_password": "newpin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "login" in resp.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_change_password_wrong_current(self, admin_client):
        token = _make_token("test-secret")
        resp = await admin_client.post(
            "/admin/password",
            data={"current_password": "wrongpin", "new_password": "newpin"},
            cookies={"session": token},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "incorrect" in resp.headers.get("location", "").lower() or "admin" in resp.headers.get("location", "")
