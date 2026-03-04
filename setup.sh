#!/usr/bin/env bash
#
# rpiCoffee – Comprehensive Raspberry Pi 5 Setup Script
#
# Installs all dependencies, downloads models, builds Docker images,
# configures .env, sets up USB permissions, and optionally creates
# systemd services for auto-start.
#
# Architecture:
#   App runs NATIVELY (Python venv) for direct PicoQuake USB access.
#   Classifier, LLM, TTS, Remote-Save run as Docker containers.
#
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
MODEL_URL="https://github.com/jenschristianschroder/rpiCoffee/releases/download/v0.1/coffee-Q4_K_M.gguf"
MODEL_SHA256=""   # Optional: set to verify download integrity
TTS_VOICE="en_US-lessac-medium"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Ensure shell scripts are executable (safety net for ZIP/tarball installs) ─
chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/app/entrypoint.sh 2>/dev/null || true

LOG_FILE="$SCRIPT_DIR/setup.log"
ERRORS=()
WARNINGS=()

# ── Colours / formatting ────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; WARNINGS+=("$*"); }
fail() { echo -e "  ${RED}✗${NC} $*"; ERRORS+=("$*"); }
info() { echo -e "  ${CYAN}▸${NC} $*"; }
header() {
    echo ""
    echo -e "${BOLD}── $* ──${NC}"
}

# ── Helper: prompt with default ──────────────────────────────────
# Usage: prompt "Label" VARIABLE "default"
prompt() {
    local label="$1" varname="$2" default="$3"
    local input
    read -rp "  $label [${default}]: " input
    eval "$varname=\"${input:-$default}\""
}

# Usage: prompt_yn "Label" VARIABLE "y"
prompt_yn() {
    local label="$1" varname="$2" default="$3"
    local input
    while true; do
        read -rp "  $label [${default}]: " input
        input="${input:-$default}"
        case "$input" in
            [yY]) eval "$varname=true";  return ;;
            [nN]) eval "$varname=false"; return ;;
            *)    echo "    Please enter y or n." ;;
        esac
    done
}

# Usage: prompt_secret "Label" VARIABLE "default"
prompt_secret() {
    local label="$1" varname="$2" default="$3"
    local input
    read -srp "  $label [${default:+****}]: " input
    echo ""
    eval "$varname=\"${input:-$default}\""
}

# ── Helper: write key=value to .env (create or update) ──────────
env_set() {
    local file="$1" key="$2" value="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$file"
    else
        echo "${key}=${value}" >> "$file"
    fi
}

# ════════════════════════════════════════════════════════════════
#  Phase 0: Pre-flight Check & Plan Display
# ════════════════════════════════════════════════════════════════
header "Phase 0 · Pre-flight checks"

# OS / architecture
ARCH="$(uname -m)"
if [[ "$ARCH" != "aarch64" ]]; then
    warn "Expected aarch64 (ARM64), detected ${ARCH}. Some steps may fail."
fi

if [[ -f /proc/device-tree/model ]]; then
    PI_MODEL="$(tr -d '\0' < /proc/device-tree/model)"
    info "Board: $PI_MODEL"
    if [[ "$PI_MODEL" != *"Pi 5"* ]]; then
        warn "Optimised for Raspberry Pi 5; detected: ${PI_MODEL}"
    fi
else
    warn "Cannot detect Pi model (no /proc/device-tree/model)"
fi

# Disk space (need ≥ 5 GB)
AVAIL_KB=$(df --output=avail "$SCRIPT_DIR" | tail -1)
AVAIL_GB=$(( AVAIL_KB / 1048576 ))
if (( AVAIL_GB < 5 )); then
    fail "Only ${AVAIL_GB} GB free; at least 5 GB required."
    echo -e "    ${RED}Aborting.${NC}"
    exit 1
fi
ok "${AVAIL_GB} GB disk space available"

# Internet
if curl -sf --max-time 5 https://github.com > /dev/null 2>&1; then
    ok "Internet connectivity OK"
else
    fail "No internet access – cannot download packages or models."
    echo -e "    ${RED}Aborting.${NC}"
    exit 1
fi

