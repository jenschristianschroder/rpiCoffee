"""HTTP client for the classifier service."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from config import config

logger = logging.getLogger("rpicoffee.classifier_client")
_TIMEOUT = 30.0  # classification may take a while with large payloads
_TRAIN_TIMEOUT = 120.0  # training timeout


class ClassifierClient:
    """Calls classifier endpoints: classify, train, upload-model, model info."""

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

    @staticmethod
    async def train(data_dir: str | None = None) -> dict[str, Any] | None:
        """Trigger model training on the classifier service."""
        try:
            body = {}
            if data_dir:
                body["data_dir"] = data_dir
            async with httpx.AsyncClient(timeout=_TRAIN_TIMEOUT) as client:
                r = await client.post(
                    f"{config.CLASSIFIER_ENDPOINT}/train",
                    json=body,
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier train request failed: %s", exc)
            return None

    @staticmethod
    async def train_status() -> dict[str, Any] | None:
        """Poll training progress from the classifier service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.CLASSIFIER_ENDPOINT}/train/status")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier train_status failed: %s", exc)
            return None

    @staticmethod
    async def upload_model(file) -> dict[str, Any] | None:
        """Upload a .joblib model file to the classifier service."""
        try:
            contents = await file.read()
            filename = getattr(file, "filename", "model.joblib")
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.CLASSIFIER_ENDPOINT}/upload-model",
                    files={"file": (filename, contents, "application/octet-stream")},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier upload_model failed: %s", exc)
            return None

    @staticmethod
    async def model_info() -> dict[str, Any] | None:
        """Get model metadata from the classifier service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.CLASSIFIER_ENDPOINT}/model/info")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier model_info failed: %s", exc)
            return None

    @staticmethod
    async def get_labels() -> list[str]:
        """Get available labels from the classifier service's training data."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.CLASSIFIER_ENDPOINT}/labels")
                r.raise_for_status()
                return r.json().get("labels", [])
        except Exception as exc:
            logger.error("Classifier get_labels failed: %s", exc)
            return []

    @staticmethod
    async def get_settings() -> list[dict[str, Any]] | None:
        """Fetch settings metadata from the classifier service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.CLASSIFIER_ENDPOINT}/settings")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier get_settings failed: %s", exc)
            return None

    @staticmethod
    async def update_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
        """Update settings on the classifier service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.patch(
                    f"{config.CLASSIFIER_ENDPOINT}/settings",
                    json={"settings": settings},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("Classifier update_settings failed: %s", exc)
            return None
