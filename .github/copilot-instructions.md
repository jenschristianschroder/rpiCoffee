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
│   ├── llm-ollama/             # Ollama proxy service (Docker, Hailo HAT+ :8003)
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

Available profiles: `classifier`, `llm`, `llm-ollama`, `tts`, `remote-save`.

## Adding a New Backend Service

1. Create `services/<name>/` with `app.py`, `requirements.txt`, `Dockerfile`, `.env.example`, and `README.md`.
2. Add a Docker Compose service entry in `docker-compose.yml` with an appropriate profile.
3. Add a corresponding client in `app/services/<name>_client.py`.
4. Register the client in `app/main.py` and wire it into `app/pipeline.py` if it is a pipeline stage.
5. Expose configuration keys with defaults in `app/config.py` (`_DEFAULTS`).

## Test Design & Automation

### Test framework and location

Use **pytest** with **pytest-asyncio** for all automated tests. Place tests under a top-level `tests/` directory mirroring the source tree:

```
tests/
├── conftest.py               # Shared fixtures (mock config, sample sensor data, …)
├── app/
│   ├── test_config.py        # ConfigManager unit tests
│   ├── test_pipeline.py      # Pipeline orchestration tests
│   ├── sensor/
│   │   └── test_reader.py    # Sensor reader / channel-filter tests
│   └── services/
│       ├── test_classifier_client.py
│       ├── test_llm_client.py
│       └── test_tts_client.py
└── services/
    ├── classifier/
    │   └── test_classifier_app.py   # FastAPI TestClient tests
    └── remote-save/
        └── test_remote_save_app.py
```

Install test dependencies in a shared `requirements-test.txt` at the repository root:

```
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=5.0       # Coverage reporting
respx>=0.21          # Mock httpx requests
httpx>=0.27          # Required by respx
```

Run all tests from the repository root:

```bash
pytest tests/ -v
```

### Async tests

Set `asyncio_mode = "auto"` in `pyproject.toml` so every `async def test_*` function is automatically treated as an asyncio test — no `@pytest.mark.asyncio` decorator needed:

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Because `pipeline.py` lives under `app/`, add `app/` to `PYTHONPATH` (e.g. via the `PYTHONPATH=app` env var when running pytest) so tests can import it as a top-level module. Patch the full dotted path as seen from the module's own namespace:

```python
# tests/app/test_pipeline.py
from unittest.mock import AsyncMock, patch

async def test_run_pipeline_returns_error_on_empty_sensor_data():
    # "pipeline.read_sensor" is the path used inside app/pipeline.py
    with patch("pipeline.read_sensor", new=AsyncMock(return_value=[])):
        from pipeline import run_pipeline
        result = await run_pipeline()
    assert result["error"] is not None
    assert result["sensor_samples"] == 0
```

### Config isolation

The config system reads from both `.env` **and** a JSON file under `SETTINGS_DIR` (computed at import time). To keep tests isolated and avoid touching `data/settings.json`, redirect `SETTINGS_DIR` to a temp directory **before** importing `config`, then use a temporary `.env` file:

```python
# tests/conftest.py
import importlib
from pathlib import Path

import pytest

from config import ConfigManager


@pytest.fixture()
def test_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    # Point SETTINGS_DIR at a per-test temp dir so JSON settings don't
    # read/write the real data/settings.json.
    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))

    # Reload the config module so the module-level SETTINGS_PATH is
    # recomputed based on the temporary SETTINGS_DIR.
    import config as config_module
    importlib.reload(config_module)

    # Back the ConfigManager with a temp .env file for this test only.
    env_file = tmp_path / ".env"
    env_file.write_text("SENSOR_MODE=mock\nSENSOR_DURATION_S=5\n")
    return config_module.ConfigManager(env_file=env_file)
```

### Mocking HTTP service clients

Use **respx** to mock `httpx` calls without starting a real server. `ClassifierClient` uses the endpoint from `config.CLASSIFIER_ENDPOINT`, so set that env var and respx-mock the correct route:

```python
import httpx
import pytest
import respx

@pytest.mark.respx(base_url="http://classifier:8001")
async def test_classifier_client_classify(respx_mock, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENDPOINT", "http://classifier:8001")
    respx_mock.post("/classify").mock(
        return_value=httpx.Response(200, json={"label": "espresso", "confidence": 0.95})
    )
    from services.classifier_client import ClassifierClient
    result = await ClassifierClient.classify([
        {"acc_x": 0.1, "acc_y": 0.0, "acc_z": 1.0,
         "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0}
    ])
    assert result["label"] == "espresso"
```

### Mock sensor data fixture

Provide a shared fixture for realistic sensor samples so individual tests stay concise:

