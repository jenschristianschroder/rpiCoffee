"""
Test script for Raspberry Pi 5 + AI Hat+ 2 (Hailo 10) running hailo-ollama.

The server exposes an Ollama-compatible API at http://<host>:8000.
Discovered model: qwen2:1.5b (Q4_0, HEF format)

Usage:
    python test_hailo.py                          # Interactive chat mode
    python test_hailo.py --prompt "Hello world"   # Single prompt
    python test_hailo.py --benchmark              # Latency & throughput benchmark
    python test_hailo.py --info                   # Show server & model info
    python test_hailo.py --all                    # Run all checks then enter chat
"""

import argparse
import json
import sys
import time

try:
    import requests
except ImportError:
    print("Missing dependency: requests\nInstall with:  pip install requests")
    sys.exit(1)


# ── Configuration ──────────────────────────────────────────────────────────────
DEFAULT_HOST = "10.57.175.39"
DEFAULT_PORT = 8000
DEFAULT_MODEL = "qwen2:1.5b"
DEFAULT_KEEP_ALIVE = -1  # -1 = keep model loaded forever, 0 = unload immediately, or "30m" etc.
DEFAULT_SYSTEM = """You are a witty coffee commentator.

Your job:
- Write exactly ONE short sentence in English.
- Make it humorous, clever, and lightly teasing.
- Mention the coffee type, weekday, and time naturally.
- Keep it punchy and specific.

Style rules:
- Dry humor, office-friendly, mildly sarcastic.
- Sound like a sharp coworker with good taste in coffee.
- Prefer clever observations over random jokes.
- You may personify the coffee or the drinker.
- Always address the user as "you" and refer to the coffee by name.

Output rules:
- One sentence only.
- 10 to 22 words.
- No emojis.
- No hashtags.
- No quotes.
- No bullet points.
- No explanations.
- Do not ask a question.
- Do not mention being an AI.
- Do not repeat the input labels.

If the input is missing a field, still respond with one witty sentence using what is available."""

TIMEOUT = 120  # seconds – generation can be slow on edge devices


def base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


# ── Helpers ────────────────────────────────────────────────────────────────────
def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── 1. Server info ─────────────────────────────────────────────────────────────
def show_info(url: str):
    """Display server version, loaded models, and running models."""
    print_header(f"Server Info  →  {url}")

    # Root
    try:
        r = requests.get(url, timeout=10)
        print(f"  Server     : {r.text.strip()}")
        print(f"  Framework  : {r.headers.get('Server', 'unknown')}")
    except Exception as e:
        print(f"  Root check failed: {e}")
        return False

    # Version
    try:
        r = requests.get(f"{url}/api/version", timeout=10)
        data = r.json()
        print(f"  Version    : {data.get('version', 'unknown')}")
    except Exception:
        pass

    # Available models
    try:
        r = requests.get(f"{url}/api/tags", timeout=10)
        models = r.json().get("models", [])
        print(f"  Models     : {len(models)} available")
        for m in models:
            size_mb = m.get("size", 0) / (1024 * 1024)
            details = m.get("details", {})
            print(f"    - {m['name']}")
            print(f"      Format    : {details.get('format', '?')}")
            print(f"      Family    : {details.get('family', '?')}")
            print(f"      Params    : {details.get('parameter_size', '?')}")
            print(f"      Quant     : {details.get('quantization_level', '?')}")
            print(f"      Size      : {size_mb:.1f} MB")
            print(f"      Modified  : {m.get('modified_at', '?')}")
    except Exception as e:
        print(f"  Failed to list models: {e}")

    # Running models
    try:
        r = requests.get(f"{url}/api/ps", timeout=10)
        running = r.json().get("models", [])
        print(f"  Running    : {len(running)} model(s) loaded in memory")
        for m in running:
            print(f"    - {m.get('name', m)}")
    except Exception:
        pass

    return True


