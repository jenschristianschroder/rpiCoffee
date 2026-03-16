"""
Ollama proxy service for Hailo AI HAT+ 2 / hailo-ollama.

Proxies text generation requests to an Ollama-compatible API, applies
post-processing (12H→24H time correction, place/brand stripping, TTS
optimisation), and returns a normalised response.

Endpoints:
    POST  /generate   {"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00"}
    GET   /health
    GET   /settings
    PATCH /settings
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("llm-ollama")

SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", "/data")) / "settings.json"

# ── Runtime settings (mutable) ───────────────────────────────────
_runtime: dict[str, Any] = {}

_SETTINGS_REGISTRY: list[dict[str, str]] = [
    {
        "key": "OLLAMA_ENDPOINT", "name": "Ollama Endpoint",
        "description": "URL of the upstream Ollama API server", "type": "str",
    },
    {"key": "OLLAMA_MODEL", "name": "Model", "description": "Ollama model name to use for generation", "type": "str"},
    {
        "key": "OLLAMA_KEEP_ALIVE", "name": "Keep Alive",
        "description": "Ollama keep_alive: -1 = keep model loaded forever, 0 = unload immediately, or seconds",
        "type": "int",
    },
    {
        "key": "LLM_MAX_TOKENS", "name": "Max Tokens",
        "description": "Maximum number of tokens to generate per request", "type": "int",
    },
    {
        "key": "LLM_TEMPERATURE", "name": "Temperature",
        "description": "Controls randomness: lower is more deterministic, higher is more creative (0.0\u20132.0)",
        "type": "float",
    },
    {
        "key": "LLM_TOP_P", "name": "Top-P",
        "description": "Nucleus sampling: only tokens within this cumulative probability are considered (0.0\u20131.0)",
        "type": "float",
    },
    {
        "key": "LLM_TTS", "name": "TTS Mode",
        "description": "Optimize output text for text-to-speech when enabled", "type": "bool",
    },
    {
        "key": "LLM_SYSTEM_MESSAGE", "name": "System Message",
        "description": "System prompt sent to the model to control tone, style, and output format", "type": "str",
    },
]

DEFAULT_SYSTEM_PROMPT = (
    "You are a witty coffee commentator. Given a coffee type and time, "
    "write a short, humorous observation about drinking that coffee at that time. "
    "Do not mention any specific places, brands, companies, or locations."
)


def _load_settings() -> None:
    """Load persisted settings from disk, falling back to env/defaults."""
    _runtime["OLLAMA_ENDPOINT"] = os.environ.get("OLLAMA_ENDPOINT", "http://localhost:8000")
    _runtime["OLLAMA_MODEL"] = os.environ.get("OLLAMA_MODEL", "qwen2:1.5b")
    _runtime["OLLAMA_KEEP_ALIVE"] = int(os.environ.get("OLLAMA_KEEP_ALIVE", "-1"))
    _runtime["LLM_MAX_TOKENS"] = int(os.environ.get("LLM_MAX_TOKENS", "256"))
    _runtime["LLM_TEMPERATURE"] = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
    _runtime["LLM_TOP_P"] = float(os.environ.get("LLM_TOP_P", "0.9"))
    _runtime["LLM_TTS"] = os.environ.get("LLM_TTS", "true").lower() in ("true", "1", "yes")
    _runtime["LLM_SYSTEM_MESSAGE"] = os.environ.get("LLM_SYSTEM_MESSAGE", DEFAULT_SYSTEM_PROMPT)

    if SETTINGS_PATH.exists():
        try:
            persisted = json.loads(SETTINGS_PATH.read_text())
            for entry in _SETTINGS_REGISTRY:
                key = entry["key"]
                if key in persisted:
                    dtype = entry["type"]
                    if dtype == "int":
                        _runtime[key] = int(persisted[key])
                    elif dtype == "float":
                        _runtime[key] = float(persisted[key])
                    elif dtype == "bool":
                        v = persisted[key]
                        _runtime[key] = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
                    else:
                        _runtime[key] = str(persisted[key])
        except (json.JSONDecodeError, OSError):
            pass


def _save_settings() -> None:
    """Persist current runtime settings to disk."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(_runtime, indent=2))


# ── Post-processing helpers ──────────────────────────────────────

_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2})?")

_12H_RE = re.compile(
    r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*(am|pm)\b", re.IGNORECASE
)

_PLACES = [
    "Swiss National Bank", "Swiss national bank",
    "Starbucks", "Costa", "Dunkin", "Peet's", "Tim Hortons",
    "McDonald's", "Nespresso", "Lavazza", "Illy",
]
_PLACE_AT_RE = re.compile(
    r"\s*\bat\s+(?:the\s+)?(?:" + "|".join(re.escape(p) for p in _PLACES) + r")\b",
    re.IGNORECASE,
)
_PLACE_BARE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _PLACES) + r")\b",
    re.IGNORECASE,
)


def _parse_timestamp(user_msg: str) -> tuple[str, str]:
    """Extract weekday name and 24H time from an ISO timestamp in the prompt."""
    m = _TS_RE.search(user_msg)
    if m:
        try:
            dt = datetime.fromisoformat(m.group())
            return dt.strftime("%A"), dt.strftime("%H:%M")
        except ValueError:
            pass
    return "", ""


def _postprocess(text: str, day_name: str, time_24h: str) -> str:
    """Replace 12H times with correct 24H time and strip hallucinated places."""
    if time_24h:
        text = _12H_RE.sub(time_24h, text)
    text = _PLACE_AT_RE.sub("", text)
    text = _PLACE_BARE_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _tts_clean(text: str) -> str:
    """Optimise text for natural text-to-speech output."""
    def _expand_time(m: re.Match) -> str:
        h, mn = int(m.group(1)), m.group(2)
        if mn == "00":
            return f"{h} o'clock"
        return f"{h} {mn}"

    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _expand_time, text)
    text = text.replace('"', "").replace("'", "")
    text = re.sub(r"\s*\(", ", ", text)
    text = re.sub(r"\)\s*", ", ", text)
    text = text.replace("—", ", ").replace("--", ", ")
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip().strip(",").strip()
    return text


