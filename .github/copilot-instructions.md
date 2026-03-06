# GitHub Copilot Instructions for rpiCoffee

## Project Overview

rpiCoffee is a Raspberry Pi IoT kiosk that attaches a vibration sensor to a coffee machine, classifies the drink being brewed using machine learning, generates a witty one-liner with a fine-tuned LLM, and speaks it aloud — all running locally with no cloud dependency.

**Brew pipeline stages:**
1. **Sense** — PicoQuake USB IMU captures 30s of 6-axis vibration data
2. **Classify** — scikit-learn RandomForest identifies the coffee type
3. **Comment** — Fine-tuned Qwen2.5-0.5B LLM generates a witty remark
4. **Speak** — Piper TTS synthesizes speech and plays it through a speaker
5. **Save** *(optional)* — Results persisted to Microsoft Dataverse

## Repository Structure

```
rpiCoffee/
├── app/                        # Main FastAPI application (runs natively on host)
│   ├── main.py                 # Entry point, API routes, auto-trigger loop
│   ├── pipeline.py             # 5-stage brew pipeline orchestrator
│   ├── config.py               # Layered configuration manager
│   ├── admin/                  # Admin panel (routes + Jinja2 templates)
│   ├── sensor/                 # Sensor abstraction (mock, picoquake, serial)
│   └── services/               # HTTP clients for backend microservices
├── docs/                       # Documentation
│   ├── setup-raspberry-pi.md   # Raspberry Pi installation & operations guide
│   └── local-development.md    # Local development & contribution guide
├── services/
│   ├── classifier/             # ML coffee classifier (Docker, scikit-learn :8001)
│   ├── llm/                    # Fine-tuned LLM inference server (Docker, llama-cpp :8002)
│   ├── tts/                    # Piper TTS server (Docker, :5050)
│   └── remote-save/            # Microsoft Dataverse upload service (Docker, :7000)
├── data/                       # Sample CSVs, settings, training data, audio output
├── docker-compose.yml          # Profile-gated backend service definitions
└── setup.sh                    # Interactive Raspberry Pi installer
```

## Tech Stack

- **Runtime**: Python 3.11+, FastAPI, Uvicorn (async ASGI)
- **ML**: scikit-learn (RandomForest classifier), llama-cpp-python (GGUF inference)
- **TTS**: Piper (offline speech synthesis)
- **Sensor**: PicoQuake USB IMU via the `picoquake` library or `pyserial`
- **Containerisation**: Docker + Docker Compose (profile-gated services)
- **Hardware targets**: Raspberry Pi 5 (primary), Hailo AI HAT+ 2 (optional NPU)
- **Persistence**: Microsoft Dataverse (optional), local JSON settings

## Python Code Conventions

- Use `from __future__ import annotations` at the top of every module.
- Use **full type hints** on all function signatures and class attributes.
- Prefer `async def` / `await` for I/O-bound operations (HTTP calls, file I/O, sensor reads).
- Use `pathlib.Path` instead of bare `os.path` string manipulation.
- Follow the module-level docstring style used throughout the codebase (short summary sentence, then blank line, then detail).
- Use `logging.getLogger(__name__)` for per-module loggers; never `print()` for diagnostic output.
- Configuration values must be read through the `config` singleton from `app/config.py`, not accessed directly from `os.environ`.

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

Each backend service has a thin async HTTP client in `app/services/`. Follow the existing patterns when adding a new service:

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

Three sensor modes are supported; select via `SENSOR_MODE` env var:

| Mode | Class | Use case |
|------|-------|---------|
| `mock` | `MockReader` | Local development — replays sample CSVs |
| `picoquake` | `PicoQuakeReader` | Real PicoQuake USB IMU on Pi |
| `serial` | `SerialReader` | Generic serial IMU |

Always code against the shared sensor interface, not concrete reader classes.

## Development Workflow

See the dedicated guides for full details:

- **[Local Development](../docs/local-development.md)** — Windows/macOS setup, mock sensor, testing, contribution guidelines
- **[Setup on Raspberry Pi](../docs/setup-raspberry-pi.md)** — Installation, configuration, management scripts, systemd

### Quick reference

```bash
# Local development (Windows)
python -m venv .venv && .venv\Scripts\activate
pip install -r app/requirements.txt
pip install -r services/classifier/requirements.txt
docker compose --profile classifier --profile llm up -d
run-app-local.bat
```

Set `SENSOR_MODE=mock` (the default) to replay CSV samples without hardware.

### Docker Compose profiles

Services are opt-in via `--profile`:

```bash
docker compose --profile classifier --profile llm --profile tts up -d
```

Available profiles: `classifier`, `llm`, `tts`, `remote-save`.

## Adding a New Backend Service

1. Create `services/<name>/` with `app.py`, `requirements.txt`, `Dockerfile`, `.env.example`, and `README.md`.
2. Add a Docker Compose service entry in `docker-compose.yml` with an appropriate profile.
3. Add a corresponding client in `app/services/<name>_client.py`.
4. Register the client in `app/main.py` and wire it into `app/pipeline.py` if it is a pipeline stage.
5. Expose configuration keys with defaults in `app/config.py` (`_DEFAULTS`).

## Pull Request Guidelines

- Keep PRs focused on a single concern.
- Include a brief description of **why** the change is needed and **what** was changed.
- Update the relevant `README.md` (root or service-level) if behaviour or configuration changes.
- Test locally in `mock` sensor mode before submitting.
- For new pipeline stages or sensor modes, add a corresponding mock/stub so the feature works without hardware.

## Security Notes

- Never commit secrets (API keys, Dataverse credentials, `SECRET_KEY`) — use `.env` (gitignored).
- The `.env.example` file documents all required variables without real values; keep it up to date.
- The admin panel is PIN-protected (`ADMIN_PASSWORD`). Sessions expire after 10 minutes of inactivity.
- Change the default `SECRET_KEY` and `ADMIN_PASSWORD` before deploying to a network-accessible Pi.
