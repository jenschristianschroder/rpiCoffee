# Local Development

Guide for setting up a development environment, running rpiCoffee without hardware, and contributing to the project.

> **Back to [project overview](../README.md)** · See also [Setup on Raspberry Pi](setup-raspberry-pi.md)

## Prerequisites

- **Python 3.11+**
- **Docker Desktop** (for backend services)
- **Git**

No Raspberry Pi, sensor, or speaker hardware is required — the mock sensor replays sample CSV files and TTS can be skipped on Windows.

## Getting Started

```bash
# 1. Clone the repository
git clone https://github.com/jenschristianschroder/rpiCoffee.git
cd rpiCoffee

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 3. Install dependencies
pip install -r app\requirements.txt
pip install -r services\classifier\requirements.txt
pip install -r services\llm\requirements-serve.txt
```

## Running the Application

### Option A: Docker backends + native app (recommended)

Run the backend services in Docker and the main app natively. This is the closest to the production architecture and gives you hot-reload on the app:

```bash
# Start backend services
docker compose --profile classifier --profile llm --profile tts up -d

# Run the app on the host with hot-reload
run-app-local.bat
```

The app runs at **http://localhost:8080** with `SENSOR_MODE=mock` by default.

`run-app-local.bat` points the app at Docker service endpoints on `localhost`, sets up the data directory, and launches uvicorn with auto-reload.

### Option B: Everything natively (no Docker)

If you don't want to use Docker at all, you can run each service in a separate terminal:

**Terminal 1 — Classifier:**

```bash
cd services\classifier
uvicorn main:app --host 0.0.0.0 --port 8001
```

**Terminal 2 — LLM server:**

```bash
cd services\llm
python server.py --model coffee-gguf/coffee-Q4_K_M.gguf --port 8002
```

**Terminal 3 — Main app:**

```bash
cd app
set SENSOR_MODE=mock
set CLASSIFIER_ENDPOINT=http://localhost:8001
set LLM_ENDPOINT=http://localhost:8002
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

> **Note:** TTS requires Piper, which only runs on Linux. On Windows, set `TTS_ENABLED=false` or leave TTS out — the pipeline will skip the speech stage gracefully.

## Configuration for Local Dev

The mock sensor mode is the default, so no `.env` file is strictly required for basic local development. Key settings:

| Variable | Default | Notes |
|----------|---------|-------|
| `SENSOR_MODE` | `mock` | Replays CSV samples from `data/` |
| `CLASSIFIER_ENDPOINT` | `http://classifier:8001` | Override to `http://localhost:8001` when running natively (done automatically by `run-app-local.bat`) |
| `LLM_ENDPOINT` | `http://llm:8002` | Override to `http://localhost:8002` when running natively (done automatically by `run-app-local.bat`) |
| `LLM_OLLAMA_SERVICE_ENDPOINT` | `http://llm-ollama:8003` | Override to `http://localhost:8003` when running natively |
| `TTS_ENABLED` | `true` | Set to `false` on Windows |

For the full list of environment variables, see the [Configuration](setup-raspberry-pi.md#configuration) section in the Raspberry Pi guide.

## Sensor Modes

Three sensor modes are supported, selected via the `SENSOR_MODE` environment variable:

| Mode | Class | Use case |
|------|-------|---------|
| `mock` | `MockReader` | Local development — replays sample CSVs from `data/` |
| `picoquake` | `PicoQuakeReader` | Real PicoQuake USB IMU on Raspberry Pi |
| `serial` | `SerialReader` | Generic serial IMU |

Always code against the shared sensor interface in `app/sensor/reader.py`, not concrete reader classes.

## Testing Locally

### Triggering a brew

With the app running in mock mode:

1. Open **http://localhost:8080** in your browser (kiosk UI)
2. Click the **Brew** button, or
3. Send an API request:

```bash
curl -X POST http://localhost:8080/api/brew
```

The mock sensor replays a sample CSV file, the classifier identifies the coffee type, and the LLM generates a witty comment. On Windows (without TTS), the audio step is skipped.

### Streaming pipeline events

```bash
curl http://localhost:8080/api/brew/stream
```

Returns Server-Sent Events (SSE) for each pipeline stage as it completes.

### Health checks

```bash
curl http://localhost:8080/health           # Main app
curl http://localhost:8001/health           # Classifier
curl http://localhost:8002/health           # LLM
```

## Architecture Patterns

### Configuration system

rpiCoffee uses a three-layer config system (highest priority last):

1. Hardcoded defaults in `app/config.py` (`_DEFAULTS` dict)
2. `.env` file values (loaded via `python-dotenv`)
3. `data/settings.json` — runtime overrides persisted by the admin panel

Always read settings via the `config` object:

```python
from config import config

value = config.get("SENSOR_DURATION_S")        # typed read
config.set("LLM_TEMPERATURE", 0.8)             # runtime update (persisted)
```

### Service clients

Each backend service has a thin async HTTP client in `app/services/`. Follow this pattern when adding a new service:

```python
import httpx

class MyServiceClient:
    def __init__(self, endpoint: str, enabled: bool) -> None:
        self.endpoint = endpoint
        self.enabled = enabled

    async def call(self, payload: dict) -> dict:
        if not self.enabled:
            return {}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.endpoint}/route", json=payload)
            response.raise_for_status()
            return response.json()
```

### Sensor abstraction

Always code against the shared sensor interface, not concrete reader classes. See `app/sensor/reader.py` for the base class.

## Adding a New Backend Service

1. Create `services/<name>/` with `app.py`, `requirements.txt`, `Dockerfile`, `.env.example`, and `README.md`
2. Add a Docker Compose service entry in `docker-compose.yml` with an appropriate profile
3. Add a corresponding client in `app/services/<name>_client.py`
4. Register the client in `app/main.py` and wire it into `app/pipeline.py` if it is a pipeline stage
5. Expose configuration keys with defaults in `app/config.py` (`_DEFAULTS`)

## Code Conventions

- Use `from __future__ import annotations` at the top of every module
- Use **full type hints** on all function signatures and class attributes
- Prefer `async def` / `await` for I/O-bound operations (HTTP calls, file I/O, sensor reads)
- Use `pathlib.Path` instead of bare `os.path` string manipulation
- Use `logging.getLogger(__name__)` for per-module loggers; never `print()` for diagnostic output
- Configuration values must be read through the `config` singleton, not accessed directly from `os.environ`

## Pull Request Guidelines

- Keep PRs focused on a single concern
- Include a brief description of **why** the change is needed and **what** was changed
- Update the relevant README (root or service-level) if behaviour or configuration changes
- Test locally in `mock` sensor mode before submitting
- For new pipeline stages or sensor modes, add a corresponding mock/stub so the feature works without hardware
