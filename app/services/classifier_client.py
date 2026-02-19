"""HTTP client for the classifier service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from config import config

logger = logging.getLogger("rpicoffee.classifier_client")
_TIMEOUT = 30.0  # classification may take a while with large payloads


class ClassifierClient:
    """Calls classifier /classify and /health endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.CLASSIFIER_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("Classifier health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def classify(data: list[dict[str, float]]) -> dict[str, Any] | None:
        """
        Send sensor data to classifier for classification.

        Parameters
        ----------
        data : list of dicts
            Each dict has keys: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

        Returns
        -------
        dict with 'label' and 'confidence', or None on failure.
        """
        if not config.CLASSIFIER_ENABLED:
            logger.info("Classifier is disabled – skipping classification")
            return None

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.CLASSIFIER_ENDPOINT}/classify",
                    json={"data": data},
                )
                r.raise_for_status()
                result = r.json()
                logger.info("Classification result: %s (%.2f)", result["label"], result["confidence"])
                return result
        except Exception as exc:
            logger.error("Classifier classify failed: %s", exc)
            return None
