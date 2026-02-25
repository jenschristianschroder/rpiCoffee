"""
Ollama API adapter for hailo-ollama (Hailo AI HAT+ 2 / Hailo 10).

Translates the Ollama-compatible API (streaming NDJSON) into the same
normalised response dict that the rest of the pipeline expects:

    {"response": str, "tokens": int, "elapsed_s": float, "tokens_per_s": float}

The hailo-ollama server exposes:
    POST /api/generate   – text generation (streaming NDJSON)
    GET  /api/tags       – list available models
    GET  /api/ps         – running models
    GET  /api/version    – server version
    GET  /                – server ID string
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger("rpicoffee.ollama_adapter")

# ── Post-processing (mirrored from services/llm/server.py) ────────────────────
# These are needed client-side because the Ollama backend doesn't run our
# custom server.py so cannot do post-processing on the server.

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


# ── System prompt ─────────────────────────────────────────────────────────────
# Same intent as the fine-tuned model but explicit since Ollama uses a generic
# base model (e.g. qwen2:1.5b) rather than our fine-tuned coffee GGUF.

SYSTEM_PROMPT = (
    "You are a witty coffee commentator.\n\n"
    "Your job:\n"
    "- Write exactly ONE short sentence in English.\n"
    "- Make it humorous, clever, and lightly teasing.\n"
    "- Mention the coffee type, weekday, and time naturally.\n"
    "- Keep it punchy and specific.\n\n"
    "Style rules:\n"
    "- Dry humor, office-friendly, mildly sarcastic.\n"
    "- Sound like a sharp coworker with good taste in coffee.\n"
    "- Prefer clever observations over random jokes.\n"
    "- You may personify the coffee or the drinker.\n"
    "- Always address the user as 'you' and refer to the coffee by name.\n\n"
    "Output rules:\n"
    "- One sentence only.\n"
    "- 10 to 22 words.\n"
    "- No emojis. No hashtags. No quotes. No bullet points.\n"
    "- No explanations. Do not ask a question.\n"
    "- Do not mention being an AI. Do not repeat the input labels.\n"
    "- Do not mention any specific places, brands, companies, or locations."
)


# ── Ollama API calls ─────────────────────────────────────────────────────────

async def ollama_health(endpoint: str) -> dict[str, Any]:
    """
    Check hailo-ollama health via GET /api/tags.

    Returns {"enabled": True, "healthy": True/False, ...}
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{endpoint}/api/tags")
            r.raise_for_status()
            data = r.json()
            models = [m["name"] for m in data.get("models", [])]
            return {"enabled": True, "healthy": True, "status": "ok", "models": models}
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return {"enabled": True, "healthy": False, "error": str(exc)}


async def ollama_generate(
    endpoint: str,
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 256,
    temperature: float = 0.7,
    top_p: float = 0.9,
    tts: bool = False,
    keep_alive: int | str = -1,
    timeout: float = 60.0,
) -> dict[str, Any] | None:
    """
    Call the Ollama /api/generate endpoint (streaming NDJSON) and return a
    normalised result dict compatible with the rest of the pipeline.

    Returns
    -------
    dict with keys: response, tokens, elapsed_s, tokens_per_s
    None on failure.
    """
    if system is None:
        system = SYSTEM_PROMPT

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
    t0 = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
    except httpx.ConnectError:
        logger.error("Ollama: could not connect to %s", endpoint)
        return None
    except httpx.TimeoutException:
        logger.error("Ollama: request timed out after %.0fs", timeout)
        return None
    except Exception as exc:
        logger.error("Ollama generate failed: %s", exc)
        return None

    elapsed = time.perf_counter() - t0

    # Extract token counts from Ollama metadata
    eval_count = metadata.get("eval_count", 0)
    eval_ns = metadata.get("eval_duration", 0)
    tokens_per_s = (eval_count / (eval_ns / 1e9)) if eval_ns else 0

    # Post-process the response (same as llm/server.py does server-side)
    day_name, time_24h = _parse_timestamp(prompt)
    text = full_response.strip()
    text = _postprocess(text, day_name, time_24h)
    if tts:
        text = _tts_clean(text)

    logger.info(
        "Ollama generated %d tokens in %.2fs (%.1f tok/s)",
        eval_count, elapsed, tokens_per_s,
    )

    return {
        "response": text,
        "tokens": eval_count,
        "elapsed_s": round(elapsed, 2),
        "tokens_per_s": round(tokens_per_s, 1),
    }
