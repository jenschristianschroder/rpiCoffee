"""HTTP client for the remote save service."""

from __future__ import annotations

import base64
import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from config import config

logger = logging.getLogger("rpicoffee.remote_save")
_TIMEOUT = 30.0

_CSV_COLUMNS = ["label", "elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]


def _sensor_data_to_csv(sensor_data: list[dict[str, float]], label: str) -> str:
    """Convert raw sensor dicts to a CSV string with the classification label."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in sensor_data:
        writer.writerow({"label": label, **row})
    return buf.getvalue()


def _csv_to_base64(csv_str: str) -> str:
    """Base64-encode a CSV string."""
    return base64.b64encode(csv_str.encode("utf-8")).decode("ascii")


class RemoteSaveClient:
    """Calls the remote save service /save and /health endpoints."""

    @staticmethod
    async def health() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.REMOTE_SAVE_ENDPOINT}/health")
                r.raise_for_status()
                return {"enabled": True, "healthy": True, **r.json()}
        except Exception as exc:
            logger.warning("remote_save health check failed: %s", exc)
            return {"enabled": True, "healthy": False, "error": str(exc)}

    @staticmethod
    async def save(
        result: dict[str, Any],
        raw_sensor_data: list[dict[str, float]],
    ) -> bool | None:
        """
        Send brew result and full raw sensor data to the remote save service.

        The sensor data is converted to CSV and base64-encoded before sending.

        Returns True on success, None on failure or if disabled.
        """
        if not config.REMOTE_SAVE_ENABLED:
            logger.info("remote_save is disabled – skipping save")
            return None

        try:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            label = result.get("label") or "unknown"
            confidence = result.get("confidence") or 0.0

            csv_str = _sensor_data_to_csv(raw_sensor_data, label)

            payload = {
                "name": f"{label.lower()}-{now_str}",
                "data": csv_str,
                "text": result.get("text") or "",
                "coffee_type": label.lower(),
                "confidence": confidence,
                "file_content": _csv_to_base64(csv_str),
                "file_name": f"{label.lower()}-{now_str}.csv",
            }

            logger.info("Sending coffee_type=%s to remote save", label.lower())

            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                r = await client.post(
                    f"{config.REMOTE_SAVE_ENDPOINT}/save",
                    json=payload,
                )
                r.raise_for_status()
                resp = r.json()
                logger.info("Remote save successful: %s", resp.get("record_id", resp.get("id", "?")))
                return True
        except Exception as exc:
            logger.error("remote_save failed: %s", exc)
            return None

    @staticmethod
    async def get_settings() -> list[dict[str, Any]] | None:
        """Fetch settings metadata from the remote-save service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{config.REMOTE_SAVE_ENDPOINT}/settings")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("remote_save get_settings failed: %s", exc)
            return None

    @staticmethod
    async def update_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
        """Update settings on the remote-save service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.patch(
                    f"{config.REMOTE_SAVE_ENDPOINT}/settings",
                    json={"settings": settings},
                )
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            logger.error("remote_save update_settings failed: %s", exc)
            return None
