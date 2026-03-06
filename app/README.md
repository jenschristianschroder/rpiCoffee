# rpiCoffee — Main Application

FastAPI application that orchestrates the brew pipeline, serves the kiosk UI and admin panel, and manages the sensor lifecycle. Runs as a Docker container alongside the backend services.

## Overview

The main app is the central coordinator. It:

- Reads vibration data from the sensor (or mock data)
- Sends it through a 5-stage pipeline: **sensor → classifier → LLM → TTS → remote save**
- Serves a kiosk web UI that displays results and plays audio
- Provides an admin panel for runtime configuration
- Runs an auto-trigger background loop that detects vibrations and runs the pipeline automatically
- Includes a sensor watchdog that auto-restarts dead sensor connections (rate-limited to 5 restarts per 5 minutes)

## API Reference

### Kiosk & Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Kiosk display (HTML) |
| `GET` | `/health` | Health check (`{"status": "ok"}`) |

### Brew Pipeline

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/brew` | Run full pipeline (sensor → classifier → LLM → TTS) |
| `GET` | `/api/brew/stream` | Stream pipeline execution as SSE events |
| `GET` | `/api/test/stream` | Test run: replay a random CSV through the pipeline (no save) |
| `GET` | `/api/auto-trigger/stream` | SSE stream for auto-triggered brew results (kiosk subscribes here) |

### Sensor

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/sensor/stream` | SSE endpoint streaming continuous live sensor data |
| `POST` | `/api/sensor/restart` | Restart the sensor acquisition process |

### Service Status

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/services/status` | Health check of all backend services + sensor |

### Data Collection

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/collect/start` | Enable data collection mode (body: `{"label": "espresso"}`) |
| `POST` | `/api/collect/stop` | Disable data collection mode |

### Training Data

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/training-data` | List all training CSV files grouped by label |
| `GET` | `/api/training-data/{label}/{filename}/download` | Download a specific training CSV file |
| `POST` | `/api/training-data/{label}/upload` | Upload a training CSV file for the given label (multipart `file`) |
| `DELETE` | `/api/training-data/{label}/{filename}` | Delete a specific training file |
| `DELETE` | `/api/training-data/{label}` | Delete all training data for a label |
| `DELETE` | `/api/training-data` | Delete all training data |

### Sample Files

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/data-files` | List sample CSV files in `/data/` |
| `GET` | `/api/data-files/{filename}/download` | Download a specific sample CSV file |
| `POST` | `/api/data-files/upload` | Upload a sample `.csv.sample` file (multipart `file`) |
| `DELETE` | `/api/data-files/{filename}` | Delete a sample CSV file |
| `POST` | `/api/data-files/promote` | Promote a training CSV to a sample file (body: `{"label": "...", "filename": "..."}`) |

### Model Training (proxied to classifier)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/train` | Trigger model training on the classifier service |
| `GET` | `/api/train/status` | Get training progress |
| `POST` | `/api/upload-model` | Upload a `.joblib` model file |
| `GET` | `/api/model/info` | Get current model metadata |

### Admin Panel

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/admin/login` | Login page |
| `POST` | `/admin/login` | Submit PIN |
| `GET` | `/admin/` | Dashboard (requires fresh session token) |
| `POST` | `/admin/settings` | Save configuration changes |
| `GET` | `/admin/sensor/config` | Get sensor configuration (JSON) |

## Pipeline

The brew pipeline (`pipeline.py`) executes five stages sequentially. Each stage is independently skippable — if a service is disabled or unreachable, the pipeline reports what succeeded and what was skipped.

```
Sensor data → Classifier → LLM → TTS → Remote Save
                 ↓            ↓      ↓        ↓
             coffee type   witty    WAV     Dataverse
             + confidence  text     audio   record
```

**Data collection mode** bypasses the classifier → LLM → TTS pipeline entirely, instead saving the raw sensor recording as a labelled CSV file in `/data/training/<label>/`.

## Sensor Modes

| Mode | Description |
|------|-------------|
| `mock` | Replays sample CSV files from `/data/*.csv.sample`. On Linux uses PTY pairs; on Windows uses in-memory replay. No hardware needed. |
| `picoquake` | Reads a PicoQuake USB IMU sensor via a shared-memory ring buffer. Spawns a subprocess (`picoquake_acq.py`) for acquisition. Supports auto-trigger, live streaming, warmup/cooldown. |
| `serial` | Reads raw serial port data (e.g. `/dev/ttyUSB0` or `COM3`). |

### Auto-Trigger

When `SENSOR_AUTO_TRIGGER` is enabled in `picoquake` mode, a background loop polls the sensor's recording flag:

1. Vibration exceeds the RMS threshold → recording starts (flag 0→1)
2. Sensor data streams live to the kiosk chart via SSE
3. After `SENSOR_DURATION_S` seconds, recording completes (flag 1→2)
4. Pipeline runs automatically with the captured data
5. Sensor re-arms after `SENSOR_COOLDOWN_S` seconds

Trigger sources can be `accel`, `gyro`, or `both` (with `or`/`and` combine mode).

## Configuration

All settings are managed by `config.py` using a three-layer priority system:

1. **Defaults** → hardcoded in `_DEFAULTS`
2. **`.env` file** → overrides defaults
3. **`data/settings.json`** → persisted by the admin panel, highest priority

See the [root README](../README.md#configuration) for the full environment variable reference.

### Secrets (env-only, never persisted to settings.json)

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `change-me-to-a-random-string` | Session cookie signing key |
| `ADMIN_PASSWORD` | `1234` | Initial admin password (auto-hashed to `ADMIN_PASSWORD_HASH` on first run) |

## Admin Panel

The admin panel at `/admin` provides a web interface for all runtime configuration.

**Authentication:**
- PIN-based login using bcrypt-hashed password
- Session cookies signed with `itsdangerous` URLSafeSerializer
- Dashboard access requires a fresh token (< 10 seconds old), enforcing PIN entry on every navigation
- AJAX/POST endpoints accept any valid session token
- Session cookie expires after 10 minutes

**Dashboard capabilities:**
- Edit all service, sensor, LLM, data collection, and UI settings
- View and manage training data files
- Upload classifier models and trigger training
- Change the admin PIN
- View live sensor data chart
- Check service health status

Settings changes that affect the sensor (mode, device ID, thresholds, etc.) automatically restart the sensor. Switching the LLM backend between `llama-cpp` and `ollama` starts/stops the hailo-ollama service.

## Running Locally

The app runs as part of the Docker Compose stack. From the repository root:

```bash
# Start the app (and all enabled backend services)
docker compose --profile classifier --profile llm --profile tts up -d

# View app logs
docker compose logs -f app

# Rebuild after code changes
docker compose up -d --build app
```

The app defaults to `SENSOR_MODE=mock`, which replays sample CSV files. See the [root README](../README.md) for full configuration options.

## Dependencies

See [requirements.txt](requirements.txt):

- `fastapi`, `uvicorn` — web framework + ASGI server
- `httpx` — async HTTP client for service communication
- `picoquake` — PicoQuake sensor library
- `pyserial` — serial port access
- `bcrypt`, `itsdangerous` — admin authentication
- `jinja2` — HTML templates
- `numpy` — sensor data processing
- `python-dotenv` — `.env` file loading
