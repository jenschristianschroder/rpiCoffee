"""Tests for app/pipeline_executor.py — call_service()."""

from __future__ import annotations

import httpx
import pytest
import respx
from pipeline_executor import ServiceCallError, call_service


class TestCallService:
    @respx.mock
    @pytest.mark.asyncio
    async def test_json_post_success(self):
        respx.post("http://svc:8001/classify").mock(
            return_value=httpx.Response(200, json={"label": "espresso", "confidence": 0.9})
        )
        result = await call_service("http://svc:8001", "POST", "/classify", {"data": []})
        assert result == {"label": "espresso", "confidence": 0.9}

    @respx.mock
    @pytest.mark.asyncio
    async def test_binary_response(self):
        respx.post("http://svc:5050/synthesize").mock(
            return_value=httpx.Response(200, content=b"\x00\x01\x02", headers={"content-type": "audio/wav"})
        )
        result = await call_service("http://svc:5050", "POST", "/synthesize", {"text": "hello"})
        assert isinstance(result, bytes)
        assert result == b"\x00\x01\x02"

    @respx.mock
    @pytest.mark.asyncio
    async def test_get_request(self):
        respx.get("http://svc:8001/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        result = await call_service("http://svc:8001", "GET", "/health", {})
        assert result == {"status": "ok"}

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_500_raises(self):
        respx.post("http://svc:8001/classify").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )
        with pytest.raises(ServiceCallError) as exc_info:
            await call_service("http://svc:8001", "POST", "/classify", {})
        assert "500" in exc_info.value.message

    @respx.mock
    @pytest.mark.asyncio
    async def test_connection_error_raises(self):
        respx.post("http://svc:8001/classify").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ServiceCallError):
            await call_service("http://svc:8001", "POST", "/classify", {})

    @respx.mock
    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        respx.post("http://svc:8001/classify").mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(ServiceCallError):
            await call_service("http://svc:8001", "POST", "/classify", {}, timeout=1.0)

    def test_service_call_error_attributes(self):
        err = ServiceCallError("http://svc:8001/classify", "connection refused")
        assert err.service == "http://svc:8001/classify"
        assert err.message == "connection refused"
        assert "connection refused" in str(err)
