# rpiCoffee — LLM Service

Fine-tuned text generation service that produces witty, sarcastic one-liners about coffee brews. Serves a quantized Qwen2.5-0.5B model via a lightweight HTTP API.

## Overview

The LLM service receives a coffee type and timestamp from the main app and generates a short, humorous commentary. It supports two backends:

| Backend | How it works | When to use |
|---------|-------------|-------------|
| **llama-cpp** (default) | CPU inference via `llama-cpp-python` serving a GGUF model | Standard setup — no special hardware needed |
| **ollama** | Hailo AI HAT+ 2 NPU via `hailo-ollama` | When Hailo accelerator is installed for faster inference |

The model is a Qwen2.5-0.5B-Instruct fine-tuned on 48 coffee commentary samples, quantized to GGUF Q4_K_M (~350 MB). Post-processing corrects 12H→24H time formats, strips hallucinated brand/place references, and cleans output for TTS consumption.

## API Reference

See [API.md](API.md) for the full API reference. Summary:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/generate` | Generate a coffee commentary |
| `GET` | `/settings` | Get configurable settings with current values |
| `PATCH` | `/settings` | Update settings (persisted to volume) |

The `/generate` endpoint accepts an optional `system` field to override the system prompt, and a `tts: true` flag to optimize output for speech synthesis.

## Configuration

### Environment variables (runtime)

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BACKEND` | `llama-cpp` | `llama-cpp` for CPU GGUF or `ollama` for Hailo NPU |
| `LLM_ENDPOINT` | `http://llm:8002` | URL of the llama-cpp server |
| `LLM_OLLAMA_ENDPOINT` | `http://localhost:8000` | URL of the ollama server |
| `LLM_MODEL` | `qwen2:1.5b` | Ollama model name (only used when backend=ollama) |
| `LLM_MAX_TOKENS` | `256` | Maximum tokens to generate per request |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature (higher = more creative) |
| `LLM_TOP_P` | `0.9` | Nucleus sampling threshold |
| `LLM_SYSTEM_MESSAGE` | *(coffee commentator)* | System prompt controlling tone, style, and output rules |
| `LLM_KEEP_ALIVE` | `-1` | Ollama keep_alive: -1=forever, 0=unload, or seconds |
| `LLM_TTS` | `true` | Automatically send generated text to TTS |

### Server flags

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `coffee-gguf/coffee-f16.gguf` | Path to GGUF model file |
| `--port` | `8000` | HTTP port |
| `--ctx` | `1024` | Context window size |
| `--threads` | `4` | CPU threads for inference |
| `--batch` | `64` | Batch size |

### Docker environment

| Variable | Default | Description |
|----------|---------|-------------|
| `OMP_NUM_THREADS` | `4` | OpenMP thread count |
| `THREADS` | `4` | llama-cpp thread count |

## Docker

### Build

```bash
docker build --platform linux/arm64 -t rpicoffee-llm ./services/llm
```

### Run

```bash
docker run -d -p 8002:8002 \
  -e OMP_NUM_THREADS=4 \
  -e THREADS=4 \
  rpicoffee-llm
```

### Docker Compose

Managed by `docker-compose.yml` under the `llm` profile. Memory limited to 2 GB, 4 CPUs:

```bash
docker compose --profile llm up -d
```

---

## Training Pipeline

Fine-tune Qwen2.5-0.5B-Instruct on a coffee commentary dataset, quantize to
GGUF Q4_K_M, and serve it from a Docker container on a Raspberry Pi.

### Training architecture

```
dataset-coffee.json          ← raw data (48 JSONL samples)
    ↓  prepare_dataset.py
dataset-chat.jsonl           ← chat-template format
    ↓  finetune.py (QLoRA, 4-bit)
coffee-lora/                 ← LoRA adapter weights
    ↓  merge_and_export.py
coffee-merged/               ← full FP16 model
    ↓  llama.cpp convert + quantise
coffee-gguf/coffee-Q4_K_M.gguf  ← ~350 MB quantised model
    ↓  Dockerfile
Docker image (ARM64)         ← runs on Raspberry Pi
```

### Requirements

| Step | Hardware | Notes |
|------|----------|-------|
| Training | GPU (8 GB+ VRAM) | QLoRA keeps VRAM low; an RTX 3060 or Colab T4 works |
| Export | CPU (8 GB+ RAM) | Merging loads FP16 model into RAM |
| Inference | Raspberry Pi 4/5 (4 GB+) | Q4_K_M needs ~350 MB RAM |

---

## Step 1 – Prepare Dataset

```bash
pip install -r requirements-train.txt
python prepare_dataset.py
```

Converts `dataset-coffee.json` → `dataset-chat.jsonl` with system/user/assistant
messages in Qwen2.5 chat format.

