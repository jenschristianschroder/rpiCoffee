"""HTTP client for the TTS text-to-speech service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import config

logger = logging.getLogger("rpicoffee.tts_client")
_TIMEOUT = 30.0


class TTSClient:
    """Calls TTS /synthesize and /health endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.TTS_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("TTS health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def synthesize(text: str, speed: float = 1.0) -> bytes | None:
        """
        Convert text to WAV audio via localtts.

        Returns
        -------
        bytes (WAV audio data) or None on failure.
        """
        if not config.TTS_ENABLED:
            logger.info("TTS is disabled – skipping speech synthesis")
            return None

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.TTS_ENDPOINT}/synthesize",
                    json={"text": text, "speed": speed},
                )
                r.raise_for_status()
                logger.info("TTS synthesized %d bytes of audio", len(r.content))
                return r.content
        except Exception as exc:
            logger.error("TTS synthesize failed: %s", exc)
            return None

    @staticmethod
    async def get_settings() -> list[dict[str, Any]] | None:
        """Fetch settings metadata from the TTS service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.TTS_ENDPOINT}/settings")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("TTS get_settings failed: %s", exc)
            return None

    @staticmethod
    async def update_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
        """Update settings on the TTS service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.patch(
                    f"{config.TTS_ENDPOINT}/settings",
                    json={"settings": settings},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("TTS update_settings failed: %s", exc)
            return None
