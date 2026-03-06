# rpiCoffee — TTS Service

Offline text-to-speech API powered by [Piper TTS](https://github.com/rhasspy/piper). Runs in Docker on a Raspberry Pi — no cloud required.

## Overview

Receives text from the main app (typically an LLM-generated coffee commentary) and returns a WAV audio file. Uses ONNX-based inference for fast, fully offline speech synthesis.

Default voice: `en_US-lessac-medium` (~100 MB, downloaded at Docker build time).

## API Reference

### Health

```
GET /health
```

Returns server status.

### Synthesize

```
POST /synthesize
Content-Type: application/json
```

Returns a WAV audio file.

**Request body:**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | string | *(required)* | Text to speak (1–10,000 chars) |
| `voice` | string | loaded | Voice model name |
| `speed` | float | `1.0` | Speed multiplier (0.25–4.0) |

**Examples:**

```bash
# Basic synthesis
curl -X POST http://localhost:5050/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from local TTS!"}' \
  --output speech.wav

# Faster speech
curl -X POST http://localhost:5050/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Quick announcement", "speed": 1.5}' \
  --output fast.wav

# Different voice
curl -X POST http://localhost:5050/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "British accent", "voice": "en_GB-alan-medium"}' \
  --output british.wav
```

### List Voices

```
GET /voices
```

Returns available voice models.

### Interactive Docs

```
GET /docs
```

Swagger UI for API exploration.

### Settings

```
GET /settings
```

Returns configurable settings with current values.

```
PATCH /settings
Content-Type: application/json
```

**Request body:**

```json
{ "settings": { "DEFAULT_SPEED": 1.2 } }
```

Settings are persisted to `/data/settings.json` inside the container volume.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TTS_ENABLED` | `true` | Enable the TTS service (main app setting) |
| `TTS_ENDPOINT` | `http://tts:5050` | URL of the TTS service (main app setting) |

The service itself runs on port **5050** inside the container.

## Docker

### Build

```bash
docker build -t rpicoffee-tts ./services/tts
```

### Run

```bash
docker run -d -p 5050:5050 rpicoffee-tts
```

### Docker Compose

Managed by `docker-compose.yml` under the `tts` profile:

```bash
docker compose --profile tts up -d
```

### Add voices

```bash
docker exec rpicoffee-tts python scripts/download_model.py --voice en_US-amy-medium -o /app/models
docker compose restart tts
```

## Development

```bash
cd services/tts
pip install -r requirements.txt
cd app
uvicorn main:app --host 0.0.0.0 --port 5050 --reload
```

> **Note:** Piper TTS requires Linux. The TTS service is skipped when running locally on Windows.

## Dependencies

- `fastapi`, `uvicorn` — web framework
- `piper-tts` — Piper TTS engine (ONNX inference)
- `aiofiles` — async file I/O
