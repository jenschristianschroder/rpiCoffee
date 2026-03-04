"""Manage the rpicoffee-hailo-ollama systemd service.

Since the app runs natively on the host (not in Docker), we can call
``systemctl`` directly.  A sudoers drop-in installed by ``setup.sh``
grants the app user passwordless access to the four commands below.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from config import config

logger = logging.getLogger("rpicoffee.hailo_ollama")

_UNIT = "rpicoffee-hailo-ollama"


# ── Low-level helpers ────────────────────────────────────────────

async def _systemctl(*args: str) -> tuple[int, str]:
    """Run a ``systemctl`` sub-command and return (returncode, stdout)."""
    cmd = ["sudo", "systemctl", *args, _UNIT]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode().strip() if stdout else ""
    logger.debug("systemctl %s → rc=%s  %s", " ".join(args), proc.returncode, text)
    return proc.returncode, text


# ── Public API ───────────────────────────────────────────────────

async def is_active() -> bool:
    """Return *True* if the hailo-ollama service is currently running."""
    rc, _ = await _systemctl("is-active")
    return rc == 0


async def is_enabled() -> bool:
    """Return *True* if the hailo-ollama service is enabled at boot."""
    rc, _ = await _systemctl("is-enabled")
    return rc == 0


async def start_and_enable(timeout: int = 60) -> bool:
    """Start the service, enable it at boot, and wait for the health endpoint.

    Returns *True* if the service is healthy within *timeout* seconds.
    """
    logger.info("Starting and enabling %s …", _UNIT)

    await _systemctl("enable")
    await _systemctl("start")

    # Poll the health endpoint until it responds or we run out of time
    endpoint = config.get("LLM_OLLAMA_ENDPOINT") or "http://localhost:8000"
    health_url = f"{endpoint}/api/tags"
    elapsed = 0
    interval = 2
    while elapsed < timeout:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(health_url, timeout=2)
                if resp.status_code == 200:
                    logger.info("%s is healthy (%s)", _UNIT, health_url)
                    return True
        except Exception:
            pass
        await asyncio.sleep(interval)
        elapsed += interval

    logger.error("%s did not become healthy within %ss", _UNIT, timeout)
    return False


async def stop_and_disable() -> None:
    """Stop the running service and disable it from starting at boot."""
    logger.info("Stopping and disabling %s …", _UNIT)
    await _systemctl("stop")
    await _systemctl("disable")
    logger.info("%s stopped and disabled", _UNIT)
