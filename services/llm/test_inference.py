"""
Restart the server, run 5 inferences, save results to test_results.txt.

Usage:
    python test_inference.py
    python test_inference.py --model coffee-gguf/coffee-Q4_K_M.gguf --port 8000
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
import signal
import os

PROMPTS = [
    "Write a statement about Cappuccino at 2026-02-17T07:06:00+02:00",
    "Write a statement about Cappuccino at 2026-02-18T14:35:00+02:00",
    "Write a statement about Cappuccino at 2026-02-19T09:40:00+02:00",
    "Write a statement about Cappuccino at 2026-02-20T21:10:00+02:00",
    "Write a statement about Cappuccino at 2026-02-21T06:33:00+02:00",
]


def kill_existing(port):
    """Kill any process listening on the given port."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                pid = line.strip().split()[-1]
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid],
                    capture_output=True, timeout=5,
                )
    except Exception:
        pass
    time.sleep(2)


def wait_for_server(url, timeout=60):
    """Poll the health endpoint until the server is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{url}/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(2)
    return False


def generate(url, prompt):
    """Send a generate request and return the parsed response."""
    body = json.dumps({"prompt": prompt, "tts": True}).encode()
    req = urllib.request.Request(
        f"{url}/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="coffee-gguf/coffee-Q4_K_M.gguf")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--output", default="test_results.txt")
    args = p.parse_args()

    url = f"http://localhost:{args.port}"
    python = sys.executable

    # 1. Kill existing server
    print("Killing existing server...")
    kill_existing(args.port)

    # 2. Start server
    print(f"Starting server with model {args.model} on port {args.port}...")
    server_log = open("server.log", "w")
    server_proc = subprocess.Popen(
        [python, "server.py", "--model", args.model, "--port", str(args.port)],
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    # 3. Wait for server to be ready
    print("Waiting for server to load model...")
    if not wait_for_server(url):
        print("ERROR: Server did not start within 60s")
        server_proc.kill()
        sys.exit(1)
    print("Server ready.\n")

    # 4. Run inferences
    results = []
    for i, prompt in enumerate(PROMPTS, 1):
        print(f"Test {i}/5: {prompt}")
        try:
            r = generate(url, prompt)
            results.append({
                "test": i,
                "prompt": prompt,
                "response": r["response"],
                "tokens": r["tokens"],
                "elapsed_s": r["elapsed_s"],
                "tokens_per_s": r["tokens_per_s"],
            })
            print(f"  -> {r['response'][:80]}...")
            print(f"     ({r['tokens']} tokens, {r['tokens_per_s']} t/s)\n")
        except Exception as e:
            results.append({"test": i, "prompt": prompt, "error": str(e)})
            print(f"  -> ERROR: {e}\n")

    # 5. Save results
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(f"Coffee LLM Inference Test - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Model: {args.model}\n")
        f.write("=" * 70 + "\n\n")
        for r in results:
            f.write(f"--- Test {r['test']} ---\n")
            f.write(f"Prompt:   {r['prompt']}\n")
            if "error" in r:
                f.write(f"Error:    {r['error']}\n")
            else:
                f.write(f"Response: {r['response']}\n")
                f.write(f"Tokens:   {r['tokens']} | Time: {r['elapsed_s']}s | Speed: {r['tokens_per_s']} t/s\n")
            f.write("\n")

    print(f"Results saved to {args.output}")

    # 6. Kill server
    server_proc.kill()
    server_log.close()
    print("Server stopped.")


if __name__ == "__main__":
    main()
