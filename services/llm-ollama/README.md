# rpiCoffee — LLM Ollama Service

Ollama proxy service for Hailo AI HAT+ 2 / hailo-ollama. Forwards text generation requests to an Ollama-compatible API, applies post-processing, and returns normalised responses matching the same contract as the llama-cpp LLM service.

## Overview

This service acts as a thin proxy between the main rpiCoffee app and an upstream Ollama server. It exists so the app client can use the same `/generate` API contract regardless of which LLM backend is active, and so all post-processing (time correction, brand stripping, TTS optimisation) happens server-side rather than in the app.

Typical deployment: the upstream Ollama server runs as a native systemd service (`hailo-ollama`) on the Raspberry Pi, leveraging the Hailo AI HAT+ 2 NPU for hardware-accelerated inference. This service runs in Docker and proxies requests to it.

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (includes upstream Ollama connectivity) |
| `POST` | `/generate` | Generate a coffee commentary via Ollama |
| `GET` | `/settings` | Get configurable settings with current values |
| `PATCH` | `/settings` | Update settings (persisted to volume) |

### Health

```
GET /health
```

Returns service status and upstream Ollama connectivity:

```json
{
  "status": "ok",
  "ollama": "connected",
  "models": ["qwen2:1.5b"]
}
```

### Generate

```
POST /generate
Content-Type: application/json
```

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | *(required)* | Coffee prompt with ISO timestamp |
| `system` | string | *(from settings)* | Override system prompt |
| `max_tokens` | int | `256` | Maximum tokens to generate |
| `temperature` | float | `0.7` | Sampling temperature |
| `top_p` | float | `0.9` | Nucleus sampling threshold |
| `tts` | bool | `true` | Optimise output for text-to-speech |

**Response:**

```json
{
  "response": "8 o'clock espresso is survival juice for people who question their career choices daily.",
  "tokens": 42,
  "elapsed_s": 1.5,
  "tokens_per_s": 28.0
}
```

**Example:**

```bash
curl -X POST http://localhost:8003/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Espresso at 2026-03-01T08:00:00"}'
```

### Settings

```
GET /settings
```

Returns all configurable settings with current values.

```
PATCH /settings
Content-Type: application/json
```

```json
{ "settings": { "OLLAMA_MODEL": "llama3:8b", "LLM_TEMPERATURE": 0.9 } }
```

Settings are persisted to `/data/settings.json` inside the container volume.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_ENDPOINT` | `http://localhost:8000` | URL of the upstream Ollama API |
| `OLLAMA_MODEL` | `qwen2:1.5b` | Ollama model name |
| `OLLAMA_KEEP_ALIVE` | `-1` | Keep model loaded: -1=forever, 0=unload, or seconds |
| `LLM_MAX_TOKENS` | `256` | Maximum tokens per generation |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature (0.0–2.0) |
| `LLM_TOP_P` | `0.9` | Nucleus sampling threshold (0.0–1.0) |
| `LLM_TTS` | `true` | Optimise output for text-to-speech |
| `LLM_SYSTEM_MESSAGE` | *(coffee commentator)* | System prompt controlling tone/style |
| `SETTINGS_DIR` | `/data` | Directory for persisted settings |

## Docker

### Build

```bash
docker build -t rpicoffee-llm-ollama ./services/llm-ollama
```

### Run

```bash
docker run -d -p 8003:8003 \
  -e OLLAMA_ENDPOINT=http://host.docker.internal:8000 \
  rpicoffee-llm-ollama
```

### Docker Compose

Managed by `docker-compose.yml` under the `llm-ollama` profile:

```bash
docker compose --profile llm-ollama up -d
```

> **Note:** The upstream Ollama server (hailo-ollama) runs as a native systemd service on the host, not in Docker. The `OLLAMA_ENDPOINT` must point to the host from inside the container (e.g. `http://host.docker.internal:8000` on Docker Desktop, or the host's IP on Linux).
