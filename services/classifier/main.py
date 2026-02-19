"""
Coffee drink classifier (mock).

Accepts IMU sensor data and returns a random classification.
When a real ML model is ready, replace this service.
"""

from __future__ import annotations

import random

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="rpicoffee-classifier", version="1.0.0")

_CONFIDENCE_THRESHOLD = 0.6
_LABELS = ["black", "espresso", "cappuccino"]


class SensorReading(BaseModel):
    acc_x: float
    acc_y: float
    acc_z: float
    gyro_x: float
    gyro_y: float
    gyro_z: float


class ClassifyRequest(BaseModel):
    data: list[SensorReading] = Field(..., min_length=1)


class ClassifyResponse(BaseModel):
    label: str
    confidence: float


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    """
    Mock classifier.

    Produces a random label with a plausible confidence score.
    If confidence falls below the threshold, returns "other".
    """
    # Pick a random label and confidence
    label = random.choice(_LABELS)
    confidence = round(random.uniform(0.55, 0.98), 4)

    if confidence < _CONFIDENCE_THRESHOLD:
        label = "other"

    return ClassifyResponse(label=label, confidence=confidence)
