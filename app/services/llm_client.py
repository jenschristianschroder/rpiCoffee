"""HTTP client for the locallm text generation service."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from config import config

logger = logging.getLogger("rpicoffee.llm_client")
_TIMEOUT = 30.0


class LLMClient:
    """Calls locallm /generate and /health endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.LOCALLM_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("locallm health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def generate(
        coffee_label: str,
        timestamp: datetime | None = None,
        tts: bool | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Generate a natural-language sentence about the coffee type.

        Parameters default to the corresponding config values
        (LLM_TTS, LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_TOP_P).

        Returns
        -------
        dict with 'response', 'tokens', 'elapsed_s', 'tokens_per_s', or None on failure.
        """
        if not config.LOCALLM_ENABLED:
            logger.info("locallm is disabled – skipping text generation")
            return None

        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # Resolve from config if not explicitly provided
        if tts is None:
            tts = config.LLM_TTS
        if max_tokens is None:
            max_tokens = config.LLM_MAX_TOKENS
        if temperature is None:
            temperature = config.LLM_TEMPERATURE
        if top_p is None:
            top_p = config.LLM_TOP_P

        prompt = f"Write a statement about {coffee_label.title()} at {timestamp.isoformat()}"

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.LOCALLM_ENDPOINT}/generate",
                    json={
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "top_p": top_p,
                        "tts": tts,
                    },
                )
                r.raise_for_status()
                result = r.json()
                logger.info("LLM generated %d tokens in %.2fs", result.get("tokens", 0), result.get("elapsed_s", 0))
                return result
        except Exception as exc:
            logger.error("locallm generate failed: %s", exc)
            return None
