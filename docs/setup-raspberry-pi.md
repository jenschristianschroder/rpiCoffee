# Setup on Raspberry Pi

Step-by-step guide for installing, configuring, and running rpiCoffee on a Raspberry Pi.

> **Back to [project overview](../README.md)** · See also [Local Development](local-development.md)

## Prerequisites

| Component | Purpose | Required? |
|-----------|---------|-----------|
| Raspberry Pi 5 (4–8 GB) | Main compute platform | Yes (Pi 4 also works) |
| PicoQuake USB sensor | 6-axis IMU vibration sensing | No — mock mode replays CSV samples |
| Hailo AI HAT+ 2 | NPU-accelerated LLM inference | No — llama-cpp CPU fallback |
| USB speaker (e.g. Jabra) | Play TTS audio | Recommended |
| Touchscreen display | Kiosk UI with virtual keyboard | Optional |

**Software:**

- Raspberry Pi OS (64-bit, Bookworm or Trixie recommended)
- Internet connection (for initial setup only — runs offline after)
- At least **5 GB** of free disk space

## Quick Start

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

## What `setup.sh` Does

The installer runs 8 phases interactively. It takes roughly 30–45 minutes, dominated by Docker image builds.

| Phase | Description | Details |
|-------|-------------|---------|
| **0** | Pre-flight checks | Validates ARM64 architecture, detects Pi model, checks ≥5 GB disk space, tests internet |
| **1** | System dependencies | Installs Docker, Python 3, Chromium, build tools; adds user to `docker` and `dialout` groups |
| **2** | Environment configuration | Generates `.env` from `.env.example`, creates random `SECRET_KEY`, prompts for admin password, sensor mode, service toggles, optional Dataverse credentials |
| **3** | Python virtual environment | Creates `.venv`, installs app dependencies via pip |
| **4** | Model downloads | LLM GGUF model (~350 MB), TTS voice (~100 MB); optional `hailo-ollama` model pull |
| **5** | Docker image builds | Builds images for enabled services (classifier, LLM, TTS, remote-save). The LLM image can take 15–30 min on ARM64 |
| **6** | USB device setup | Installs udev rules for PicoQuake sensor, disables USB autosuspend to prevent connection drops |
| **7** | Systemd auto-start (optional) | Creates systemd units for Docker services, app, hailo-ollama, and Chromium kiosk mode |
| **8** | Data directory bootstrap | Creates `data/` directories and copies seed CSV samples |

> **Tip:** If setup fails at a particular phase, fix the issue and re-run `./setup.sh` — it is idempotent and will skip already-completed steps.

## Management Scripts

| Script | Description |
|--------|-------------|
| `start.sh` | Starts Docker services (by profile), waits for health checks, launches app natively |
| `stop.sh` | Stops app (uvicorn) + Docker services + hailo-ollama |
| `status.sh` | Full status dashboard with health checks (supports `--json` for machine-readable output) |

### Starting services

```bash
./start.sh
```

`start.sh` reads your `.env` to determine which services are enabled, starts only those Docker profiles, waits for each service's `/health` endpoint to respond, then launches the main app via uvicorn.

### Stopping services

```bash
./stop.sh
```

### Checking status

```bash
# Pretty table output (default)
./status.sh

# Machine-readable JSON
./status.sh --json
```

`status.sh` checks all service health endpoints, Docker container states, systemd unit status, and USB sensor connectivity.

## Configuration

rpiCoffee uses a three-layer configuration system (highest priority last):

1. **Hardcoded defaults** in `app/config.py`
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

> **Security:** Change the default `SECRET_KEY` and `ADMIN_PASSWORD` before deploying to a network-accessible Pi. Never commit secrets to version control — use `.env` (gitignored).

## Admin Panel

The web-based admin panel at `/admin` provides:

- **Service configuration** — enable/disable services, change endpoints, LLM parameters
- **Sensor settings** — mode, thresholds, trigger sources, warmup/cooldown
- **Training data management** — view, delete, and promote training CSV files
- **Model management** — trigger training, upload models, view model info
- **System message editor** — customize the LLM personality
- **Password management** — change the admin PIN

Access is protected by a PIN (default: `1234`). Sessions expire after 10 minutes of inactivity.

## USB Sensor Setup

When using `SENSOR_MODE=picoquake`, the PicoQuake USB sensor needs udev rules for non-root access. These are installed automatically by `setup.sh` (phase 6), but you can set them up manually:

