"""rpiCoffee – main FastAPI application."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import config
from admin.router import router as admin_router
from pipeline import run_pipeline, run_pipeline_streaming
from services.classifier_client import ClassifierClient
from services.llm_client import LLMClient
from services.tts_client import TTSClient
from services.remote_save_client import RemoteSaveClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("rpicoffee")

AUDIO_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("rpiCoffee starting up")
    cfg = config.to_dict()
    for svc in ("CLASSIFIER", "LLM", "TTS", "REMOTE_SAVE"):
        enabled = cfg.get(f"{svc}_ENABLED", False)
        endpoint = cfg.get(f"{svc}_ENDPOINT", "n/a")
        logger.info("  %s: %s (%s)", svc, "enabled" if enabled else "disabled", endpoint)
    yield
    logger.info("rpiCoffee shutting down")


app = FastAPI(title="rpiCoffee", version="1.0.0", lifespan=lifespan)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "admin" / "templates"))

# Routers
app.include_router(admin_router, prefix="/admin", tags=["admin"])

# Serve generated audio files
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


# ── API endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Kiosk display – no auth required."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/brew")
async def brew():
    """Run the full pipeline: sensor → classifier → llm → tts."""
    result = await run_pipeline()
    return result


@app.get("/api/brew/stream")
async def brew_stream():
    """Stream the brew pipeline as Server-Sent Events."""
    return StreamingResponse(
        run_pipeline_streaming(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/services/status")
async def services_status():
    """Check health of all backend services."""
    statuses: dict[str, dict] = {}

    if config.CLASSIFIER_ENABLED:
        statuses["classifier"] = await ClassifierClient.health()
    else:
        statuses["classifier"] = {"enabled": False}

    if config.LLM_ENABLED:
        statuses["llm"] = await LLMClient.health()
    else:
        statuses["llm"] = {"enabled": False}

    if config.TTS_ENABLED:
        statuses["tts"] = await TTSClient.health()
    else:
        statuses["tts"] = {"enabled": False}

    if config.REMOTE_SAVE_ENABLED:
        statuses["remote_save"] = await RemoteSaveClient.health()
    else:
        statuses["remote_save"] = {"enabled": False}

    statuses["sensor_mock"] = {"enabled": config.SENSOR_MOCK_ENABLED}

    return statuses
