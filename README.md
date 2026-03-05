# rpiCoffee

A Raspberry Pi that detects your coffee and roasts you for it.

rpiCoffee is an IoT kiosk system that attaches a vibration sensor to a coffee machine, classifies the drink being brewed using machine learning, generates a witty one-liner about it with a fine-tuned LLM, and speaks it aloud — all running locally on a Raspberry Pi with no cloud dependency.

## How It Works

1. **Sense** — A PicoQuake USB IMU sensor captures 30 seconds of 6-axis vibration data (accelerometer + gyroscope) from the coffee machine
2. **Classify** — A scikit-learn RandomForest model identifies the coffee type (black, espresso, cappuccino) from 52 statistical features
3. **Comment** — A fine-tuned Qwen2.5-0.5B LLM (quantized to GGUF Q4_K_M, ~350 MB) generates a short, witty remark about the coffee and time of day
4. **Speak** — Piper TTS synthesizes the text as speech and plays it through a connected speaker
5. **Save** *(optional)* — Results and raw sensor data are persisted to Microsoft Dataverse

## Architecture

```mermaid
graph TB
    subgraph "Raspberry Pi 5"
        sensor["PicoQuake USB Sensor<br/>(6-axis IMU)"]
        subgraph "Native (host)"
            app["Main App<br/>FastAPI :8080<br/><i>Kiosk UI · Admin Panel · Pipeline</i>"]
        end
        subgraph "Docker Containers"
            classifier["Classifier<br/>scikit-learn :8001"]
            llm["LLM<br/>llama-cpp :8002"]
            tts["TTS<br/>Piper :5050"]
            remote["Remote Save<br/>Dataverse :7000"]
        end
        hailo["Hailo AI HAT+ 2<br/>ollama :8000"]
        speaker["Speaker"]
    end

    sensor -- "USB" --> app
    app -- "POST /classify" --> classifier
    app -- "POST /generate" --> llm
    app -. "POST /generate<br/>(alternative)" .-> hailo
    app -- "POST /synthesize" --> tts
    app -- "POST /save" --> remote
    tts -- "WAV audio" --> speaker
```

The main app runs **natively** on the host (not in Docker) for direct USB sensor access. Backend services run as Docker containers, each gated by a Docker Compose profile so only enabled services start.

An alternative LLM backend uses the **Hailo AI HAT+ 2** NPU accelerator via `hailo-ollama` for hardware-accelerated inference.

## Hardware

| Component | Purpose | Required? |
|-----------|---------|-----------|
| Raspberry Pi 5 (4–8 GB) | Main compute platform | Yes (Pi 4 also works) |
| PicoQuake USB sensor | 6-axis IMU vibration sensing | No — mock mode replays CSV samples |
| Hailo AI HAT+ 2 | NPU-accelerated LLM inference | No — llama-cpp CPU fallback |
| USB speaker (e.g. Jabra) | Play TTS audio | Recommended |
| Touchscreen display | Kiosk UI with virtual keyboard | Optional |

## Quick Start — Raspberry Pi

```bash
# 1. Clone the repository
git clone https://github.com/jenschristianschroder/rpiCoffee.git
cd rpiCoffee

# 2. Run the interactive installer (8 phases)
./setup.sh

# 3. Start all services
./start.sh

# 4. Open the kiosk UI
# http://<pi-ip>:8080
```

### What `setup.sh` does

| Phase | Description |
|-------|-------------|
| 0 | Pre-flight checks (architecture, disk space, internet) |
| 1 | System dependencies (Docker, Python 3, Chromium, build tools) |
| 2 | `.env` configuration + optional Dataverse credentials |
| 3 | Python virtual environment + pip install |
| 4 | Model downloads (LLM GGUF ~350 MB, TTS voice ~100 MB) |
| 5 | Docker image builds (classifier, LLM, TTS, remote-save) |
| 6 | USB device setup (udev rules for PicoQuake, autosuspend disabled) |
| 7 | Optional systemd services + Chromium kiosk mode |
| 8 | Data directory bootstrap |

### Management scripts

| Script | Description |
|--------|-------------|
| `start.sh` | Start Docker services (by profile), wait for health checks, launch app natively |
| `stop.sh` | Stop app + Docker services + hailo-ollama |
| `status.sh` | Full status dashboard with health checks (supports `--json`) |

## Quick Start — Local Development (Windows)

No hardware required — the mock sensor replays sample CSV files.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate
pip install -r app\requirements.txt
pip install -r services\classifier\requirements.txt
pip install -r services\llm\requirements-serve.txt

# 2. Start all services in separate terminal windows
run-local.bat
```

This starts the classifier, LLM server, and main app with `SENSOR_MODE=mock`. TTS is skipped on Windows (requires Piper/Linux).

**Alternative** — run only the main app natively while backend services run in Docker (useful for USB sensor testing):

```bash
# Start Docker backends
docker compose --profile classifier --profile llm --profile tts up -d

