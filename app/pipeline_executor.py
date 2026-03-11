"""Thin async HTTP caller for pipeline service execution.

Handles both JSON and binary (e.g. TTS audio) responses.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0


class ServiceCallError(Exception):
    """Raised when a service call fails."""

    def __init__(self, service: str, message: str) -> None:
        self.service = service
        self.message = message
        super().__init__(f"{service}: {message}")


async def call_service(
    endpoint: str,
    method: str,
    path: str,
    payload: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any] | bytes:
    """Call a service endpoint and return the response.

    Parameters
    ----------
    endpoint : str
        Base URL of the service (e.g. ``http://classifier:8001``).
    method : str
        HTTP method (``GET``, ``POST``, etc.).
    path : str
        URL path relative to the service root (e.g. ``/classify``).
    payload : dict
        JSON body to send (ignored for GET requests).
    timeout : float
        Request timeout in seconds.

    Returns
    -------
    dict or bytes
        JSON-decoded dict for ``application/json`` responses,
        raw bytes for binary responses (e.g. audio).

    Raises
    ------
    ServiceCallError
        On HTTP errors or connection failures.
    """
    url = f"{endpoint.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method.upper() == "GET":
                resp = await client.get(url)
            else:
                resp = await client.request(method.upper(), url, json=payload)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                return resp.json()
            # Binary response (e.g. WAV audio from TTS)
            return resp.content
    except httpx.HTTPStatusError as exc:
        raise ServiceCallError(
            url,
            f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        ) from exc
    except Exception as exc:
        raise ServiceCallError(url, str(exc)) from exc
