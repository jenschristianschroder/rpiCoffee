"""
Lightweight inference server for the quantised GGUF coffee model.
Uses llama-cpp-python (C++ backend with ARM NEON on Pi).

Endpoints:
    POST /generate   {"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00"}
    GET  /health

Run:
    python server.py                              # defaults
    python server.py --model coffee-gguf/coffee-Q4_K_M.gguf --port 8080
"""

import argparse
import json
import re
import time
from datetime import datetime

from http.server import HTTPServer, BaseHTTPRequestHandler
from llama_cpp import Llama

SYSTEM_PROMPT = (
    "You are a witty coffee commentator. Given a coffee type and time, "
    "write a short, humorous observation about drinking that coffee at that time. "
    "Do not mention any specific places, brands, companies, or locations."
)

# Tuned for Raspberry Pi: small context, limited threads
DEFAULT_MODEL = "coffee-gguf/coffee-f16.gguf" #"model/coffee-Q4_K_M.gguf"
DEFAULT_CTX = 1024
DEFAULT_THREADS = 4      # Pi 4 has 4 cores; Pi 5 also 4
DEFAULT_BATCH = 64       # smaller batch = less memory pressure

model: Llama = None  # type: ignore

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
        # Replace all 12H time references with the correct 24H time
        text = _12H_RE.sub(time_24h, text)
    # Remove hallucinated place/brand references
    text = _strip_places(text)
    return text


# Patterns that match "at <Place>", "at the <Place>", or standalone brand/place names
# surrounded by word boundaries. Add entries as the model hallucinates new ones.
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
    # First try "at the Swiss National Bank" → ""
    text = _PLACE_AT_RE.sub("", text)
    # Then standalone mentions
    text = _PLACE_BARE_RE.sub("", text)
    # Clean up leftover double spaces
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def tts_clean(text: str) -> str:
    """Optimize text for natural text-to-speech output."""
    # Expand 24H times: "07:00" → "7 o'clock", "14:30" → "14 30"
    def _expand_time(m):
        h, mn = int(m.group(1)), m.group(2)
        if mn == "00":
            return f"{h} o'clock"
        return f"{h} {mn}"
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _expand_time, text)

    # Remove quotes (single and double) — TTS reads them as "quote"
    text = text.replace('"', '').replace("'", "")

    # Replace parentheses with commas for natural pauses
    text = re.sub(r"\s*\(", ", ", text)
    text = re.sub(r"\)\s*", ", ", text)

    # Remove em-dashes and replace with commas
    text = text.replace("—", ", ").replace("--", ", ")

    # Collapse multiple commas/spaces
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\s{2,}", " ", text)

    # Clean trailing/leading punctuation artifacts
    text = text.strip().strip(",").strip()

    return text


def build_prompt(user_msg: str) -> str:
    """Build a Qwen2.5 chat-template prompt."""
    return (
        f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
        f"<|im_start|>user\n{user_msg}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/generate":
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        user_prompt = body.get("prompt", "")
        max_tokens = body.get("max_tokens", 256)
        temperature = body.get("temperature", 0.7)
        top_p = body.get("top_p", 0.9)
        tts = body.get("tts", False)

        if not user_prompt:
            self._respond(400, {"error": "prompt is required"})
            return

        day_name, time_24h, _ = parse_timestamp(user_prompt)
        prompt = build_prompt(user_prompt)
        # Reset KV cache to ensure each inference is independent
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
        # Post-process: convert 12H times to 24H and add weekday
        text = postprocess(text, day_name, time_24h)
        if tts:
            text = tts_clean(text)
        tokens = result["usage"]["completion_tokens"]

        self._respond(200, {
            "response": text,
            "tokens": tokens,
            "elapsed_s": round(elapsed, 2),
            "tokens_per_s": round(tokens / elapsed, 1) if elapsed > 0 else 0,
        })

    def _respond(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # quieter logs
        print(f"[server] {args[0]} {args[1]}")


def main():
    global model
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--ctx", type=int, default=DEFAULT_CTX)
    p.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    args = p.parse_args()

    print(f"Loading model: {args.model}")
    print(f"  context={args.ctx}  threads={args.threads}  batch={args.batch}")
    model = Llama(
        model_path=args.model,
        n_ctx=args.ctx,
        n_threads=args.threads,
        n_batch=args.batch,
        last_n_tokens_size=32,
        verbose=False,
    )
    print(f"Model loaded. Serving on {args.host}:{args.port}")

    server = HTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