echo ""
echo -e "${BOLD}This script will:${NC}"
echo "  1. Install system packages (Docker, Python 3, build tools)"
echo "  2. Configure .env environment files"
echo "  3. Create a Python virtual environment (.venv)"
echo "  4. Download LLM model (~350 MB) and TTS voice (~100 MB)"
echo "  5. Build Docker images for enabled services (~20-40 min)"
echo "  6. Set up USB device permissions for PicoQuake sensor"
echo "  7. Optionally create systemd services for auto-start"
echo ""
echo "  Estimated time: 30–45 minutes (dominated by LLM Docker build)"
echo ""
read -rp "  Press Enter to proceed or Ctrl+C to cancel... "

# ════════════════════════════════════════════════════════════════
#  Phase 1: System Dependencies
# ════════════════════════════════════════════════════════════════
header "Phase 1 · System dependencies"

PKGS=(
    docker.io docker-compose-plugin
    python3 python3-venv python3-pip
    git curl wget
    build-essential cmake
    libgomp1 libportaudio2
)

# Chromium: skip if already installed (Trixie ships it), else detect package name
if command -v chromium &>/dev/null || command -v chromium-browser &>/dev/null; then
    info "Chromium already installed — skipping"
elif apt-cache show chromium-browser &>/dev/null 2>&1; then
    PKGS+=(chromium-browser)
else
    PKGS+=(chromium)
fi

info "Installing: ${PKGS[*]}"
if sudo apt-get update -qq >> "$LOG_FILE" 2>&1 && \
   sudo apt-get install -y -qq "${PKGS[@]}" >> "$LOG_FILE" 2>&1; then
    ok "System packages installed"
else
    fail "Some packages failed to install — check $LOG_FILE"
fi

GROUP_CHANGED=false

# docker group
if ! groups "$USER" | grep -qw docker; then
    sudo usermod -aG docker "$USER"
    ok "Added $USER to docker group"
    GROUP_CHANGED=true
else
    ok "$USER already in docker group"
fi

# dialout group (USB serial)
if ! groups "$USER" | grep -qw dialout; then
    sudo usermod -aG dialout "$USER"
    ok "Added $USER to dialout group"
    GROUP_CHANGED=true
else
    ok "$USER already in dialout group"
fi

if $GROUP_CHANGED; then
    warn "Group membership changed – log out & back in (or reboot) for it to take effect"
fi

# hailo-ollama (installed later in Phase 7, but we detect it early)
if command -v hailo-ollama &>/dev/null; then
    ok "hailo-ollama binary found: $(command -v hailo-ollama)"
    HAILO_OLLAMA_INSTALLED=true
else
    HAILO_OLLAMA_INSTALLED=false
    # Check if the .deb is available in the project root
    HAILO_DEB=$(ls "${SCRIPT_DIR}"/hailo_gen_ai_model_zoo_*.deb 2>/dev/null | head -1 || true)
    if [[ -n "$HAILO_DEB" ]]; then
        info "Found hailo package: $(basename "$HAILO_DEB")"
        info "Installing hailo_gen_ai_model_zoo..."
        if sudo apt install -y "$HAILO_DEB" >> "$LOG_FILE" 2>&1; then
            ok "hailo_gen_ai_model_zoo installed"
            HAILO_OLLAMA_INSTALLED=true
        else
            fail "hailo_gen_ai_model_zoo install failed — check $LOG_FILE"
        fi
    else
        info "hailo-ollama not installed (needed only if LLM backend = ollama)"
        info "Download the .deb from https://hailo.ai/developer-zone/ and place it in ${SCRIPT_DIR}/"
    fi
fi

# ════════════════════════════════════════════════════════════════
#  Phase 2: Environment Configuration
# ════════════════════════════════════════════════════════════════
header "Phase 2 · Environment configuration"

CONFIGURE_ENV=true
if [[ -f .env ]]; then
    prompt_yn "A .env file already exists. Reconfigure it?" CONFIGURE_ENV "n"
fi