## Step 2 – Fine-Tune (QLoRA)

```bash
python finetune.py
```

Key defaults (override with flags):
- **Epochs:** 8 (high for 48 samples to learn the style)
- **LoRA rank:** 16, alpha: 32
- **Batch:** 2 × 4 gradient accumulation = effective batch 8
- **Max sequence length:** 512

Output: `coffee-lora/` directory with adapter weights.

### Optional: adjust hyperparameters

```bash
python finetune.py --epochs 12 --lr 1e-4 --lora_r 32
```

## Step 3 – Merge & Export to GGUF

```bash
# Clone llama.cpp (needed for conversion)
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp && cmake -B build && cmake --build build --config Release && cd ..

# Install conversion dependencies
pip install -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt

# Merge LoRA + quantise
python merge_and_export.py
```

Output: `coffee-gguf/coffee-Q4_K_M.gguf` (~350 MB)

### Quantisation options

| Type | Size | Speed | Quality |
|------|------|-------|---------|
| Q4_K_M | ~350 MB | Fastest | Good (recommended for Pi) |
| Q5_K_M | ~420 MB | Fast | Better |
| Q8_0 | ~530 MB | Moderate | Best |

```bash
python merge_and_export.py --quant Q5_K_M
```

## Step 4 – Test Locally (Optional)

```bash
pip install llama-cpp-python
python server.py --model coffee-gguf/coffee-Q4_K_M.gguf
```

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00"}'
```

### TTS mode

Add `"tts": true` to optimise the response for text-to-speech output
(expands times to spoken form, strips quotes and parentheses):

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00", "tts": true}'
```

## Step 5 – Build & Run on Raspberry Pi

### Option A: Build on the Pi directly

```bash
# Copy the project to your Pi, then:
docker build --platform linux/arm64 -t coffee-llm .
docker run -d -p 8000:8000 --name coffee coffee-llm
```

### Option B: Cross-build with buildx (from your dev machine)

```bash
docker buildx build --platform linux/arm64 -t coffee-llm --load .
docker save coffee-llm | ssh pi@raspberrypi 'docker load'
ssh pi@raspberrypi 'docker run -d -p 8000:8000 --name coffee coffee-llm'
```

### Query the running model

```bash
curl -s http://raspberrypi:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Latte at 2026-04-01T09:00:00"}' | jq .
```

Response:
```json
{
  "response": "...",
  "tokens": 87,
  "elapsed_s": 4.2,
  "tokens_per_s": 20.7
}
```

---

## Performance Tuning for Raspberry Pi

The server and Dockerfile defaults are already tuned. Override via environment
variables in Docker:

```bash
docker run -d -p 8000:8000 \
  -e THREADS=4 \
  -e CTX=512 \
  -e BATCH=64 \
  coffee-llm
```

| Parameter | Pi 4 (4 GB) | Pi 5 (8 GB) | Effect |
|-----------|-------------|-------------|--------|
| `THREADS` | 4 | 4 | Match physical core count |
| `CTX` | 384 | 512 | Smaller = faster first-token |
| `BATCH` | 32 | 64 | Smaller = less RAM, slightly slower |

### Expected performance (Q4_K_M)

| Device | Tokens/sec | Time-to-first-token |
|--------|-----------|---------------------|
| Pi 4 (4 GB) | ~8-12 t/s | ~1.5s |
| Pi 5 (8 GB) | ~18-25 t/s | ~0.6s |

### Why GGUF + llama.cpp?

- **ARM NEON SIMD** – llama.cpp auto-vectorises for ARM, ~3-5x vs naive
- **Q4_K_M** – best quality-per-bit for small models; only ~350 MB
- **Zero Python overhead** – C++ inference kernel, Python is just the HTTP wrapper
- **No GPU needed** – runs entirely on CPU

## Project Files

| File | Purpose |
|------|---------|
| `dataset-coffee.json` | Raw training data (48 samples) |
| `prepare_dataset.py` | Converts to chat-template JSONL |
| `finetune.py` | QLoRA fine-tuning with SFTTrainer |
| `merge_and_export.py` | Merge LoRA → GGUF with quantisation |
| `server.py` | Lightweight HTTP inference server |
| `Dockerfile` | Multi-stage ARM64 container |
| `requirements-train.txt` | Training dependencies (GPU machine) |
| `requirements-serve.txt` | Serving dependencies (Pi) |

## Development

```bash
cd services/llm
pip install -r requirements-serve.txt
python server.py --model coffee-gguf/coffee-Q4_K_M.gguf --port 8002
```

## Dependencies

**Serving:** `fastapi`, `uvicorn`, `llama-cpp-python`

**Training:** `transformers`, `peft`, `trl`, `bitsandbytes`, `datasets`, `torch`
