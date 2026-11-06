"""Coffee Snark HTTP API, including the rpiCoffee service contract."""

import json
import logging
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from generator import LARGE_WEIGHTS, WEIGHTS, CoffeeModel, CommentGenerator
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

logger = logging.getLogger(__name__)


def require_iso_string(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    ):
        raise ValueError("Use an ISO date-time with an offset, for example 2026-11-04T16:30:00+01:00.")
    return value


CoffeeType = Annotated[str, Field(strict=True, min_length=1, max_length=80)]
ISODateTime = Annotated[AwareDatetime, BeforeValidator(require_iso_string)]


class BrewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coffee_type: CoffeeType
    timestamp: ISODateTime = Field(
        alias="datetime",
        description="ISO 8601 date-time with Z or an explicit UTC offset.",
    )


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coffee_label: CoffeeType
    timestamp: ISODateTime = Field(description="ISO 8601 date-time with Z or an explicit UTC offset.")


class CommentResponse(BaseModel):
    comment: str


class GenerateResponse(BaseModel):
    response: str
    elapsed_s: float


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    COFFEE_MODEL_VARIANT: Literal["classic", "large"]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: ModelSettings


def save_settings(path: Path, settings: ModelSettings):
    """Replace settings atomically; a failed write leaves the previous file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(settings.model_dump(), stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def create_app(
    *,
    history_db=None,
    timezone=None,
    weights_path=None,
    model_variant=None,
    settings_path=None,
):
    variants = {"classic": WEIGHTS, "large": LARGE_WEIGHTS}
    history = history_db or os.environ.get(
        "HISTORY_DB", str(Path(__file__).resolve().parent / "data" / "history.sqlite3")
    )
    settings_file = Path(settings_path) if settings_path is not None else Path(history).parent / "settings.json"
    path = weights_path or os.environ.get("WEIGHTS_PATH")
    pinned_weights = path is not None
    variant = "custom"
    if path is None:
        if settings_file.exists():
            variant = ModelSettings.model_validate_json(settings_file.read_text(encoding="utf-8")).COFFEE_MODEL_VARIANT
        else:
            variant = model_variant or os.environ.get("COFFEE_MODEL_VARIANT", "large")
        if variant not in variants:
            raise ValueError("COFFEE_MODEL_VARIANT must be classic or large.")
        path = variants[variant]
    model = CoffeeModel(
        path,
        timezone or os.environ.get("COFFEE_TIMEZONE", "Europe/Copenhagen"),
    )
    generator = CommentGenerator(model, history)
    model_lock = threading.Lock()
    application = FastAPI(
        title="Coffee Snark",
        version="2.0.0",
        description="Generate a short, office-friendly comment for a coffee or unidentified machine activity.",
    )
    application.state.generator = generator
    model_info = model.summary()

    def generate_text(coffee_type, timestamp):
        try:
            with model_lock:
                return generator.comment(coffee_type, timestamp)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/coffee-types")
    def coffee_types():
        with model_lock:
            return {
                "coffee_types": list(model.data["coffee_types"]),
                "timezone": str(model.timezone),
            }

    @application.get("/model")
    def model_details():
        """Inspect the active model without consuming a comment or changing history."""
        with model_lock:
            return model_info

    @application.get("/settings")
    def settings():
        with model_lock:
            return [
                {
                    "key": "COFFEE_MODEL_VARIANT",
                    "name": "Model variant",
                    "value": variant,
                    "description": (
                        "A custom weights file is active. Remove the WEIGHTS_PATH override "
                        "and restart to select a bundled model."
                        if pinned_weights
                        else "Classic: 420 punchlines. Large: 1,000 punchlines plus sentence composition. "
                        "Changes apply immediately and persist across restarts."
                    ),
                    "type": "str",
                    "options": ["custom"] if pinned_weights else list(variants),
                    "read_only": pinned_weights,
                }
            ]

    @application.patch("/settings")
    def update_settings(request: SettingsUpdate | ModelSettings):
        nonlocal model, generator, model_info, variant
        selected = request.settings if isinstance(request, SettingsUpdate) else request
        with model_lock:
            if pinned_weights:
                raise HTTPException(
                    status_code=409,
                    detail="Remove the WEIGHTS_PATH override and restart before selecting a bundled model.",
                )
            try:
                next_model = CoffeeModel(variants[selected.COFFEE_MODEL_VARIANT], str(model.timezone))
                next_info = next_model.summary()
                next_generator = CommentGenerator(next_model, history)
                save_settings(settings_file, selected)
            except (OSError, ValueError, sqlite3.Error) as error:
                logger.exception("Could not apply model settings")
                raise HTTPException(
                    status_code=500,
                    detail="Could not apply model settings; the previous selection is unchanged.",
                ) from error
            model, generator, model_info = next_model, next_generator, next_info
            variant = selected.COFFEE_MODEL_VARIANT
            application.state.generator = generator
            return {"ok": True, "settings": selected.model_dump()}

    @application.get("/manifest")
    def manifest():
        with model_lock:
            active_variant = model_info["variant"]
        return {
            "name": "coffee-comments",
            "version": application.version,
            "description": (f"Offline coffee and mystery-event comments using the {active_variant} authored model."),
            "inputs": [
                {
                    "name": "coffee_label",
                    "type": "string",
                    "required": True,
                    "description": "Coffee type from the classifier, or other/unknown for unidentified activity.",
                },
                {
                    "name": "timestamp",
                    "type": "string",
                    "required": True,
                    "description": "ISO 8601 event date-time with Z or an explicit UTC offset.",
                },
            ],
            "outputs": [
                {
                    "name": "response",
                    "type": "string",
                    "description": "Generated comment for display, TTS, or saving.",
                },
                {
                    "name": "elapsed_s",
                    "type": "float",
                    "description": "Generation time in seconds, including history update.",
                },
            ],
            "endpoints": {
                "execute": {"method": "POST", "path": "/generate"},
                "health": {"method": "GET", "path": "/health"},
                "settings": {"method": "GET", "path": "/settings"},
                "update_settings": {"method": "PATCH", "path": "/settings"},
            },
            "failure_modes": ["skip", "halt"],
        }

    @application.post("/comment", response_model=CommentResponse)
    def comment(request: BrewRequest):
        return CommentResponse(comment=generate_text(request.coffee_type, request.timestamp))

    @application.post("/generate", response_model=GenerateResponse)
    def generate(request: GenerateRequest):
        started = perf_counter()
        text = generate_text(request.coffee_label, request.timestamp)
        return GenerateResponse(response=text, elapsed_s=perf_counter() - started)

    return application