if [[ "$CONFIGURE_ENV" == "true" ]]; then
    # Start from the template
    cp .env.example .env
    info "Copied .env.example → .env"

    # SECRET_KEY — auto-generate
    SECRET_KEY="$(openssl rand -hex 32)"
    env_set .env SECRET_KEY "$SECRET_KEY"
    ok "Generated random SECRET_KEY"

    # Admin password
    prompt_secret "Admin password" ADMIN_PASSWORD "1234"
    env_set .env ADMIN_PASSWORD "$ADMIN_PASSWORD"

    # Sensor mode — auto-detect USB
    DEFAULT_SENSOR="mock"
    if ls /dev/ttyACM* 1>/dev/null 2>&1; then
        DEFAULT_SENSOR="picoquake"
        info "PicoQuake USB device detected"
    fi
    prompt "Sensor mode (mock / picoquake)" SENSOR_MODE "$DEFAULT_SENSOR"
    env_set .env SENSOR_MODE "$SENSOR_MODE"

    if [[ "$SENSOR_MODE" == "picoquake" ]]; then
        prompt "PicoQuake device ID (last 4 hex of serial)" SENSOR_DEVICE_ID "cf79"
        env_set .env SENSOR_DEVICE_ID "$SENSOR_DEVICE_ID"
        prompt_yn "Enable auto-trigger on vibration?" SENSOR_AUTO_TRIGGER "y"
        env_set .env SENSOR_AUTO_TRIGGER "$SENSOR_AUTO_TRIGGER"
    fi

    # Service toggles
    prompt_yn "Enable classifier service?" CLASSIFIER_ENABLED "y"
    env_set .env CLASSIFIER_ENABLED "$CLASSIFIER_ENABLED"

    prompt_yn "Enable LLM service?" LLM_ENABLED "y"
    env_set .env LLM_ENABLED "$LLM_ENABLED"

    if [[ "$LLM_ENABLED" == "true" ]]; then
        prompt "LLM backend (llama-cpp / ollama)" LLM_BACKEND "llama-cpp"
        env_set .env LLM_BACKEND "$LLM_BACKEND"
        if [[ "$LLM_BACKEND" == "ollama" ]]; then
            prompt "Ollama model name" LLM_MODEL "qwen2:1.5b"
            env_set .env LLM_MODEL "$LLM_MODEL"
            info "Using hailo-ollama — the LLM Docker container will NOT be built"
        fi
    fi

    prompt_yn "Enable TTS service?" TTS_ENABLED "y"
    env_set .env TTS_ENABLED "$TTS_ENABLED"

    prompt_yn "Enable remote-save service?" REMOTE_SAVE_ENABLED "y"
    env_set .env REMOTE_SAVE_ENABLED "$REMOTE_SAVE_ENABLED"

    # Service ports (single source of truth for docker-compose.yml, scripts, etc.)
    env_set .env APP_PORT           "8080"
    env_set .env CLASSIFIER_PORT    "8001"
    env_set .env LLM_PORT           "8002"
    env_set .env OLLAMA_PORT         "8000"
    env_set .env TTS_PORT           "5050"
    env_set .env REMOTE_SAVE_PORT   "7000"

    # Endpoints are always localhost in native mode — set automatically
    env_set .env CLASSIFIER_ENDPOINT "http://localhost:8001"
    env_set .env LLM_ENDPOINT        "http://localhost:8002"
    env_set .env LLM_OLLAMA_ENDPOINT  "http://localhost:8000"
    env_set .env TTS_ENDPOINT        "http://localhost:5050"
    env_set .env REMOTE_SAVE_ENDPOINT "http://localhost:7000"

    ok ".env configured"
else
    info "Keeping existing .env"
fi

# ── Load .env for subsequent phases ──────────────────────────────
set -a; source .env; set +a

# ── Phase 2b: Remote-Save credentials (conditional) ─────────────
if [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]]; then
    RS_ENV="services/remote-save/.env"
    if [[ -f "$RS_ENV" ]]; then
        info "Remote-save .env already exists"
    else
        prompt_yn "Configure Dataverse credentials for remote-save now?" CONFIGURE_DV "n"
        if [[ "$CONFIGURE_DV" == "true" ]]; then
            cp services/remote-save/.env.example "$RS_ENV"
            prompt "Dataverse tenant ID" DV_TENANT ""
            prompt "Dataverse client ID" DV_CLIENT ""
            prompt_secret "Dataverse client secret" DV_SECRET ""
            prompt "Dataverse environment URL (e.g. https://org.crm.dynamics.com)" DV_URL ""
            prompt "Dataverse table name" DV_TABLE ""
            prompt "Dataverse column prefix" DV_PREFIX "jenssch"

            env_set "$RS_ENV" DATAVERSE_TENANT_ID     "$DV_TENANT"
            env_set "$RS_ENV" DATAVERSE_CLIENT_ID     "$DV_CLIENT"
            env_set "$RS_ENV" DATAVERSE_CLIENT_SECRET  "$DV_SECRET"
            env_set "$RS_ENV" DATAVERSE_ENV_URL        "$DV_URL"
            env_set "$RS_ENV" DATAVERSE_TABLE          "$DV_TABLE"
            env_set "$RS_ENV" DATAVERSE_COL_NAME       "${DV_PREFIX}_name"
            env_set "$RS_ENV" DATAVERSE_COL_DATA       "${DV_PREFIX}_data"
            env_set "$RS_ENV" DATAVERSE_COL_TEXT       "${DV_PREFIX}_text"
            env_set "$RS_ENV" DATAVERSE_COL_CONFIDENCE "${DV_PREFIX}_confidence"
            env_set "$RS_ENV" DATAVERSE_COL_COFFEE_TYPE "${DV_PREFIX}_type"
            ok "Remote-save .env configured"
        else
            cp services/remote-save/.env.example "$RS_ENV"
            warn "Remote-save .env copied from example — fill in Dataverse credentials before use"
        fi
    fi
