# Coffee LLM API

## Base URL

```
http://<host>:8000
```

## Endpoints

### Health Check

```
GET /health
```

Returns `{"status": "ok"}` when the server is ready.

---

### Generate

```
POST /generate
Content-Type: application/json
```

#### Request Body

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | *(required)* | Coffee prompt with ISO timestamp, e.g. `"Write a statement about Espresso at 2026-03-01T08:00:00"` |
| `max_tokens` | int | `256` | Maximum tokens to generate |
| `temperature` | float | `0.7` | Sampling temperature (higher = more creative) |
| `top_p` | float | `0.9` | Nucleus sampling threshold |
| `tts` | bool | `false` | Optimise output for text-to-speech (expands times to spoken form, strips quotes and punctuation) |

#### Response

```json
{
  "response": "8 o'clock espresso is survival juice for people who question their career choices daily.",
  "tokens": 42,
  "elapsed_s": 1.5,
  "tokens_per_s": 28.0
}
```

#### Examples

**Basic request:**

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Latte at 2026-04-01T09:00:00"}'
```

**With TTS mode:**

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Cappuccino at 2026-02-17T07:00:00", "tts": true}'
```

**Custom parameters:**

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Write a statement about Black at 2026-05-10T14:30:00", "temperature": 0.9, "max_tokens": 128}'
```

## Starting the Server

```bash
python server.py                                          # defaults
python server.py --model coffee-gguf/coffee-Q4_K_M.gguf   # specify model
python server.py --port 8080 --threads 4 --ctx 512        # custom settings
```

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | `coffee-gguf/coffee-f16.gguf` | Path to GGUF model |
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8000` | Port |
| `--ctx` | `1024` | Context window size |
| `--threads` | `4` | CPU threads |
| `--batch` | `64` | Batch size |