```python
# tests/conftest.py
@pytest.fixture()
def sample_sensor_data() -> list[dict[str, float]]:
    """30 samples of mock IMU data (~0.3 s at 100 Hz)."""
    return [
        {"acc_x": 0.01 * i, "acc_y": 0.0, "acc_z": 1.0,
         "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,
         "elapsed_s": i * 0.01}
        for i in range(30)
    ]
```

### Service (microservice) tests

Each microservice in `services/<name>/` should have its own tests using FastAPI's `TestClient`:

```python
# tests/services/classifier/test_classifier_app.py
from fastapi.testclient import TestClient
from main import app   # services/classifier/main.py

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200

def test_classify_returns_label(sample_sensor_data):
    response = client.post("/classify", json={"data": sample_sensor_data})
    assert response.status_code == 200
    assert "label" in response.json()
```

### What to test

| Area | Tests to write |
|------|----------------|
| `config.py` | Layer priority, type casting (`_cast`), `set`/`get` round-trips, password hashing |
| `sensor/reader.py` | `filter_sensor_channels` with acc/gyro disabled; mock-buffer read path |
| Service clients | Happy path, HTTP error propagation, `enabled=False` short-circuit |
| `pipeline.py` | Empty sensor data → error result; data-collect mode; stage skip on disabled service |
| Microservice apps | `/health` endpoints; primary inference endpoints (e.g., classifier `/classify`) with valid and invalid payloads |

### Coverage

Aim for **≥ 80 % line coverage** on `app/` code that can run without hardware. Exclude hardware-only paths (`picoquake_reader.py`, `picoquake_acq.py`) with `# pragma: no cover` or a `.coveragerc` omit rule.

---

## CI Workflows

All CI definitions live in `.github/workflows/`. Use GitHub Actions.

### Recommended workflow: `ci.yml`

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: ["main"]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          pip install -r app/requirements.txt
          pip install -r services/classifier/requirements.txt
          pip install -r requirements-test.txt

      - name: Run tests
        env:
          SENSOR_MODE: mock
          SETTINGS_DIR: /tmp/rpicoffee-test
          PYTHONPATH: app
        run: pytest tests/ -v --tb=short --cov=app --cov-report=html

      - name: Upload coverage report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: htmlcov/
```

### CI design principles

- **Hardware-free by default**: Set `SENSOR_MODE=mock` and `SETTINGS_DIR` to a writable temp path in all CI jobs. Tests must never require physical hardware.
- **Fail fast**: Use `--tb=short` so failures are easy to read in the Actions log.
- **Single job for unit tests**: Keep the unit-test job fast (< 2 min). Long integration tests that need Docker services can live in a separate job gated by a `needs:` dependency.
- **No secrets in tests**: Never hard-code credentials. Use `os.environ.get("VAR", "test-default")` in test fixtures.
- **Matrix builds** (optional): Add a strategy matrix for Python 3.11 / 3.12 when adding new language features.

### Adding a new CI job

1. Create a new workflow file in `.github/workflows/` or add a new `job` block in `ci.yml`.
2. Start from `ubuntu-latest` unless the job requires a specific OS.
3. Always cache pip downloads with `actions/setup-python` `cache: pip`.
4. Gate deployment or release jobs with `if: github.ref == 'refs/heads/main'` and `needs: test`.

### Service integration tests (optional, Docker-based)

For tests that need real microservices, start Docker Compose services in CI before running the test suite:

```yaml
  integration:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4

      - name: Start backend services
        run: docker compose --profile classifier up -d --wait

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          pip install -r app/requirements.txt
          pip install -r requirements-test.txt

      - name: Run integration tests
        env:
          SENSOR_MODE: mock
          CLASSIFIER_ENDPOINT: http://localhost:8001
          SETTINGS_DIR: /tmp/rpicoffee-test
        run: pytest tests/ -v -m integration

      - name: Stop services
        if: always()
        run: docker compose down
```

Mark integration tests with `@pytest.mark.integration` so they can be run separately from fast unit tests.

---

## Pull Request Guidelines

- Keep PRs focused on a single concern.
- Include a brief description of **why** the change is needed and **what** was changed.
- Update the relevant `README.md` (root or service-level) if behaviour or configuration changes.
- **Add or update tests** for any changed logic — new pipeline stages, service clients, config keys, and sensor paths all require corresponding test coverage.
- All CI checks must pass before merging.
- Test locally in `mock` sensor mode before submitting (`pytest tests/ -v`).
- For new pipeline stages or sensor modes, add a corresponding mock/stub so the feature works without hardware.

## Security Notes

- Never commit secrets (API keys, Dataverse credentials, `SECRET_KEY`) — use `.env` (gitignored).
- The `.env.example` file documents all required variables without real values; keep it up to date.
- The admin panel is PIN-protected (`ADMIN_PASSWORD`). Sessions expire after 10 minutes of inactivity.
- Change the default `SECRET_KEY` and `ADMIN_PASSWORD` before deploying to a network-accessible Pi.
