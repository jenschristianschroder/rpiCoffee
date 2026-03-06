"""Pydantic models for the service manifest specification."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ManifestInput(BaseModel):
    """A single named input that a service accepts."""

    name: str = Field(..., description="Machine name used in input_map references")
    type: str = Field(..., description="Data type: string, int, float, bool, object, array, binary")
    required: bool = Field(True, description="Whether the pipeline must provide this input")
    description: str = Field("", description="Human-readable description")


class ManifestOutput(BaseModel):
    """A single named output that a service produces."""

    name: str = Field(..., description="Machine name used in output references")
    type: str = Field(..., description="Data type: string, int, float, bool, object, array, binary")
    description: str = Field("", description="Human-readable description")


class ManifestEndpoint(BaseModel):
    """HTTP method + path for a service endpoint."""

    method: str = Field(..., description="HTTP method (GET, POST, PATCH, etc.)")
    path: str = Field(..., description="URL path relative to service root")


class ManifestEndpoints(BaseModel):
    """Collection of well-known endpoints exposed by a service."""

    execute: ManifestEndpoint = Field(..., description="The main execution endpoint")
    health: ManifestEndpoint = Field(..., description="Health check endpoint")
    settings: ManifestEndpoint | None = Field(None, description="Settings read endpoint")
    update_settings: ManifestEndpoint | None = Field(None, description="Settings update endpoint")


class ServiceManifest(BaseModel):
    """Full manifest returned by GET /manifest on a pipeline service."""

    name: str = Field(..., description="Unique kebab-case service identifier")
    version: str = Field(..., description="Semantic version string")
    description: str = Field(..., description="Human-readable service summary")
    inputs: list[ManifestInput] = Field(default_factory=list, description="Inputs the service accepts")
    outputs: list[ManifestOutput] = Field(default_factory=list, description="Outputs the service produces")
    endpoints: ManifestEndpoints = Field(..., description="Well-known service endpoints")
    failure_modes: list[str] = Field(
        default_factory=lambda: ["skip", "halt"],
        description="Supported failure handling modes",
    )
