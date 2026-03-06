"""Service registry — manages registered pipeline services and pipeline configuration.

Handles service registration, manifest fetching/validation, health checks,
and persistence of the pipeline configuration to ``data/pipeline.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import config
from models.manifest import ServiceManifest
from models.registry import PipelineConfig, PipelineStep, ServiceRegistration

logger = logging.getLogger(__name__)

_MANIFEST_TIMEOUT = 5.0
_HEALTH_TIMEOUT = 5.0


class ServiceRegistry:
    """Thread-safe singleton managing service registrations and pipeline config."""

    def __init__(self) -> None:
        self._config = PipelineConfig()
        self._path: Path | None = None
        self._loaded = False

    # ── Persistence ──────────────────────────────────────────────

    def load(self, path: Path | None = None) -> None:
        """Load registry state from disk.  Creates default config if missing."""
        self._path = path or Path(config.get("PIPELINE_CONFIG_PATH"))
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._config = PipelineConfig.model_validate(raw)
                logger.info("Loaded pipeline config from %s (%d services, %d steps)",
                            self._path, len(self._config.services), len(self._config.pipeline))
            except Exception:
                logger.exception("Failed to load pipeline config from %s — using defaults", self._path)
                self._config = PipelineConfig()
        else:
            logger.info("No pipeline config at %s — will create on first save", self._path)
            self._config = PipelineConfig()
        self._loaded = True

    def save(self) -> None:
        """Persist current state to disk."""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self._config.model_dump_json())
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info("Saved pipeline config to %s", self._path)

    # ── Service registration ─────────────────────────────────────

    async def register(self, name: str, endpoint: str) -> ServiceRegistration:
        """Register a service by fetching its manifest from ``GET /manifest``."""
        endpoint = endpoint.rstrip("/")
        manifest = await self._fetch_manifest(endpoint)
        reg = ServiceRegistration(
            name=name,
            endpoint=endpoint,
            manifest=manifest,
            enabled=True,
            registered_at=datetime.now(timezone.utc),
        )
        self._config.services[name] = reg
        self.save()
        logger.info("Registered service '%s' at %s", name, endpoint)
        return reg

    def unregister(self, name: str) -> None:
        """Remove a service and any pipeline steps referencing it."""
        self._config.services.pop(name, None)
        self._config.pipeline = [s for s in self._config.pipeline if s.service != name]
        self.save()
        logger.info("Unregistered service '%s'", name)

    def get(self, name: str) -> ServiceRegistration | None:
        """Look up a registered service by name."""
        return self._config.services.get(name)

    def list_all(self) -> list[ServiceRegistration]:
        """Return all registered services."""
        return list(self._config.services.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Enable or disable a registered service."""
        reg = self._config.services.get(name)
        if reg:
            reg.enabled = enabled
            self.save()

    # ── Manifest fetching ────────────────────────────────────────

    async def refresh_manifest(self, name: str) -> ServiceManifest | None:
        """Re-fetch the manifest for an already-registered service."""
        reg = self._config.services.get(name)
        if not reg:
            return None
        manifest = await self._fetch_manifest(reg.endpoint)
        reg.manifest = manifest
        self.save()
        return manifest

    async def _fetch_manifest(self, endpoint: str) -> ServiceManifest | None:
        """Fetch and validate a manifest from a service endpoint."""
        url = f"{endpoint}/manifest"
        try:
            async with httpx.AsyncClient(timeout=_MANIFEST_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return ServiceManifest.model_validate(resp.json())
        except Exception:
            logger.exception("Failed to fetch manifest from %s", url)
            return None

    # ── Health checks ────────────────────────────────────────────

    async def health_check(self, name: str) -> dict[str, Any]:
        """Check health of a single service using its manifest-declared endpoint."""
        reg = self._config.services.get(name)
        if not reg or not reg.manifest:
            return {"status": "unknown", "error": "not registered or no manifest"}
        ep = reg.manifest.endpoints.health
        url = f"{reg.endpoint}{ep.path}"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT) as client:
                resp = await client.request(ep.method, url)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}

    async def health_check_all(self) -> dict[str, dict[str, Any]]:
        """Run parallel health checks on all registered services."""
        if not self._config.services:
            return {}
        tasks = {
            name: self.health_check(name)
            for name in self._config.services
        }
        results: dict[str, dict[str, Any]] = {}
        for name, coro in tasks.items():
            results[name] = await coro
        return results

    # ── Pipeline configuration ───────────────────────────────────

    def get_pipeline(self) -> list[PipelineStep]:
        """Return the current ordered pipeline steps."""
        return list(self._config.pipeline)

    def set_pipeline(self, steps: list[PipelineStep]) -> None:
        """Replace the entire pipeline configuration."""
        self._config.pipeline = steps
        self.save()

    def get_pipeline_config(self) -> PipelineConfig:
        """Return the full config (services + pipeline)."""
        return self._config

    def validate_pipeline(self) -> list[str]:
        """Validate the current pipeline wiring.  Returns a list of issues."""
        issues: list[str] = []
        available_outputs: dict[str, set[str]] = {
            "$sensor": {"data", "timestamp"},
        }

        for i, step in enumerate(self._config.pipeline):
            reg = self._config.services.get(step.service)
            if not reg:
                issues.append(f"Step {i}: service '{step.service}' is not registered")
                continue
            if not reg.enabled:
                issues.append(f"Step {i}: service '{step.service}' is disabled")
            if not reg.manifest:
                issues.append(f"Step {i}: service '{step.service}' has no manifest")
                continue

            # Check that all required inputs are mapped
            for inp in reg.manifest.inputs:
                if inp.required and inp.name not in step.input_map:
                    issues.append(
                        f"Step {i} ({step.service}): required input '{inp.name}' is not mapped"
                    )

            # Check that mapped sources actually exist
            for inp_name, source_ref in step.input_map.items():
                parts = source_ref.lstrip("$").split(".", 1)
                if len(parts) != 2:
                    issues.append(
                        f"Step {i} ({step.service}): invalid source reference '{source_ref}'"
                    )
                    continue
                src_service, src_key = parts
                src_key_set = f"${src_service}"
                if src_key_set not in available_outputs:
                    issues.append(
                        f"Step {i} ({step.service}): input '{inp_name}' references "
                        f"'${src_service}' which hasn't produced output yet"
                    )
                elif src_key not in available_outputs[src_key_set]:
                    issues.append(
                        f"Step {i} ({step.service}): input '{inp_name}' references "
                        f"'{source_ref}' but '{src_key}' is not a known output of '${src_service}'"
                    )

            # Validate failure mode
            if step.on_failure not in ("skip", "halt", "retry"):
                issues.append(
                    f"Step {i} ({step.service}): invalid on_failure '{step.on_failure}'"
                )

            # Register this step's outputs for downstream steps
            step_outputs = {
                out.name for out in reg.manifest.outputs
            }
            available_outputs[f"${step.service}"] = step_outputs

        return issues


# ── Singleton ────────────────────────────────────────────────────
registry = ServiceRegistry()