fi

# ════════════════════════════════════════════════════════════════
#  Phase 3: Python Virtual Environment
# ════════════════════════════════════════════════════════════════
header "Phase 3 · Python virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"
if [[ -d "$VENV_DIR" ]]; then
    info "Virtual environment already exists at .venv/"
else
    python3 -m venv "$VENV_DIR"
    ok "Created virtual environment at .venv/"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
info "Installing app dependencies..."
if pip install --upgrade pip >> "$LOG_FILE" 2>&1 && \
   pip install -r app/requirements.txt >> "$LOG_FILE" 2>&1; then
    PKG_COUNT=$(pip list --format=freeze 2>/dev/null | wc -l)
    ok "Installed $PKG_COUNT packages"
else
    fail "pip install failed — check $LOG_FILE"
fi

# Verify picoquake
if python -c "import picoquake" 2>/dev/null; then
    ok "picoquake package OK"
else
    warn "picoquake package not importable — sensor mode 'picoquake' may fail"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 4: Model Downloads
# ════════════════════════════════════════════════════════════════
header "Phase 4 · Model downloads"

# LLM model
GGUF_PATH="services/llm/coffee-gguf/coffee-Q4_K_M.gguf"
if [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]]; then
    mkdir -p "$(dirname "$GGUF_PATH")"
    if [[ -f "$GGUF_PATH" ]]; then
        ok "LLM model already present ($(du -h "$GGUF_PATH" | cut -f1))"
    else
        info "Downloading LLM model (~350 MB)..."
        if wget --progress=bar:force -O "$GGUF_PATH" "$MODEL_URL" 2>&1 | tail -1; then
            # Verify checksum if configured
            if [[ -n "$MODEL_SHA256" ]]; then
                ACTUAL_SHA="$(sha256sum "$GGUF_PATH" | cut -d' ' -f1)"
                if [[ "$ACTUAL_SHA" == "$MODEL_SHA256" ]]; then
                    ok "LLM model downloaded and verified"
                else
                    fail "LLM model checksum mismatch (expected $MODEL_SHA256, got $ACTUAL_SHA)"
                fi
            else
                ok "LLM model downloaded ($(du -h "$GGUF_PATH" | cut -f1))"
            fi
        else
            fail "LLM model download failed"
        fi
    fi