# ── Request / Response models ────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str | None = Field(None)
    coffee_label: str | None = Field(None, description="Coffee type label — builds prompt automatically")
    timestamp: str | None = Field(None, description="ISO-8601 timestamp — used with coffee_label")
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    tts: bool | None = None


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


# ── Ollama proxy call ────────────────────────────────────────────

async def _ollama_generate(
    prompt: str,
    *,
    system: str,
    max_tokens: int,
    temperature: float,
    top_p: float,
    keep_alive: int,
) -> tuple[str, dict[str, Any]]:
    """Call Ollama /api/generate (streaming NDJSON) and return (text, metadata)."""
    endpoint = _runtime["OLLAMA_ENDPOINT"]
    model = _runtime["OLLAMA_MODEL"]

    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "top_p": top_p,
            "num_predict": max_tokens,
        },
    }

    full_response = ""
    metadata: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{endpoint}/api/generate",
            json=payload,
        ) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("response", "")
                full_response += token
                if chunk.get("done"):
                    metadata = chunk

    return full_response, metadata


# ── App lifecycle ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_settings()
    logger.info("Ollama proxy started — upstream: %s  model: %s",
                _runtime["OLLAMA_ENDPOINT"], _runtime["OLLAMA_MODEL"])
    yield


app = FastAPI(title="rpicoffee-llm-ollama", version="1.0.0", lifespan=lifespan)


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/manifest")
async def manifest():
    return {
        "name": "llm-ollama",
        "version": "1.0.0",
        "description": "Coffee comment generator via Ollama proxy (Hailo AI HAT+)",
        "inputs": [
            {"name": "coffee_label", "type": "string", "required": True,
             "description": "Coffee type label from classifier"},
            {"name": "timestamp", "type": "string", "required": True, "description": "ISO-8601 timestamp of brew"},
            {"name": "system", "type": "string", "required": False,
             "description": "System prompt override — falls back to the service LLM_SYSTEM_MESSAGE setting"},
        ],
        "outputs": [
            {"name": "response", "type": "string", "description": "Generated witty comment"},
            {"name": "tokens", "type": "int", "description": "Number of tokens generated"},
            {"name": "elapsed_s", "type": "float", "description": "Generation time in seconds"},
            {"name": "tokens_per_s", "type": "float", "description": "Tokens per second"},
        ],
        "endpoints": {
            "execute": {"method": "POST", "path": "/generate"},
            "health": {"method": "GET", "path": "/health"},
            "settings": {"method": "GET", "path": "/settings"},
            "update_settings": {"method": "PATCH", "path": "/settings"},
        },
        "failure_modes": ["skip", "halt"],
    }


@app.get("/health")
async def health():
    """Check own status and upstream Ollama connectivity."""
    endpoint = _runtime["OLLAMA_ENDPOINT"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{endpoint}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"status": "ok", "ollama": "connected", "models": models}
    except Exception as exc:
        logger.warning("Ollama upstream health check failed: %s", exc)
        return {"status": "degraded", "ollama": "unreachable", "error": str(exc)}


@app.post("/generate")
async def generate(req: GenerateRequest):
    # Accept either a raw prompt or structured coffee_label + timestamp
    user_msg = (req.prompt or "").strip() or None
    if not user_msg:
        if req.coffee_label:
            ts = req.timestamp or datetime.now().astimezone().isoformat()
            user_msg = f"Write a statement about {req.coffee_label.title()} at {ts}"
        else:
            raise HTTPException(status_code=400, detail="prompt or coffee_label is required")

    max_tokens = req.max_tokens if req.max_tokens is not None else _runtime["LLM_MAX_TOKENS"]
    temperature = req.temperature if req.temperature is not None else _runtime["LLM_TEMPERATURE"]
    top_p = req.top_p if req.top_p is not None else _runtime["LLM_TOP_P"]
    tts = req.tts if req.tts is not None else _runtime["LLM_TTS"]
    system = req.system if req.system is not None else _runtime["LLM_SYSTEM_MESSAGE"]

    t0 = time.perf_counter()

    try:
        raw_text, metadata = await _ollama_generate(
            user_msg,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            keep_alive=_runtime["OLLAMA_KEEP_ALIVE"],
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail=f"Could not connect to Ollama at {_runtime['OLLAMA_ENDPOINT']}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Ollama request timed out")
    except Exception as exc:
        logger.error("Ollama generate failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    elapsed = time.perf_counter() - t0

    # Token stats from Ollama metadata
    eval_count = metadata.get("eval_count", 0)
    eval_ns = metadata.get("eval_duration", 0)
    tokens_per_s = (eval_count / (eval_ns / 1e9)) if eval_ns else 0

    # Post-process
    day_name, time_24h = _parse_timestamp(user_msg)
    text = raw_text.strip()
    text = _postprocess(text, day_name, time_24h)
    if tts:
        text = _tts_clean(text)

    return {
        "response": text,
        "tokens": eval_count,
        "elapsed_s": round(elapsed, 2),
        "tokens_per_s": round(tokens_per_s, 1),
    }


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
        elif dtype == "bool":
            _runtime[key] = value if isinstance(value, bool) else str(value).lower() in ("true", "1", "yes")
        else:
            _runtime[key] = str(value)
        updated.append(key)
    _save_settings()
    return {"updated": updated}


# ── CLI entry point ──────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")