```bash
# Create udev rule for PicoQuake USB access
echo 'SUBSYSTEM=="tty", ATTRS{idProduct}=="000a", ATTRS{idVendor}=="2e8a", MODE="0666", SYMLINK+="picoquake"' \
  | sudo tee /etc/udev/rules.d/99-picoquake.rules

# Disable USB autosuspend (prevents connection drops)
echo 'ACTION=="add", SUBSYSTEM=="usb", ATTRS{idProduct}=="000a", ATTRS{idVendor}=="2e8a", TEST=="power/control", ATTR{power/control}="on"' \
  | sudo tee /etc/udev/rules.d/99-picoquake-power.rules

# Reload rules
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Ensure your user is in the `dialout` group:

```bash
sudo usermod -aG dialout $USER
# Log out and back in for the change to take effect
```

## Systemd Auto-Start

`setup.sh` (phase 7) can create systemd units so that rpiCoffee starts automatically on boot. The following units are created:

| Unit | Type | Description |
|------|------|-------------|
| `rpicoffee-services` | oneshot | Starts Docker Compose services with enabled profiles |
| `rpicoffee-app` | simple | Runs the FastAPI app via uvicorn (depends on services) |
| `rpicoffee-kiosk` | simple | Launches Chromium in kiosk mode (depends on app) |
| `rpicoffee-hailo-ollama` | simple | Runs hailo-ollama (only when LLM backend = ollama) |

### Manual management

```bash
# Check status
sudo systemctl status rpicoffee-app

# Restart the app
sudo systemctl restart rpicoffee-app

# View logs
journalctl -u rpicoffee-app -f

# Disable auto-start
sudo systemctl disable rpicoffee-app rpicoffee-services rpicoffee-kiosk
```

## Hailo AI HAT+ Setup (Optional)

The Hailo AI HAT+ 2 provides NPU-accelerated LLM inference via `hailo-ollama` as an alternative to the CPU-based llama-cpp server.

### Installation

1. Download the `hailo_gen_ai_model_zoo` `.deb` package from [hailo.ai/developer-zone](https://hailo.ai/developer-zone/)
2. Place the `.deb` file in the rpiCoffee project root
3. Run `setup.sh` — it will detect and install the package automatically

Or install manually:

```bash
sudo apt install ./hailo_gen_ai_model_zoo_*.deb
```

### Configuration

Set these in your `.env`:

```bash
LLM_BACKEND=ollama
LLM_OLLAMA_ENDPOINT=http://localhost:8000
LLM_MODEL=qwen2:1.5b
```

When using the Ollama backend, the LLM Docker container is **not** built or started — inference runs directly on the Hailo NPU via `hailo-ollama`.

## Docker Compose Profiles

Backend services are opt-in via Docker Compose profiles. Only enabled services are started:

```bash
# Start specific services
docker compose --profile classifier --profile llm --profile tts up -d

# Available profiles: classifier, llm, tts, remote-save
```

All containers share the `coffee-net` bridge network and use an `unless-stopped` restart policy.

## Troubleshooting

### Docker build fails on Pi 4 (out of memory)

The LLM Docker image build compiles llama.cpp from source and can exceed available memory on a Pi 4 (4 GB). Try:

```bash
# Reduce parallel make jobs
export DOCKER_BUILDKIT=1
docker compose --profile llm build --build-arg CMAKE_BUILD_PARALLEL_LEVEL=1
```

### PicoQuake sensor not detected

1. Check the USB connection: `ls /dev/ttyACM*`
2. Verify udev rules are installed: `cat /etc/udev/rules.d/99-picoquake.rules`
3. Ensure your user is in the `dialout` group: `groups $USER`
4. Try unplugging and reconnecting the sensor

### Service fails health check on startup

`start.sh` waits up to 120 seconds (60 × 2s) for each service. If a service consistently fails:

```bash
# Check Docker container logs
docker compose logs classifier
docker compose logs llm

# Check if the container is running
docker compose ps
```

### TTS has no audio output

1. Verify the speaker is connected: `aplay -l`
2. Test audio output: `speaker-test -t wav -c 2`
3. Check PipeWire is running: `systemctl --user status pipewire`

### App starts but kiosk doesn't open

The kiosk unit waits for the X display and PipeWire audio before launching Chromium. Check:

```bash
journalctl -u rpicoffee-kiosk -f
```

Ensure you are running a desktop environment (Raspberry Pi OS with Desktop, not Lite).
