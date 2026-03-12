"""HTTP client for the LLM text generation service (llama-cpp backend).

Talks to the ``llm`` microservice which runs llama-cpp-python GGUF
inference.  For the Ollama backend, see ``ollama_client.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from config import config

logger = logging.getLogger("rpicoffee.llm_client")


class LLMClient:
    """Calls LLM /generate, /health, and /settings endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.LLM_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("LLM health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def generate(
        coffee_label: str,
        timestamp: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Generate a natural-language sentence about the coffee type.

        The llm service owns all generation defaults via its /settings
        endpoint; the app only sends the prompt.

        Returns
        -------
        dict with 'response', 'tokens', 'elapsed_s', 'tokens_per_s', or None on failure.
        """
        if not config.LLM_ENABLED:
            logger.info("LLM is disabled – skipping text generation")
            return None

        if timestamp is None:
            timestamp = datetime.now().astimezone()

        prompt = f"Write a statement about {coffee_label.title()} at {timestamp.isoformat()}"

        try:
            async with httpx.AsyncClient(timeout=float(config.get("LLM_TIMEOUT"))) as client:
                r = await client.post(
                    f"{config.LLM_ENDPOINT}/generate",
                    json={"prompt": prompt},
                )
                r.raise_for_status()
                result = r.json()
                logger.info("LLM generated %d tokens in %.2fs",
                            result.get("tokens", 0), result.get("elapsed_s", 0))
                return result
        except Exception as exc:
            logger.error("LLM generate failed: %s", exc)
            return None

    @staticmethod
    async def get_settings() -> list[dict[str, Any]] | None:
        """Fetch settings metadata from the LLM service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.LLM_ENDPOINT}/settings")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("LLM get_settings failed: %s", exc)
            return None

    @staticmethod
    async def update_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
        """Update settings on the LLM service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.patch(
                    f"{config.LLM_ENDPOINT}/settings",
                    json={"settings": settings},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("LLM update_settings failed: %s", exc)
            return None
