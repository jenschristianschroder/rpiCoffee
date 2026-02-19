"""Remote save mock service – stores brew results as JSON + CSV files."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("remote_save_mock")

app = FastAPI(title="remote_save_mock", version="1.0.0")

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data")) / "brews"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class SaveRequest(BaseModel):
    name: str
    data: str
    text: str
    coffee_type: str = ""
    confidence: float
    file_content: str  # base64-encoded file bytes
    file_name: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/save")
async def save(req: SaveRequest):
    """Decode the base64 file and write it plus a metadata JSON to disk."""
    # Decode file content
    try:
        file_bytes = base64.b64decode(req.file_content)
    except Exception as exc:
        logger.error("Failed to decode file_content: %s", exc)
        return {"saved": False, "error": f"Invalid base64: {exc}"}

    # Write the CSV / binary file
    file_path = DATA_DIR / req.file_name
    file_path.write_bytes(file_bytes)
    logger.info("Saved file: %s (%d bytes)", file_path, len(file_bytes))

    # Write metadata JSON
    import json
    meta = {
        "name": req.name,
        "data": req.data,
        "text": req.text,
        "coffee_type": req.coffee_type,
        "confidence": req.confidence,
        "file_name": req.file_name,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = DATA_DIR / f"{req.name}.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info("Saved metadata: %s", meta_path)

    return {"saved": True, "id": req.name}