elif [[ "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
    info "LLM using hailo-ollama — GGUF model not needed"
    # Pull the configured model into hailo-ollama
    if [[ "${HAILO_OLLAMA_INSTALLED:-false}" == "true" ]]; then
        _OLLAMA_MODEL="${LLM_MODEL:-qwen2:1.5b}"
        info "Pulling model '${_OLLAMA_MODEL}' into hailo-ollama..."
        # Start hailo-ollama temporarily in the background (suppress its output)
        hailo-ollama >> "$LOG_FILE" 2>&1 &
        _HAILO_PID=$!
        # Disown so it doesn't receive signals from the script's process group
        disown "$_HAILO_PID" 2>/dev/null || true
        _PULL_URL="http://localhost:8000"
        _PULL_TRIES=0; _PULL_MAX=30
        echo -n "  Waiting for hailo-ollama to start "
        while ! curl -sf --max-time 2 "${_PULL_URL}/api/tags" > /dev/null 2>&1; do
            _PULL_TRIES=$((_PULL_TRIES + 1))
            if (( _PULL_TRIES >= _PULL_MAX )); then
                echo ""
                fail "hailo-ollama did not start within ${_PULL_MAX}×2s"
                break
            fi
            echo -n "."
            sleep 2
        done
        if (( _PULL_TRIES < _PULL_MAX )); then
            echo ""
            ok "hailo-ollama is running (pid ${_HAILO_PID})"
            if curl -sf --max-time 300 "${_PULL_URL}/api/pull" \
                 -H 'Content-Type: application/json' \
                 -d "{\"model\": \"${_OLLAMA_MODEL}\", \"stream\": true}" \
                 >> "$LOG_FILE" 2>&1; then
                ok "Model '${_OLLAMA_MODEL}' pulled successfully"
            else
                fail "Model pull failed — check $LOG_FILE"
            fi
        fi
        # Stop the temporary hailo-ollama process (disowned, so wait is unavailable)
        kill "$_HAILO_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$_HAILO_PID" 2>/dev/null || true
    else
        warn "hailo-ollama not installed — cannot pull model; install later and run:"
        warn "  hailo-ollama &  then  curl http://localhost:8000/api/pull -H 'Content-Type: application/json' -d '{\"model\": \"${LLM_MODEL:-qwen2:1.5b}\"}'"
    fi
else
    info "LLM disabled — skipping model download"
fi

# TTS voice
if [[ "${TTS_ENABLED:-false}" == "true" ]]; then
    TTS_MODEL_DIR="services/tts/models"
    mkdir -p "$TTS_MODEL_DIR"
    if ls "$TTS_MODEL_DIR"/*.onnx 1>/dev/null 2>&1; then
        ok "TTS voice model already present"
    else
        info "Downloading TTS voice ($TTS_VOICE, ~100 MB)..."
        if python services/tts/scripts/download_model.py \
                --voice "$TTS_VOICE" \
                --output-dir "$TTS_MODEL_DIR" >> "$LOG_FILE" 2>&1; then
            ok "TTS voice downloaded"
        else
            fail "TTS voice download failed — check $LOG_FILE"
        fi
    fi
else
    info "TTS disabled — skipping voice download"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 5: Docker Image Builds
# ════════════════════════════════════════════════════════════════
header "Phase 5 · Docker image builds"

build_service() {
    local name="$1" profile="$2"
    info "Building $name..."
    if docker compose --profile "$profile" build 2>&1 | tee -a "$LOG_FILE"; then
        ok "$name image built"
    else
        fail "$name image build failed — check $LOG_FILE"
    fi
}

if [[ "${CLASSIFIER_ENABLED:-false}" == "true" ]]; then
    build_service "classifier" "classifier"
else
    info "classifier disabled — skipping build"
fi

if [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]]; then
    build_service "remote-save" "remote-save"
else
    info "remote-save disabled — skipping build"
fi

if [[ "${TTS_ENABLED:-false}" == "true" ]]; then
    build_service "tts" "tts"
else
    info "tts disabled — skipping build"
fi

if [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]]; then
    info "Building LLM service (this may take 15–30 min on ARM64)..."
    build_service "llm" "llm"
elif [[ "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
    info "LLM using hailo-ollama — skipping Docker build"
else
    info "llm disabled — skipping build"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 6: USB Device Setup
# ════════════════════════════════════════════════════════════════
header "Phase 6 · USB device setup"

if [[ "${SENSOR_MODE:-mock}" == "picoquake" ]]; then
    UDEV_RULE='/etc/udev/rules.d/99-picoquake.rules'
    RULE_CONTENT='SUBSYSTEM=="tty", ATTRS{idProduct}=="000a", ATTRS{idVendor}=="2e8a", MODE="0666", SYMLINK+="picoquake"'
    # Disable USB autosuspend for the PicoQuake to prevent connection drops
    AUTOSUSPEND_RULE='/etc/udev/rules.d/99-picoquake-power.rules'
    AUTOSUSPEND_CONTENT='ACTION=="add", SUBSYSTEM=="usb", ATTRS{idProduct}=="000a", ATTRS{idVendor}=="2e8a", TEST=="power/control", ATTR{power/control}="on"'

    if [[ -f "$UDEV_RULE" ]]; then
        ok "udev rule already installed"
    else
        echo "$RULE_CONTENT" | sudo tee "$UDEV_RULE" > /dev/null
        ok "udev rule installed at $UDEV_RULE"
    fi

    if [[ -f "$AUTOSUSPEND_RULE" ]]; then
        ok "USB autosuspend rule already installed"
    else
        echo "$AUTOSUSPEND_CONTENT" | sudo tee "$AUTOSUSPEND_RULE" > /dev/null
        ok "USB autosuspend disabled for PicoQuake"
    fi

    sudo udevadm control --reload-rules
    sudo udevadm trigger

    if ls /dev/ttyACM* 1>/dev/null 2>&1; then
        ok "PicoQuake USB device detected"
    else
        warn "No /dev/ttyACM* device found — connect PicoQuake before starting"
    fi
else
    info "Sensor mode is '${SENSOR_MODE:-mock}' — skipping USB setup"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 7: Systemd Service (Optional)
# ════════════════════════════════════════════════════════════════
header "Phase 7 · Systemd auto-start"

prompt_yn "Enable auto-start on boot?" ENABLE_SYSTEMD "y"

if [[ "$ENABLE_SYSTEMD" == "true" ]]; then
    # Build the profile flags string for docker compose
    PROFILES=""
    [[ "${CLASSIFIER_ENABLED:-false}"  == "true" ]] && PROFILES="$PROFILES --profile classifier"
    [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" != "ollama" ]] && PROFILES="$PROFILES --profile llm"
    [[ "${TTS_ENABLED:-false}"         == "true" ]] && PROFILES="$PROFILES --profile tts"
    [[ "${REMOTE_SAVE_ENABLED:-false}" == "true" ]] && PROFILES="$PROFILES --profile remote-save"
    PROFILES="${PROFILES# }"  # trim leading space

    # Docker services unit
    sudo tee /etc/systemd/system/rpicoffee-services.service > /dev/null <<EOF
[Unit]
Description=rpiCoffee Docker Services
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/docker compose ${PROFILES} up -d --build
ExecStop=/usr/bin/docker compose ${PROFILES} down
User=${USER}
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

    # hailo-ollama unit (only when using ollama backend)
    if [[ "${LLM_ENABLED:-false}" == "true" && "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
        HAILO_BIN="$(command -v hailo-ollama 2>/dev/null || echo /usr/bin/hailo-ollama)"
        sudo tee /etc/systemd/system/rpicoffee-hailo-ollama.service > /dev/null <<EOF
[Unit]
Description=rpiCoffee hailo-ollama LLM Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${HAILO_BIN}
Restart=on-failure
RestartSec=5
User=${USER}

[Install]
WantedBy=multi-user.target
EOF
        ok "hailo-ollama systemd service created"
        HAILO_UNIT_CREATED=true
    else
        # Ensure stale unit is disabled if switching away from ollama
        if systemctl is-enabled rpicoffee-hailo-ollama &>/dev/null 2>&1; then
            sudo systemctl stop rpicoffee-hailo-ollama 2>/dev/null || true
            sudo systemctl disable rpicoffee-hailo-ollama 2>/dev/null || true
            info "Disabled stale rpicoffee-hailo-ollama service"
        fi
        HAILO_UNIT_CREATED=false
    fi

    # Sudoers drop-in for hailo-ollama management from the app
    sudo tee /etc/sudoers.d/rpicoffee-hailo-ollama > /dev/null <<EOF
${USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start rpicoffee-hailo-ollama, /usr/bin/systemctl stop rpicoffee-hailo-ollama, /usr/bin/systemctl enable rpicoffee-hailo-ollama, /usr/bin/systemctl disable rpicoffee-hailo-ollama, /usr/bin/systemctl is-active rpicoffee-hailo-ollama, /usr/bin/systemctl is-enabled rpicoffee-hailo-ollama
EOF
    sudo chmod 0440 /etc/sudoers.d/rpicoffee-hailo-ollama
    ok "Sudoers drop-in for hailo-ollama management created"

    # App unit
    HAILO_AFTER=""
    HAILO_WANTS=""
    if [[ "${HAILO_UNIT_CREATED:-false}" == "true" ]]; then
        HAILO_AFTER=" rpicoffee-hailo-ollama.service"
        HAILO_WANTS="Wants=rpicoffee-hailo-ollama.service"
    fi
    sudo tee /etc/systemd/system/rpicoffee-app.service > /dev/null <<EOF
[Unit]
Description=rpiCoffee Application
After=rpicoffee-services.service${HAILO_AFTER}
Requires=rpicoffee-services.service
${HAILO_WANTS}

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}/app
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=${SCRIPT_DIR}/.env
ExecStart=${VENV_DIR}/bin/uvicorn main:app --host 0.0.0.0 --port \${APP_PORT}
Restart=on-failure
RestartSec=5
User=${USER}

[Install]
WantedBy=multi-user.target
EOF

    # Kiosk (Chromium auto-launch) unit
    KIOSK_URL="http://localhost:${APP_PORT:-8080}"

    # Create the kiosk launcher helper script
    cat > "${SCRIPT_DIR}/kiosk.sh" <<'KIOSK'
#!/usr/bin/env bash
#
# rpiCoffee – Kiosk launcher
#
# Waits for the rpiCoffee app to become healthy, then launches
# Chromium in app mode (maximised, no browser chrome).
#
# Chromium runs in the FOREGROUND (via exec) so that systemd
# keeps the service active for the lifetime of the browser process.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -a; source "$SCRIPT_DIR/.env"; set +a

APP_URL="http://localhost:${APP_PORT:-8080}"

# ── Wait for the X display to be available ──
MAX_DISPLAY_WAIT=60
for (( i=1; i<=MAX_DISPLAY_WAIT/2; i++ )); do
    if [ -S /tmp/.X11-unix/X0 ]; then
        break
    fi
    echo "[kiosk] Waiting for X display... ($i)"
    sleep 2
done
if [ ! -S /tmp/.X11-unix/X0 ]; then
    echo "[kiosk] ERROR: X display not available after ${MAX_DISPLAY_WAIT}s" >&2
    exit 1
fi

# ── Wait for the rpiCoffee web app to respond ──
MAX_WAIT=120   # seconds
ELAPSED=0

echo "[kiosk] Waiting for rpiCoffee app at $APP_URL ..."
while ! curl -sf --max-time 2 "$APP_URL" > /dev/null 2>&1; do
    sleep 2
    ELAPSED=$((ELAPSED + 2))
    if (( ELAPSED >= MAX_WAIT )); then
        echo "[kiosk] App did not become ready after ${MAX_WAIT}s – aborting"
        exit 1
    fi
done
echo "[kiosk] App is ready – launching Chromium"

# ── Detect chromium binary (Trixie: chromium, older: chromium-browser) ──
CHROMIUM_BIN=$(command -v chromium-browser 2>/dev/null || command -v chromium 2>/dev/null)
if [[ -z "$CHROMIUM_BIN" ]]; then
    echo "[kiosk] ERROR: No chromium binary found" >&2
    exit 1
fi

# ── Clean up crash flags so Chromium doesn't show a restore prompt ──
CHROMIUM_PREFS="$HOME/.config/chromium/Default/Preferences"
if [[ -f "$CHROMIUM_PREFS" ]]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' "$CHROMIUM_PREFS"
    sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/'   "$CHROMIUM_PREFS"
fi

# ── Launch Chromium in the foreground (exec replaces this shell) ──
export DISPLAY=:0
exec $CHROMIUM_BIN \
  --kiosk "$APP_URL" \
  --password-store=basic \
  --disable-infobars \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --check-for-update-interval=31536000
KIOSK
    chmod +x "${SCRIPT_DIR}/kiosk.sh"
    ok "Created kiosk launcher script (kiosk.sh)"

    sudo tee /etc/systemd/system/rpicoffee-kiosk.service > /dev/null <<EOF
[Unit]
Description=rpiCoffee Chromium Kiosk
After=rpicoffee-app.service
Requires=rpicoffee-app.service

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
Environment="DISPLAY=:0"
Environment="XAUTHORITY=/home/${USER}/.Xauthority"
ExecStartPre=/bin/sleep 5
ExecStart=${SCRIPT_DIR}/kiosk.sh
Restart=on-failure
RestartSec=10
User=${USER}

[Install]
WantedBy=graphical.target
EOF
    ok "Kiosk systemd service created"

    sudo systemctl daemon-reload
    ENABLE_UNITS="rpicoffee-services rpicoffee-app rpicoffee-kiosk"
    if [[ "${HAILO_UNIT_CREATED:-false}" == "true" ]]; then
        ENABLE_UNITS="rpicoffee-hailo-ollama $ENABLE_UNITS"
    fi
    # shellcheck disable=SC2086
    sudo systemctl enable $ENABLE_UNITS
    ok "Systemd services created and enabled"
else
    info "Skipping systemd setup"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 8: Data Directory Bootstrap
# ════════════════════════════════════════════════════════════════
header "Phase 8 · Data directory bootstrap"

mkdir -p data data/audio
ok "data/ and data/audio/ directories ready"

# Copy seed CSV samples if .csv versions don't exist
COPIED=0
for sample in data/*.csv.sample; do
    [[ -f "$sample" ]] || continue
    target="${sample%.sample}"
    if [[ ! -f "$target" ]]; then
        cp "$sample" "$target"
        ((COPIED++))
    fi
done
if (( COPIED > 0 )); then
    ok "Copied $COPIED seed CSV file(s)"
else
    ok "Seed CSV files already in place"
fi

# ════════════════════════════════════════════════════════════════
#  Phase 9: Post-Setup Summary
# ════════════════════════════════════════════════════════════════

# Detect IP
PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
PI_IP="${PI_IP:-<pi-ip>}"

echo ""
echo "════════════════════════════════════════"
echo -e "  ${BOLD}rpiCoffee Setup Complete${NC}"
echo "════════════════════════════════════════"
echo ""

# System packages
ok "System packages installed (Docker, Python 3, build tools)"

# Venv
PKG_COUNT=$(pip list --format=freeze 2>/dev/null | wc -l)
ok "Python venv at .venv/ ($PKG_COUNT packages)"

# Models
if [[ -f "$GGUF_PATH" ]]; then
    ok "LLM model: coffee-Q4_K_M.gguf ($(du -h "$GGUF_PATH" | cut -f1))"
elif [[ "${LLM_ENABLED:-false}" == "true" ]]; then
    fail "LLM model: MISSING"
fi

TTS_MODEL_DIR="services/tts/models"
if ls "$TTS_MODEL_DIR"/*.onnx 1>/dev/null 2>&1; then
    ok "TTS voice: $TTS_VOICE"
elif [[ "${TTS_ENABLED:-false}" == "true" ]]; then
    fail "TTS voice: MISSING"
fi

# Docker images
IMAGES="$(docker images --format '{{.Repository}}:{{.Tag}} ({{.Size}})' | grep rpicoffee || true)"
if [[ -n "$IMAGES" ]]; then
    ok "Docker images:"
    echo "$IMAGES" | while read -r line; do echo "       $line"; done
fi

# USB sensor
if [[ "${SENSOR_MODE:-mock}" == "picoquake" ]]; then
    if ls /dev/ttyACM* 1>/dev/null 2>&1; then
        ok "USB sensor: detected, udev rule installed"
    else
        warn "USB sensor: udev rule installed but no device connected"
    fi
else
    info "USB sensor: not configured (mode=${SENSOR_MODE:-mock})"
fi

# hailo-ollama
if [[ "${LLM_BACKEND:-llama-cpp}" == "ollama" ]]; then
    if [[ "${HAILO_OLLAMA_INSTALLED:-false}" == "true" ]]; then
        ok "hailo-ollama: installed"
    else
        warn "hailo-ollama: NOT installed — download from https://hailo.ai/developer-zone/"
    fi
    if [[ "${HAILO_UNIT_CREATED:-false}" == "true" ]]; then
        ok "hailo-ollama: systemd service enabled (auto-start at boot)"
    fi
fi

# Systemd
if [[ "${ENABLE_SYSTEMD:-false}" == "true" ]]; then
    ok "Systemd auto-start: enabled"
    ok "Kiosk mode: Chromium will open http://localhost:${APP_PORT:-8080} on boot"
else
    info "Systemd auto-start: not configured"
fi

# Warnings
if (( ${#WARNINGS[@]} > 0 )); then
    echo ""
    echo -e "  ${YELLOW}⚠ Warnings:${NC}"
    for w in "${WARNINGS[@]}"; do
        echo -e "    ${YELLOW}•${NC} $w"
    done
fi

# Errors
if (( ${#ERRORS[@]} > 0 )); then
    echo ""
    echo -e "  ${RED}✗ Errors:${NC}"
    for e in "${ERRORS[@]}"; do
        echo -e "    ${RED}•${NC} $e"
    done
fi

echo ""
echo -e "  ${BOLD}Access:${NC} http://${PI_IP}:${APP_PORT:-8080}/admin/"
echo ""
if [[ "${ENABLE_SYSTEMD:-false}" == "true" ]]; then
    echo "  Next: reboot, or run ${BOLD}./start.sh${NC} now"
else
    echo "  Next: run ${BOLD}./start.sh${NC} to launch"
fi

if $GROUP_CHANGED; then
    echo "        (open a new shell or reboot first for group changes)"
fi

echo ""
echo "  Full log: $LOG_FILE"
echo ""
echo "════════════════════════════════════════"