# ── 2. Generate (single prompt) ───────────────────────────────────────────────
def generate(url: str, model: str, prompt: str, stream: bool = True,
             system: str | None = None, temperature: float | None = None,
             keep_alive: int | str = DEFAULT_KEEP_ALIVE,
             verbose: bool = True) -> dict:
    """
    Send a prompt to /api/generate and return the response.

    When stream=True, tokens are printed as they arrive.
    Returns a dict with 'response', 'total_duration', 'eval_count', etc.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
        "keep_alive": keep_alive,
    }
    if system:
        payload["system"] = system
    if temperature is not None:
        payload["options"] = {"temperature": temperature}

    if verbose:
        print(f"\n  Model : {model}")
        print(f"  Prompt: {prompt[:100]}{'...' if len(prompt) > 100 else ''}")
        print(f"  Keep-alive: {keep_alive}")
        print()

    full_response = ""
    metadata = {}

    try:
        if stream:
            # Streaming: read line-by-line NDJSON
            with requests.post(
                f"{url}/api/generate",
                json=payload,
                stream=True,
                timeout=TIMEOUT,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    full_response += token
                    if verbose:
                        print(token, end="", flush=True)
                    if chunk.get("done"):
                        metadata = chunk
        else:
            # Non-streaming: single JSON response
            r = requests.post(
                f"{url}/api/generate",
                json=payload,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            full_response = data.get("response", "")
            metadata = data
            if verbose:
                print(full_response, end="")

    except requests.ConnectionError:
        print("\n  ERROR: Could not connect to server.")
        return {}
    except requests.Timeout:
        print(f"\n  ERROR: Request timed out after {TIMEOUT}s.")
        return {}
    except Exception as e:
        print(f"\n  ERROR: {e}")
        return {}

    if verbose:
        print()  # newline after streamed output
        _print_generation_stats(metadata)

    metadata["response"] = full_response
    return metadata


def _print_generation_stats(meta: dict):
    """Print timing / throughput stats from the generate response."""
    total_ns = meta.get("total_duration", 0)
    load_ns = meta.get("load_duration", 0)
    prompt_ns = meta.get("prompt_eval_duration", 0)
    eval_ns = meta.get("eval_duration", 0)
    prompt_count = meta.get("prompt_eval_count", 0)
    eval_count = meta.get("eval_count", 0)

    if not total_ns:
        return

    total_s = total_ns / 1e9
    load_s = load_ns / 1e9
    prompt_s = prompt_ns / 1e9
    eval_s = eval_ns / 1e9

    print("\n  ── Stats ──────────────────────────────────────")
    print(f"  Total time       : {total_s:.2f}s")
    if load_s:
        print(f"  Model load       : {load_s:.2f}s")
    if prompt_count and prompt_s:
        print(f"  Prompt eval      : {prompt_count} tokens in {prompt_s:.2f}s "
              f"({prompt_count / prompt_s:.1f} tok/s)")
    if eval_count and eval_s:
        print(f"  Generation       : {eval_count} tokens in {eval_s:.2f}s "
              f"({eval_count / eval_s:.1f} tok/s)")
    print("  ────────────────────────────────────────────────")


# ── 3. Benchmark ──────────────────────────────────────────────────────────────
BENCHMARK_PROMPTS = [
    "What is 2 + 2?",
    "Explain quantum computing in one sentence.",
    "Write a haiku about artificial intelligence.",
    "What is the capital of France?",
    "List 3 benefits of edge AI.",
]


def benchmark(url: str, model: str, rounds: int = 5,
              keep_alive: int | str = DEFAULT_KEEP_ALIVE):
    """Run multiple prompts and report throughput statistics."""
    print_header(f"Benchmark  →  {model}  ({rounds} prompts)")

    results = []
    for i, prompt in enumerate(BENCHMARK_PROMPTS[:rounds]):
        print(f"\n  [{i+1}/{rounds}] \"{prompt}\"")
        start = time.time()
        meta = generate(url, model, prompt, stream=False, verbose=False,
                        keep_alive=keep_alive)
        wall_time = time.time() - start

        eval_count = meta.get("eval_count", 0)
        eval_ns = meta.get("eval_duration", 0)
        tok_per_s = (eval_count / (eval_ns / 1e9)) if eval_ns else 0
        response_preview = meta.get("response", "")[:80].replace("\n", " ")

        print(f"         Wall: {wall_time:.2f}s  |  Tokens: {eval_count}  |  "
              f"{tok_per_s:.1f} tok/s")
        print(f"         → {response_preview}")

        results.append({
            "prompt": prompt,
            "wall_time": wall_time,
            "eval_count": eval_count,
            "tok_per_s": tok_per_s,
        })

    # Summary
    if results:
        avg_wall = sum(r["wall_time"] for r in results) / len(results)
        avg_tps = sum(r["tok_per_s"] for r in results) / len(results)
        total_tokens = sum(r["eval_count"] for r in results)
        print("\n  ── Summary ────────────────────────────────────")
        print(f"  Avg wall time  : {avg_wall:.2f}s")
        print(f"  Avg throughput : {avg_tps:.1f} tok/s")
        print(f"  Total tokens   : {total_tokens}")
        print("  ────────────────────────────────────────────────")

    return results


# ── 4. Interactive chat ───────────────────────────────────────────────────────
def interactive_chat(url: str, model: str, system: str | None = None,
                     keep_alive: int | str = DEFAULT_KEEP_ALIVE):
    """Simple interactive chat loop using /api/generate with context."""
    print_header(f"Interactive Chat  →  {model}  (keep_alive={keep_alive})")
    print("  Type your message and press Enter. Type 'quit' or 'exit' to stop.")
    print("  Type '/clear' to reset context, '/info' for server info.\n")

    context = []  # Ollama context for multi-turn

    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Goodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("  Goodbye!")
            break
        if user_input.lower() == "/clear":
            context = []
            print("  Context cleared.\n")
            continue
        if user_input.lower() == "/info":
            show_info(url)
            continue

        payload = {
            "model": model,
            "prompt": user_input,
            "stream": True,
            "keep_alive": keep_alive,
        }
        if system:
            payload["system"] = system
        if context:
            payload["context"] = context

        print(f"\n{model} > ", end="", flush=True)

        try:
            with requests.post(
                f"{url}/api/generate",
                json=payload,
                stream=True,
                timeout=TIMEOUT,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = chunk.get("response", "")
                    print(token, end="", flush=True)
                    if chunk.get("done"):
                        # Save context for multi-turn conversation
                        context = chunk.get("context", [])
                        _print_generation_stats(chunk)
        except Exception as e:
            print(f"\n  ERROR: {e}")

        print()


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Test Raspberry Pi 5 + Hailo 10 AI Hat+ (hailo-ollama server)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"Pi IP address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Server port (default: {DEFAULT_PORT})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Model name (default: {DEFAULT_MODEL})")
    parser.add_argument("--prompt", type=str,
                        help="Single prompt to send (non-interactive)")
    parser.add_argument("--system", type=str, default=DEFAULT_SYSTEM,
                        help=f"System prompt for the model (default: \"{DEFAULT_SYSTEM[:40]}...\")")
    parser.add_argument("--temperature", type=float,
                        help="Sampling temperature")
    parser.add_argument("--no-stream", action="store_true",
                        help="Disable streaming (wait for full response)")
    parser.add_argument("--keep-alive", default=str(DEFAULT_KEEP_ALIVE),
                        help='Keep model loaded: -1=forever, 0=unload, or duration '
                             f'like "30m" (default: {DEFAULT_KEEP_ALIVE})')
    parser.add_argument("--info", action="store_true",
                        help="Show server & model info")
    parser.add_argument("--benchmark", action="store_true",
                        help="Run throughput benchmark")
    parser.add_argument("--rounds", type=int, default=5,
                        help="Number of benchmark prompts (default: 5)")
    parser.add_argument("--all", action="store_true",
                        help="Run info + benchmark, then enter chat")
    args = parser.parse_args()

    url = base_url(args.host, args.port)

    # Parse keep_alive: try int first, otherwise pass as string (e.g. "30m")
    try:
        keep_alive: int | str = int(args.keep_alive)
    except ValueError:
        keep_alive = args.keep_alive

    # Quick connectivity check
    try:
        r = requests.get(url, timeout=10)
        print(f"\n  Connected to {url}  ({r.text.strip()})")
    except Exception:
        print(f"\n  ERROR: Cannot reach {url}. Is the Pi online?")
        sys.exit(1)

    if args.info or args.all:
        show_info(url)

    if args.benchmark or args.all:
        benchmark(url, args.model, args.rounds, keep_alive=keep_alive)

    if args.prompt:
        generate(url, args.model, args.prompt,
                 stream=not args.no_stream,
                 system=args.system,
                 temperature=args.temperature,
                 keep_alive=keep_alive)
    elif not args.info and not args.benchmark:
        # Default: enter interactive chat
        interactive_chat(url, args.model, args.system, keep_alive=keep_alive)
    elif args.all:
        interactive_chat(url, args.model, args.system, keep_alive=keep_alive)

    print()


if __name__ == "__main__":
    main()
