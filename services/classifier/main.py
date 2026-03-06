"""
Coffee drink classifier – ML-based.

Accepts IMU sensor data and returns a classification using a trained
scikit-learn model.  Supports on-device training from CSV files and
model upload.

Endpoints
---------
GET  /health              → service health check
POST /classify            → classify sensor data
POST /train               → train model from CSV data
GET  /train/status        → poll training progress
POST /upload-model        → upload a .joblib model file
GET  /model/info          → current model metadata
GET  /labels              → labels found in training data
GET  /training-data       → list training CSV files
DELETE /training-data/{label}/{filename}  → delete a training file
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel, Field

from model_manager import model_manager, TRAINING_DIR, MODEL_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("classifier")

app = FastAPI(title="rpicoffee-classifier", version="2.0.0")

# Thread pool for background training
_executor = ThreadPoolExecutor(max_workers=1)

# ── Settings persistence ─────────────────────────────────────────
SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", "/data")) / "settings.json"

_runtime: dict[str, Any] = {}

_SETTINGS_REGISTRY: list[dict[str, str]] = [
    {"key": "CONFIDENCE_THRESHOLD", "name": "Confidence Threshold", "description": "Minimum confidence score to accept a classification result", "type": "float"},
    {"key": "MODEL_DIR", "name": "Model Directory", "description": "Path where trained model files are stored", "type": "str"},
    {"key": "TRAINING_DIR", "name": "Training Directory", "description": "Path to the directory containing labelled training CSVs", "type": "str"},
]


def _load_settings() -> None:
    _runtime["CONFIDENCE_THRESHOLD"] = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.6"))
    _runtime["MODEL_DIR"] = os.environ.get("MODEL_DIR", "/data/models")
    _runtime["TRAINING_DIR"] = os.environ.get("TRAINING_DIR", "/data/training")

    if SETTINGS_PATH.exists():
        try:
            persisted = json.loads(SETTINGS_PATH.read_text())
            for entry in _SETTINGS_REGISTRY:
                key = entry["key"]
                if key in persisted:
                    dtype = entry["type"]
                    if dtype == "float":
                        _runtime[key] = float(persisted[key])
                    elif dtype == "int":
                        _runtime[key] = int(persisted[key])
                    else:
                        _runtime[key] = str(persisted[key])
        except (json.JSONDecodeError, OSError):
            pass


def _save_settings() -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(_runtime, indent=2))


@app.on_event("startup")
async def _ensure_dirs():
    """Create data directories if they don't exist (covers volume-mount edge cases)."""
    _load_settings()
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)


# ── Request / Response models ────────────────────────────────────

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


class TrainRequest(BaseModel):
    data_dir: str | None = None


# ── Health ───────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": model_manager.is_ready,
    }


# ── Classify ─────────────────────────────────────────────────────

@app.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    """Classify sensor data using the trained ML model."""
    sensor_data = [reading.model_dump() for reading in req.data]
    result = model_manager.predict(sensor_data)
    return ClassifyResponse(label=result["label"], confidence=result["confidence"])


# ── Train ────────────────────────────────────────────────────────

@app.post("/train")
async def train(req: TrainRequest | None = None):
    """
    Trigger model training from CSV files.

    Scans /data/training/<label>/*.csv and /data/*.csv.sample files.
    Training runs in a background thread.
    """
    if model_manager.training_status.is_training:
        return {"status": "already_training", "message": "Training is already in progress"}

    data_dir = req.data_dir if req else None

    # Run training in background thread to avoid blocking
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_executor, model_manager.train, data_dir)

    return {"status": "training_started", "message": "Model training started in background"}


@app.get("/train/status")
async def train_status():
    """Poll training progress and results."""
    return model_manager.training_status.to_dict()


# ── Model upload ─────────────────────────────────────────────────

@app.post("/upload-model")
async def upload_model(file: UploadFile = File(...)):
    """Upload a .joblib model file and hot-swap the active model."""
    if not file.filename or not file.filename.endswith(".joblib"):
        return {"error": "File must be a .joblib file"}

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    save_path = MODEL_DIR / file.filename

    try:
        contents = await file.read()
        save_path.write_bytes(contents)

        result = model_manager.load_model(str(save_path))
        return result
    except Exception as e:
        logger.exception("Model upload failed")
        return {"error": str(e)}


# ── Model info ───────────────────────────────────────────────────

@app.get("/model/info")
async def model_info():
    """Return metadata about the currently loaded model."""
    return model_manager.get_info()


# ── Labels ───────────────────────────────────────────────────────

@app.get("/labels")
async def get_labels():
    """Return labels found in the training data directory."""
    labels: list[str] = []
    if TRAINING_DIR.exists():
        for d in sorted(TRAINING_DIR.iterdir()):
            if d.is_dir() and any(d.glob("*.csv")):
                labels.append(d.name)
    return {"labels": labels}


# ── Settings ─────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


@app.get("/settings")
async def get_settings():
    return [
        {**entry, "value": _runtime.get(entry["key"])}
        for entry in _SETTINGS_REGISTRY
    ]


@app.patch("/settings")
async def update_settings(req: SettingsUpdate):
    valid_keys = {e["key"] for e in _SETTINGS_REGISTRY}
    updated = []
    for key, value in req.settings.items():
        if key not in valid_keys:
            continue
        dtype = next(e["type"] for e in _SETTINGS_REGISTRY if e["key"] == key)
        if dtype == "int":
            _runtime[key] = int(value)
        elif dtype == "float":
            _runtime[key] = float(value)
        else:
            _runtime[key] = str(value)
        updated.append(key)
    _save_settings()
    return {"updated": updated}


# ── Training data management ────────────────────────────────────

@app.get("/training-data")
async def list_training_data():
    """List all training CSV files grouped by label."""
    result: dict[str, list[dict[str, Any]]] = {}

    if TRAINING_DIR.exists():
        for label_dir in sorted(TRAINING_DIR.iterdir()):
            if not label_dir.is_dir():
                continue
            label = label_dir.name
            files = []
            for csv_file in sorted(label_dir.glob("*.csv")):
                stat = csv_file.stat()
                files.append({
                    "filename": csv_file.name,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                })
            if files:
                result[label] = files

    return {"training_data": result}


@app.delete("/training-data/{label}/{filename}")
async def delete_training_file(label: str, filename: str):
    """Delete a specific training CSV file."""
    file_path = TRAINING_DIR / label / filename

    if not file_path.exists():
        return {"error": "File not found"}

    # Safety: ensure the path is within TRAINING_DIR
    try:
        file_path.resolve().relative_to(TRAINING_DIR.resolve())
    except ValueError:
        return {"error": "Invalid path"}

    file_path.unlink()
    logger.info("Deleted training file: %s/%s", label, filename)

    # Remove empty label directory
    label_dir = TRAINING_DIR / label
    if label_dir.exists() and not any(label_dir.iterdir()):
        label_dir.rmdir()

    return {"status": "deleted", "label": label, "filename": filename}
