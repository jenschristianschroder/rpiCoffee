"""Coffee Snark HTTP API, including the rpiCoffee service contract."""

import os
import re
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import FastAPI, HTTPException
from generator import LARGE_WEIGHTS, WEIGHTS, CoffeeModel, CommentGenerator
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field


def require_iso_string(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("Use an ISO date-time with an offset, for example 2026-11-04T16:30:00+01:00.")
    return value


CoffeeType = Annotated[str, Field(strict=True, min_length=1, max_length=80)]
ISODateTime = Annotated[AwareDatetime, BeforeValidator(require_iso_string)]


class BrewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coffee_type: CoffeeType
    timestamp: ISODateTime = Field(alias="datetime", description="ISO 8601 date-time with Z or an explicit UTC offset.")


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coffee_label: CoffeeType
    timestamp: ISODateTime = Field(description="ISO 8601 date-time with Z or an explicit UTC offset.")


class CommentResponse(BaseModel):
    comment: str


class GenerateResponse(BaseModel):
    response: str
    elapsed_s: float


def create_app(*, history_db=None, timezone=None, weights_path=None, model_variant=None):
    variants = {"classic": WEIGHTS, "large": LARGE_WEIGHTS}
    path = weights_path or os.environ.get("WEIGHTS_PATH")
    if path is None:
        variant = model_variant or os.environ.get("COFFEE_MODEL_VARIANT", "large")
        if variant not in variants:
            raise ValueError("COFFEE_MODEL_VARIANT must be classic or large.")
        path = variants[variant]
    model = CoffeeModel(
        path,
        timezone or os.environ.get("COFFEE_TIMEZONE", "Europe/Copenhagen"),
    )
    history = history_db or os.environ.get(
        "HISTORY_DB", str(Path(__file__).resolve().parent / "data" / "history.sqlite3")
    )
    generator = CommentGenerator(model, history)
    application = FastAPI(
        title="Coffee Snark",
        version="2.0.0",
        description="Generate a short, office-friendly comment for a coffee or unidentified machine activity.",
    )
    application.state.generator = generator
    model_info = model.summary()

    def generate_text(coffee_type, timestamp):
        try:
            return generator.comment(coffee_type, timestamp)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/coffee-types")
    def coffee_types():
        return {"coffee_types": list(model.data["coffee_types"]), "timezone": str(model.timezone)}

    @application.get("/model")
    def model_details():
        """Inspect the active model without consuming a comment or changing history."""
        return model_info

    @application.get("/manifest")
    def manifest():
        return {
            "name": "coffee-comments",
            "version": application.version,
            "description": (
                f"Offline coffee and mystery-event comments using the {model_info['variant']} authored model."
            ),
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
                {"name": "response", "type": "string", "description": "Generated comment for display, TTS, or saving."},
                {
                    "name": "elapsed_s",
                    "type": "float",
                    "description": "Generation time in seconds, including history update.",
                },
            ],
            "endpoints": {
                "execute": {"method": "POST", "path": "/generate"},
                "health": {"method": "GET", "path": "/health"},
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
