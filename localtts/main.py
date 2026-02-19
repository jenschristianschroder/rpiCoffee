"""
localtts – local text-to-speech service powered by Piper TTS.

Runs in Docker on a Raspberry Pi — no cloud required.
API matches the spec in localtts.md.
"""

from __future__ import annotations

import io
import logging
import subprocess
import wave
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("localtts")

app = FastAPI(title="localtts", version="1.0.0", docs_url="/docs")

# Piper model directory (downloaded at Docker build time)
MODEL_DIR = Path("/opt/piper/models")
DEFAULT_VOICE = "en_US-lessac-medium"


def _find_model(voice: str) -> Path | None:
    """Locate the .onnx model file for a given voice name."""
    for ext in (".onnx",):
        candidate = MODEL_DIR / f"{voice}{ext}"
        if candidate.exists():
            return candidate
    # Try subdirectory
    subdir = MODEL_DIR / voice
    if subdir.is_dir():
        onnx_files = list(subdir.glob("*.onnx"))
        if onnx_files:
            return onnx_files[0]
    return None


def _list_voices() -> list[str]:
    """List available voice model names."""
    voices: list[str] = []
    for p in MODEL_DIR.glob("*.onnx"):
        voices.append(p.stem)
    for d in MODEL_DIR.iterdir():
        if d.is_dir():
            for p in d.glob("*.onnx"):
                voices.append(d.name)
                break
    return sorted(set(voices)) if voices else [DEFAULT_VOICE]


class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)
    voice: str = Field(default=DEFAULT_VOICE)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/voices")
async def voices():
    return {"voices": _list_voices()}


@app.post("/synthesize")
async def synthesize_post(req: SynthesizeRequest):
    return _synthesize(req.text, req.voice, req.speed)


@app.get("/synthesize")
async def synthesize_get(
    text: str = Query(..., min_length=1, max_length=10000),
    voice: str = Query(default=DEFAULT_VOICE),
    speed: float = Query(default=1.0, ge=0.25, le=4.0),
):
    return _synthesize(text, voice, speed)


def _synthesize(text: str, voice: str, speed: float) -> Response:
    """Run Piper TTS and return WAV audio."""
    model_path = _find_model(voice)
    if model_path is None:
        raise HTTPException(status_code=400, detail=f"Voice model '{voice}' not found")

    config_path = model_path.with_suffix(".onnx.json")

    cmd = [
        "piper",
        "--model", str(model_path),
        "--output-raw",
        "--length-scale", str(1.0 / speed),
    ]
    if config_path.exists():
        cmd.extend(["--config", str(config_path)])

    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.error("Piper error: %s", result.stderr.decode(errors="replace"))
            raise HTTPException(status_code=500, detail="TTS synthesis failed")

        # Piper outputs raw 16-bit 22050 Hz mono PCM – wrap in WAV
        raw_audio = result.stdout
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(22050)
            wf.writeframes(raw_audio)

        wav_bytes = wav_buffer.getvalue()
        logger.info("Synthesized %d bytes of WAV audio", len(wav_bytes))

        return Response(content=wav_bytes, media_type="audio/wav")

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="TTS synthesis timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Piper binary not found")
