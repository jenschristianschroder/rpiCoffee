"""HTTP client for the localtts text-to-speech service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import config

logger = logging.getLogger("rpicoffee.tts_client")
_TIMEOUT = 30.0


class TTSClient:
    """Calls localtts /synthesize and /health endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.LOCALTTS_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("localtts health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def synthesize(text: str, speed: float = 1.0) -> bytes | None:
        """
        Convert text to WAV audio via localtts.

        Returns
        -------
        bytes (WAV audio data) or None on failure.
        """
        if not config.LOCALTTS_ENABLED:
            logger.info("localtts is disabled – skipping speech synthesis")
            return None

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.LOCALTTS_ENDPOINT}/synthesize",
                    json={"text": text, "speed": speed},
                )
                r.raise_for_status()
                logger.info("TTS synthesized %d bytes of audio", len(r.content))
                return r.content
        except Exception as exc:
            logger.error("localtts synthesize failed: %s", exc)
            return None
