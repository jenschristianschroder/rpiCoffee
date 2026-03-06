"""
Lightweight inference server for the quantised GGUF coffee model.

Uses llama-cpp-python (C++ backend with ARM NEON on Pi) served via FastAPI.

Endpoints:
    POST /generate   {"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00"}
    GET  /health
    GET  /settings
    PATCH /settings

Run:
    python server.py                              # defaults
    python server.py --model coffee-gguf/coffee-Q4_K_M.gguf --port 8080
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from llama_cpp import Llama

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("llm")

SYSTEM_PROMPT = (
    "You are a witty coffee commentator. Given a coffee type and time, "
    "write a short, humorous observation about drinking that coffee at that time. "
    "Do not mention any specific places, brands, companies, or locations."
)

# Tuned for Raspberry Pi: small context, limited threads
DEFAULT_MODEL = "coffee-gguf/coffee-f16.gguf"
DEFAULT_CTX = 1024
DEFAULT_THREADS = 4      # Pi 4 has 4 cores; Pi 5 also 4
DEFAULT_BATCH = 64       # smaller batch = less memory pressure

SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", "/data")) / "settings.json"

model: Llama = None  # type: ignore

# ── Runtime settings (mutable) ───────────────────────────────────
_runtime: dict[str, Any] = {}

_SETTINGS_REGISTRY: list[dict[str, str]] = [
    {"key": "MODEL_PATH", "name": "Model Path", "description": "Path to the GGUF model file", "type": "str"},
    {"key": "CTX_SIZE", "name": "Context Size", "description": "Model context window size in tokens", "type": "int"},
    {"key": "THREADS", "name": "Threads", "description": "Number of CPU threads for inference", "type": "int"},
    {"key": "BATCH_SIZE", "name": "Batch Size", "description": "Batch size for prompt evaluation", "type": "int"},
    {"key": "LLM_MAX_TOKENS", "name": "Max Tokens", "description": "Maximum number of tokens to generate per request", "type": "int"},
    {"key": "LLM_TEMPERATURE", "name": "Temperature", "description": "Controls randomness: lower is more deterministic, higher is more creative (0.0\u20132.0)", "type": "float"},
    {"key": "LLM_TOP_P", "name": "Top-P", "description": "Nucleus sampling: only tokens within this cumulative probability are considered (0.0\u20131.0)", "type": "float"},
    {"key": "LLM_TTS", "name": "TTS Mode", "description": "Optimize output text for text-to-speech when enabled", "type": "bool"},
    {"key": "LLM_SYSTEM_MESSAGE", "name": "System Message", "description": "System prompt sent to the model to control tone, style, and output format", "type": "str"},
]


def _load_settings() -> None:
    """Load persisted settings from disk, falling back to env/defaults."""
    _runtime["MODEL_PATH"] = os.environ.get("MODEL_PATH", DEFAULT_MODEL)
    _runtime["CTX_SIZE"] = int(os.environ.get("CTX", str(DEFAULT_CTX)))
    _runtime["THREADS"] = int(os.environ.get("THREADS", str(DEFAULT_THREADS)))
    _runtime["BATCH_SIZE"] = int(os.environ.get("BATCH", str(DEFAULT_BATCH)))
    _runtime["LLM_MAX_TOKENS"] = int(os.environ.get("LLM_MAX_TOKENS", "256"))
    _runtime["LLM_TEMPERATURE"] = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
    _runtime["LLM_TOP_P"] = float(os.environ.get("LLM_TOP_P", "0.9"))
    _runtime["LLM_TTS"] = os.environ.get("LLM_TTS", "true").lower() in ("true", "1", "yes")
    _runtime["LLM_SYSTEM_MESSAGE"] = os.environ.get("LLM_SYSTEM_MESSAGE", SYSTEM_PROMPT)

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


# Regex to find ISO timestamps in prompts
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2})?")


def parse_timestamp(user_msg: str) -> tuple[str, str, str]:
    """Extract metadata from an ISO timestamp in the prompt.
    Returns (day_name, time_24h, stripped_iso) or ("", "", "") if none found."""
    m = _TS_RE.search(user_msg)
    if m:
        try:
            dt = datetime.fromisoformat(m.group())
            return dt.strftime("%A"), dt.strftime("%H:%M"), m.group()
        except ValueError:
            pass
    return "", "", ""


# Regex patterns for 12-hour times in model output
_12H_RE = re.compile(
    r"\b(1[0-2]|0?[1-9])(?:[:.]([0-5]\d))?\s*(am|pm)\b", re.IGNORECASE
)


def postprocess(text: str, day_name: str, time_24h: str) -> str:
    """Replace 12-hour times with the correct 24H time and strip places/brands."""
    if time_24h:
        text = _12H_RE.sub(time_24h, text)
    text = _strip_places(text)
    return text


# Patterns that match "at <Place>", "at the <Place>", or standalone brand/place names
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


def _strip_places(text: str) -> str:
    """Remove place/brand references from text."""
    text = _PLACE_AT_RE.sub("", text)
    text = _PLACE_BARE_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def tts_clean(text: str) -> str:
    """Optimize text for natural text-to-speech output."""
    def _expand_time(m):
        h, mn = int(m.group(1)), m.group(2)
        if mn == "00":
            return f"{h} o'clock"
        return f"{h} {mn}"
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _expand_time, text)
    text = text.replace('"', '').replace("'", "")
    text = re.sub(r"\s*\(", ", ", text)
    text = re.sub(r"\)\s*", ", ", text)
    text = text.replace("—", ", ").replace("--", ", ")
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip().strip(",").strip()
    return text


def build_prompt(user_msg: str, system: str | None = None) -> str:
    """Build a Qwen2.5 chat-template prompt."""
    sys_msg = system if system else SYSTEM_PROMPT
    return (
        f"<|im_start|>system\n{sys_msg}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


# ── Request / Response models ────────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    tts: bool | None = None


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


# ── App lifecycle ────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    _load_settings()
    logger.info("Loading model: %s", _runtime["MODEL_PATH"])
    logger.info("  context=%s  threads=%s  batch=%s",
                _runtime["CTX_SIZE"], _runtime["THREADS"], _runtime["BATCH_SIZE"])
    model = Llama(
        model_path=_runtime["MODEL_PATH"],
        n_ctx=_runtime["CTX_SIZE"],
        n_threads=_runtime["THREADS"],
        n_batch=_runtime["BATCH_SIZE"],
        last_n_tokens_size=32,
        verbose=False,
    )
    logger.info("Model loaded.")
    yield


app = FastAPI(title="rpicoffee-llm", version="2.0.0", lifespan=lifespan)


# ── Endpoints ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/generate")
async def generate(req: GenerateRequest):
    if not req.prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    # Resolve from runtime defaults when request omits a value
    max_tokens = req.max_tokens if req.max_tokens is not None else _runtime["LLM_MAX_TOKENS"]
    temperature = req.temperature if req.temperature is not None else _runtime["LLM_TEMPERATURE"]
    top_p = req.top_p if req.top_p is not None else _runtime["LLM_TOP_P"]
    tts = req.tts if req.tts is not None else _runtime["LLM_TTS"]
    system = req.system if req.system is not None else _runtime["LLM_SYSTEM_MESSAGE"]

    day_name, time_24h, _ = parse_timestamp(req.prompt)
    prompt = build_prompt(req.prompt, system=system)
    model.reset()
    t0 = time.perf_counter()
    result = model(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
        stop=["<|im_end|>", "<|im_start|>"],
        echo=False,
        repeat_penalty=1.15,
    )
    elapsed = time.perf_counter() - t0

    text = result["choices"][0]["text"].strip()
    text = postprocess(text, day_name, time_24h)
    if tts:
        text = tts_clean(text)
    tokens = result["usage"]["completion_tokens"]

    return {
        "response": text,
        "tokens": tokens,
        "elapsed_s": round(elapsed, 2),
        "tokens_per_s": round(tokens / elapsed, 1) if elapsed > 0 else 0,
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

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=None)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8002)
    p.add_argument("--ctx", type=int, default=None)
    p.add_argument("--threads", type=int, default=None)
    p.add_argument("--batch", type=int, default=None)
    args = p.parse_args()

    # CLI args override env vars for backward compatibility
    if args.model:
        os.environ["MODEL_PATH"] = args.model
    if args.ctx:
        os.environ["CTX"] = str(args.ctx)
    if args.threads:
        os.environ["THREADS"] = str(args.threads)
    if args.batch:
        os.environ["BATCH"] = str(args.batch)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
