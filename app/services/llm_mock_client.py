"""Mock LLM client for local development without a real LLM service.

Returns randomised canned coffee comments so the pipeline completes
without requiring GPU/CPU-intensive inference.  Activate by setting
``LLM_BACKEND=mock`` in your ``.env`` or environment.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Any

from config import config

logger = logging.getLogger("rpicoffee.llm_mock_client")

_CANNED_RESPONSES: dict[str, list[str]] = {
    "espresso": [
        "An espresso this early means you're either very productive or very tired.",
        "Espresso: because sleep is just a suggestion at this point.",
        "You and that espresso have a standing appointment, apparently.",
        "Bold choice going straight for the espresso on a morning like this.",
    ],
    "cappuccino": [
        "A cappuccino right now says you have opinions about foam-to-coffee ratios.",
        "Cappuccino o'clock — your taste buds are clearly running the schedule today.",
        "That cappuccino foam won't appreciate itself, so here you are.",
        "Nothing says quiet confidence like ordering a cappuccino mid-morning.",
    ],
    "black": [
        "Black coffee — because you like your mornings as unfiltered as your opinions.",
        "Straight black, no frills — you clearly have somewhere important to be.",
        "A plain black coffee says more about your character than any resumé could.",
        "Black coffee at this hour means you've already made all your decisions for the day.",
    ],
    "_default": [
        "Interesting coffee choice — your taste buds are keeping everyone guessing today.",
        "Another coffee, another chance to pretend you're a morning person.",
        "That coffee isn't going to drink itself, and neither will your ambition.",
        "Coffee selected, judgement suspended — carry on with your caffeinated agenda.",
    ],
}


class MockLLMClient:
    """Returns canned coffee comments without calling a real LLM service."""

    @staticmethod
    async def health() -> dict[str, Any]:
        return {"enabled": True, "healthy": True, "backend": "mock"}

    @staticmethod
    async def generate(
        coffee_label: str,
        timestamp: datetime | None = None,
    ) -> dict[str, Any] | None:
        if not config.LLM_ENABLED:
            logger.info("LLM is disabled – skipping text generation")
            return None

        t0 = time.perf_counter()
        key = coffee_label.lower() if coffee_label.lower() in _CANNED_RESPONSES else "_default"
        response = random.choice(_CANNED_RESPONSES[key])  # noqa: S311
        elapsed = time.perf_counter() - t0
        tokens = len(response.split())

        logger.info("Mock LLM generated %d tokens in %.4fs (canned response)", tokens, elapsed)
        return {
            "response": response,
            "tokens": tokens,
            "elapsed_s": round(elapsed, 4),
            "tokens_per_s": round(tokens / max(elapsed, 0.0001), 1),
        }

    @staticmethod
    async def get_settings() -> list[dict[str, Any]] | None:
        return [
            {"key": "backend", "value": "mock", "editable": False,
             "description": "Mock backend — no real LLM inference"},
        ]

    @staticmethod
    async def update_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
        logger.info("Mock LLM update_settings called (no-op): %s", settings)
        return {"ok": True}
