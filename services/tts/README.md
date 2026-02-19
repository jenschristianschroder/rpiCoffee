# Local Text-to-Speech

Offline TTS API powered by [Piper TTS](https://github.com/rhasspy/piper). Runs in Docker on a Raspberry Pi — no cloud required.

## Start the server

```bash
docker compose up -d --build
```

The API runs at `http://localhost:5000`.

## Generate speech

**POST** `/synthesize` — returns a WAV audio file.

```bash
curl -X POST http://localhost:5000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from local TTS!"}' \
  --output speech.wav
```

### Request body

| Field   | Type   | Default   | Description                   |
|---------|--------|-----------|-------------------------------|
| `text`  | string | required  | Text to speak (1-10000 chars) |
| `voice` | string | loaded    | Voice model name              |
| `speed` | float  | `1.0`     | Speed multiplier (0.25 - 4.0) |

### Examples

```bash
# Faster speech
curl -X POST http://localhost:5000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Quick announcement", "speed": 1.5}' \
  --output fast.wav

# Different voice
curl -X POST http://localhost:5000/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "British accent", "voice": "en_GB-alan-medium"}' \
  --output british.wav

# Browser shortcut (GET)
# http://localhost:5000/synthesize?text=Hello+world&speed=1.2
```

## Other endpoints

| Endpoint     | Method | Description              |
|-------------|--------|--------------------------|
| `/health`   | GET    | Server status            |
| `/voices`   | GET    | List available voices    |
| `/docs`     | GET    | Interactive API docs     |

## Add voices

```bash
docker exec local-tts python scripts/download_model.py --voice en_US-amy-medium -o /app/models
docker compose restart
```
