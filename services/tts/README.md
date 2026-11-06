# rpiCoffee — TTS Service

Offline text-to-speech API powered by [Piper TTS](https://github.com/OHF-Voice/piper1-gpl).
Runs in Docker on a Raspberry Pi — no cloud required.

## Overview

Receives text from the main app (typically an LLM-generated coffee commentary) and returns a WAV audio file. Uses ONNX-based inference for fast, fully offline speech synthesis.

Default voice: `en_US-lessac-medium` (~63 MB model, downloaded at Docker build time).

The engine is pinned to **Piper 1.8.0**, which provides Linux ARM64 and x86-64 wheels.
The wrapper uses `PiperVoice.synthesize_wav` and `SynthesisConfig`; speed still maps
to `length_scale = 1 / speed`. The existing voice, WAV response, port 5050, settings
volume, and pipeline contract are retained.

After merging the update, use the usual Pi update process from the repository root:

```bash
git pull
sudo reboot
```

The existing boot service rebuilds the enabled TTS container. No service
re-registration or pipeline changes are needed. Confirm the running engine with
`curl -sS http://127.0.0.1:5050/health`; it reports `"engine_version":"1.8.0"`.
Updating the engine does not replace or retrain the voice model.

## API Reference

### Health

```
GET /health
```

Returns server status, engine name and version, and the currently loaded voice.

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
docker build --target test -t rpicoffee-tts-test services/tts
docker run --rm --network none rpicoffee-tts-test
docker compose --profile tts up -d --build tts
python3 services/tts/tests/smoke_http.py
```

Tests perform real CPU synthesis with the bundled voice, validate complete,
non-silent PCM WAV files, and check speed changes and both HTTP synthesis routes.
CI runs these tests plus production-container HTTP checks on ARM64 and x86-64.

## Dependencies

- `fastapi`, `uvicorn` — web framework
- `piper-tts==1.8.0` — Piper TTS engine (ONNX inference, GPL-3.0-or-later)
- `aiofiles` — async file I/O

Voice models retain their own licenses; see each voice's model card.