# Run the app on the host
run-app-local.bat
```

## Configuration

rpiCoffee uses a three-layer configuration system (highest priority last):

1. **Hardcoded defaults** (in `app/config.py`)
2. **`.env` file** values
3. **`data/settings.json`** — persisted at runtime via the admin panel

### Key Environment Variables

#### Services

| Variable | Default | Description |
|----------|---------|-------------|
| `CLASSIFIER_ENABLED` | `true` | Enable the ML classifier service |
| `CLASSIFIER_ENDPOINT` | `http://classifier:8001` | Classifier service URL |
| `LLM_ENABLED` | `true` | Enable the LLM text generation service |
| `LLM_BACKEND` | `llama-cpp` | `llama-cpp` for CPU or `ollama` for Hailo AI HAT+ |
| `LLM_ENDPOINT` | `http://llm:8002` | LLM service URL (llama-cpp) |
| `LLM_OLLAMA_ENDPOINT` | `http://localhost:8000` | Ollama endpoint (Hailo) |
| `TTS_ENABLED` | `true` | Enable text-to-speech |
| `TTS_ENDPOINT` | `http://tts:5050` | TTS service URL |
| `REMOTE_SAVE_ENABLED` | `true` | Enable Dataverse persistence |
| `REMOTE_SAVE_ENDPOINT` | `http://remote-save:7000` | Remote save service URL |

#### Sensor

| Variable | Default | Description |
|----------|---------|-------------|
| `SENSOR_MODE` | `mock` | `mock`, `picoquake`, or `serial` |
| `SENSOR_DEVICE_ID` | `cf79` | PicoQuake USB device ID (last 4 hex chars of serial) |
| `SENSOR_SAMPLE_RATE_HZ` | `100` | Sensor readings per second |
| `SENSOR_DURATION_S` | `30` | Seconds of data to capture per brew |
| `SENSOR_VIBRATION_THRESHOLD` | `0.15` | Accelerometer RMS threshold (g) for auto-trigger |
| `SENSOR_AUTO_TRIGGER` | `true` | Automatically start pipeline on vibration detection |
| `SENSOR_TRIGGER_SOURCES` | `accel` | Trigger source: `accel`, `gyro`, or `both` |
| `SENSOR_WARMUP_S` | `5` | Seconds to ignore triggers after sensor start |
| `SENSOR_COOLDOWN_S` | `10` | Seconds to wait between captures |

#### LLM Generation

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MAX_TOKENS` | `256` | Maximum tokens per generation |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature (0.0–2.0) |
| `LLM_TOP_P` | `0.9` | Nucleus sampling threshold (0.0–1.0) |
| `LLM_SYSTEM_MESSAGE` | *(coffee commentator prompt)* | System prompt controlling tone/style |
| `LLM_KEEP_ALIVE` | `-1` | Ollama keep_alive: -1=forever, 0=unload, or seconds |

#### Auth & UI

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `change-me-to-a-random-string` | Session signing key |
| `ADMIN_PASSWORD` | `1234` | Initial admin password (hashed on first run) |
| `VIRTUAL_KEYBOARD_ENABLED` | `false` | On-screen keyboard for touchscreen kiosk |

## Admin Panel

The web-based admin panel at `/admin` provides:

- **Service configuration** — enable/disable services, change endpoints, LLM parameters
- **Sensor settings** — mode, thresholds, trigger sources, warmup/cooldown
- **Training data management** — view, delete, and promote training CSV files
- **Model management** — trigger training, upload models, view model info
- **System message editor** — customize the LLM personality
- **Password management** — change the admin PIN

Access is protected by a PIN (default: `1234`). Sessions expire after 10 minutes of inactivity.

## Services

| Service | Port | Description | README |
|---------|------|-------------|--------|
| **Main App** | 8080 | FastAPI orchestrator, kiosk UI, admin panel, sensor management | [app/README.md](app/README.md) |
| **Classifier** | 8001 | scikit-learn RandomForest coffee type classifier | [services/classifier/README.md](services/classifier/README.md) |
| **LLM** | 8002 | Fine-tuned Qwen2.5-0.5B GGUF inference server | [services/llm/README.md](services/llm/README.md) |
| **TTS** | 5050 | Piper TTS offline speech synthesis | [services/tts/README.md](services/tts/README.md) |
| **Remote Save** | 7000 | Microsoft Dataverse persistence service | [services/remote-save/README.md](services/remote-save/README.md) |

## Project Structure

```
rpiCoffee/
├── app/                        # Main FastAPI application (runs natively)
│   ├── main.py                 # App entry point, API routes, auto-trigger loop
│   ├── pipeline.py             # 5-stage brew pipeline orchestrator
│   ├── config.py               # Layered configuration manager
│   ├── admin/                  # Admin panel (routes + Jinja2 templates)
│   ├── sensor/                 # Sensor abstraction (mock, picoquake, serial)
│   └── services/               # HTTP clients for backend services
├── services/
│   ├── classifier/             # ML coffee classifier (Docker)
│   ├── llm/                    # Fine-tuned LLM server (Docker)
│   ├── tts/                    # Piper TTS server (Docker)
│   └── remote-save/            # Dataverse upload service (Docker)
├── data/                       # Sample CSVs, settings, training data, audio
├── docker-compose.yml          # Backend service definitions (profile-gated)
├── setup.sh                    # Interactive Raspberry Pi installer
├── start.sh / stop.sh          # Service lifecycle management
├── status.sh                   # Health check dashboard
├── run-local.bat               # Windows: start all services locally
└── run-app-local.bat           # Windows: app on host + Docker backends
```

## License

This project is provided as-is for educational and personal use.
