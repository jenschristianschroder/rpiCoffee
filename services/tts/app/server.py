"""
Local TTS API Server

Provides a REST API for text-to-speech synthesis using Piper TTS.
Runs entirely locally — no cloud services required.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.tts_engine import TTSEngine, TTSEngineError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODELS_DIR = Path("/app/models")
SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", "/data")) / "settings.json"

app = FastAPI(
    title="Local TTS API",
    description="Local text-to-speech API powered by Piper TTS. No cloud required.",
    version="1.0.0",
)

# Initialize TTS engine at startup
engine: Optional[TTSEngine] = None

# ── Settings persistence ─────────────────────────────────────────
_runtime: dict[str, Any] = {}

_SETTINGS_REGISTRY: list[dict[str, str]] = [
    {"key": "MODELS_DIR", "name": "Models Directory", "description": "Path to the directory containing Piper voice models", "type": "str"},
    {"key": "DEFAULT_SPEED", "name": "Default Speed", "description": "Default speech speed multiplier (1.0 = normal)", "type": "float"},
]


def _load_settings() -> None:
    _runtime["MODELS_DIR"] = os.environ.get("MODELS_DIR", str(MODELS_DIR))
    _runtime["DEFAULT_SPEED"] = float(os.environ.get("DEFAULT_SPEED", "1.0"))

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


class SynthesizeRequest(BaseModel):
    """Request body for speech synthesis."""
    text: str = Field(..., min_length=1, max_length=10000, description="Text to synthesize")
    voice: Optional[str] = Field(None, description="Voice model name (e.g. en_US-lessac-medium)")
    speed: float = Field(1.0, ge=0.25, le=4.0, description="Speech speed multiplier")
    output_format: str = Field("wav", pattern="^(wav|raw)$", description="Output audio format")


class VoiceInfo(BaseModel):
    """Information about an available voice."""
    name: str
    language: str
    quality: str


@app.on_event("startup")
async def startup():
    """Initialize the TTS engine on server startup."""
    global engine
    _load_settings()
    try:
        engine = TTSEngine(models_dir=MODELS_DIR)
        voices = engine.list_voices()
        logger.info(f"TTS engine initialized with {len(voices)} voice(s): {voices}")
        if voices:
            engine.load_voice(voices[0])
            logger.info(f"Default voice loaded: {voices[0]}")
    except Exception as e:
        logger.error(f"Failed to initialize TTS engine: {e}")
        raise


@app.get("/manifest")
async def manifest():
    return {
        "name": "tts",
        "version": "1.0.0",
        "description": "Offline speech synthesis using Piper TTS",
        "inputs": [
            {"name": "text", "type": "string", "required": True, "description": "Text to synthesize"},
            {"name": "speed", "type": "float", "required": False, "description": "Speech speed multiplier (default 1.0)"},
        ],
        "outputs": [
            {"name": "audio", "type": "binary", "description": "Synthesized WAV audio bytes"},
        ],
        "endpoints": {
            "execute": {"method": "POST", "path": "/synthesize"},
            "health": {"method": "GET", "path": "/health"},
            "settings": {"method": "GET", "path": "/settings"},
            "update_settings": {"method": "PATCH", "path": "/settings"},
        },
        "failure_modes": ["skip", "halt"],
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "engine": "piper-tts",
        "loaded_voice": engine.current_voice if engine else None,
    }


@app.get("/voices", response_model=list[VoiceInfo])
async def list_voices():
    """List all available voice models."""
    if not engine:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")

    voices = engine.list_voices()
    result = []
    for v in voices:
        parts = v.split("-")
        lang = parts[0] if parts else "unknown"
        quality = parts[-1] if len(parts) > 1 else "unknown"
        result.append(VoiceInfo(name=v, language=lang, quality=quality))
    return result


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    """
    Synthesize speech from text.

    Returns a WAV audio file.
    """
    if not engine:
        raise HTTPException(status_code=503, detail="TTS engine not initialized")

    # Switch voice if requested
    if request.voice and request.voice != engine.current_voice:
        try:
            engine.load_voice(request.voice)
        except TTSEngineError as e:
            raise HTTPException(status_code=400, detail=str(e))

    try:
        start = time.time()
        audio_bytes = engine.synthesize(
            text=request.text,
            speed=request.speed,
        )
        elapsed = time.time() - start
        logger.info(
            f"Synthesized {len(request.text)} chars in {elapsed:.2f}s "
            f"({len(audio_bytes)} bytes)"
        )

        return Response(
            content=audio_bytes,
            media_type="audio/wav",
            headers={
                "X-Processing-Time": f"{elapsed:.3f}",
                "Content-Disposition": "inline; filename=speech.wav",
            },
        )
    except TTSEngineError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/synthesize")
async def synthesize_get(
    text: str = Query(..., min_length=1, max_length=10000),
    voice: Optional[str] = Query(None),
    speed: float = Query(1.0, ge=0.25, le=4.0),
):
    """
    Synthesize speech from text via GET request.
    Convenient for quick testing in a browser.
    """
    request = SynthesizeRequest(text=text, voice=voice, speed=speed)
    return await synthesize(request)


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
