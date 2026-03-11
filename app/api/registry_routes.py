"""API routes for the service registry and pipeline configuration."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from models.registry import PipelineStep
from pydantic import BaseModel, Field
from registry import registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registry", tags=["registry"])


# ── Request models ───────────────────────────────────────────────

class RegisterServiceRequest(BaseModel):
    name: str = Field(..., description="Unique service name (kebab-case)")
    endpoint: str = Field(..., description="Base URL of the service")


class SetEnabledRequest(BaseModel):
    enabled: bool


class UpdateServiceRequest(BaseModel):
    endpoint: str = Field(..., description="New base URL for the service")


class PipelineUpdateRequest(BaseModel):
    pipeline: list[PipelineStep]


class ValidatePipelineRequest(BaseModel):
    pipeline: list[PipelineStep] | None = Field(
        None,
        description="Pipeline steps to validate.  Omit to validate the currently stored pipeline.",
    )


# ── Service CRUD ─────────────────────────────────────────────────

@router.get("/services")
async def list_services() -> list[dict[str, Any]]:
    """List all registered services with their manifests."""
    return [
        {
            "name": reg.name,
            "endpoint": reg.endpoint,
            "enabled": reg.enabled,
            "registered_at": reg.registered_at.isoformat(),
            "manifest": reg.manifest.model_dump() if reg.manifest else None,
        }
        for reg in registry.list_all()
    ]


@router.post("/services", status_code=201)
async def register_service(req: RegisterServiceRequest) -> dict[str, Any]:
    """Register a new service by name + endpoint.  Fetches its manifest."""
    existing = registry.get(req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Service '{req.name}' is already registered")
    reg = await registry.register(req.name, req.endpoint)
    return {
        "name": reg.name,
        "endpoint": reg.endpoint,
        "enabled": reg.enabled,
        "manifest": reg.manifest.model_dump() if reg.manifest else None,
    }


@router.delete("/services/{name}", status_code=204)
async def unregister_service(name: str) -> None:
    """Remove a registered service and any pipeline steps referencing it."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    registry.unregister(name)


@router.post("/services/{name}/refresh")
async def refresh_manifest(name: str) -> dict[str, Any]:
    """Re-fetch the manifest for a registered service."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    manifest = await registry.refresh_manifest(name)
    return {"name": name, "manifest": manifest.model_dump() if manifest else None}


@router.patch("/services/{name}/enabled")
async def set_service_enabled(name: str, req: SetEnabledRequest) -> dict[str, Any]:
    """Enable or disable a registered service."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    registry.set_enabled(name, req.enabled)
    return {"name": name, "enabled": req.enabled}


@router.patch("/services/{name}")
async def update_service(name: str, req: UpdateServiceRequest) -> dict[str, Any]:
    """Update a service's endpoint URL and re-fetch its manifest."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    reg = await registry.update_service(name, req.endpoint)
    return {
        "name": reg.name,
        "endpoint": reg.endpoint,
        "enabled": reg.enabled,
        "manifest": reg.manifest.model_dump() if reg.manifest else None,
    }


# ── Health ───────────────────────────────────────────────────────

@router.get("/health")
async def health_check_all() -> dict[str, dict[str, Any]]:
    """Run health checks on all registered services."""
    return await registry.health_check_all()


@router.get("/health/{name}")
async def health_check(name: str) -> dict[str, Any]:
    """Run a health check on a single service."""
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return await registry.health_check(name)


# ── Pipeline configuration ───────────────────────────────────────

@router.get("/pipeline")
async def get_pipeline() -> dict[str, Any]:
    """Return the current pipeline configuration."""
    cfg = registry.get_pipeline_config()
    return {
        "pipeline": [step.model_dump() for step in cfg.pipeline],
        "services": {
            name: {
                "name": reg.name,
                "endpoint": reg.endpoint,
                "enabled": reg.enabled,
                "manifest": reg.manifest.model_dump() if reg.manifest else None,
            }
            for name, reg in cfg.services.items()
        },
    }


@router.put("/pipeline")
async def set_pipeline(req: PipelineUpdateRequest) -> dict[str, Any]:
    """Replace the entire pipeline step configuration."""
    # Validate all referenced services exist
    for step in req.pipeline:
        if not registry.get(step.service):
            raise HTTPException(
                status_code=400,
                detail=f"Step references unregistered service '{step.service}'",
            )
    registry.set_pipeline(req.pipeline)
    return {"pipeline": [step.model_dump() for step in req.pipeline]}


@router.post("/pipeline/validate")
async def validate_pipeline(req: ValidatePipelineRequest = ValidatePipelineRequest()) -> dict[str, Any]:
    """Validate pipeline wiring and return any issues.

    If a ``pipeline`` list is supplied in the request body it is validated
    instead of the currently stored pipeline, allowing the editor to validate
    the canvas state without saving first.
    """
    steps = req.pipeline if req.pipeline is not None else None
    issues = registry.validate_pipeline(steps=steps)
    return {
        "valid": len(issues) == 0,
        "issues": issues,
    }
