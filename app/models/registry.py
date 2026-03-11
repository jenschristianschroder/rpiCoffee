"""Pydantic models for the service registry and pipeline configuration."""

from __future__ import annotations

from datetime import datetime, timezone

from models.manifest import ServiceManifest
from pydantic import BaseModel, Field


class ServiceRegistration(BaseModel):
    """A registered pipeline service with its manifest and metadata."""

    name: str = Field(..., description="Unique service identifier (kebab-case)")
    endpoint: str = Field(..., description="Base URL of the service (e.g. http://classifier:8001)")
    manifest: ServiceManifest | None = Field(None, description="Fetched service manifest")
    enabled: bool = Field(True, description="Whether this service is available for pipeline use")
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of initial registration",
    )


class PipelineStep(BaseModel):
    """A single step in the pipeline configuration."""

    service: str = Field(..., description="Name of the registered service to execute")
    input_map: dict[str, str] = Field(
        default_factory=dict,
        description="Maps service input names to source references ($sensor.data, $classifier.label, etc.)",
    )
    on_failure: str = Field("skip", description="Failure policy: skip, halt, or retry")
    retry_count: int = Field(1, ge=1, description="Number of retries when on_failure=retry")
    enabled: bool = Field(True, description="Whether this step is active in the pipeline")


class PipelineConfig(BaseModel):
    """Full pipeline configuration: registered services + ordered steps."""

    services: dict[str, ServiceRegistration] = Field(
        default_factory=dict,
        description="Map of service name → registration",
    )
    pipeline: list[PipelineStep] = Field(
        default_factory=list,
        description="Ordered list of pipeline steps to execute",
    )
