"""rpiCoffee – main FastAPI application."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import config
from admin.router import router as admin_router
from pipeline import run_pipeline, run_pipeline_streaming
from services.classifier_client import ClassifierClient
from services.llm_client import LLMClient
from services.ollama_client import OllamaClient
from services.tts_client import TTSClient
from services.remote_save_client import RemoteSaveClient
from services import training_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger("rpicoffee")

AUDIO_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))) / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# ── Auto-trigger event bus ───────────────────────────────────────
# Connected SSE clients register an asyncio.Queue here; the auto-trigger
# background task pushes events into every queue.
_auto_trigger_clients: list[asyncio.Queue] = []
_auto_trigger_task: asyncio.Task | None = None

# Track whether we started picoquake so shutdown can clean up
_sensor_started = False

# Signals SSE generators to exit on shutdown
_shutdown_event: asyncio.Event | None = None


# Sensor watchdog constants
_SENSOR_MAX_RESTARTS = 5
_SENSOR_RESTART_WINDOW = 300  # seconds


async def _auto_trigger_loop():
    """Poll the PicoQuake recording_flag and run the pipeline on vibration."""
    from sensor.picoquake_reader import picoquake_reader

    logger.info("Auto-trigger loop started (polling every 500 ms)")
    _prev_flag = 0
    _restart_times: list[float] = []  # track restart timestamps for rate-limiting
    while True:
        try:
            await asyncio.sleep(0.5)

            if not config.SENSOR_AUTO_TRIGGER:
                continue

            # ── Watchdog: auto-restart dead sensor ────────────────
            if not picoquake_reader.is_running and config.SENSOR_MODE == "picoquake":
                import time as _time
                now = _time.monotonic()
                # Prune restart timestamps outside the rate-limit window
                _restart_times = [t for t in _restart_times
                                  if now - t < _SENSOR_RESTART_WINDOW]
                if len(_restart_times) >= _SENSOR_MAX_RESTARTS:
                    # Already restarted too many times recently — back off
                    logger.error(
                        "Sensor has been restarted %d times in the last %ds "
                        "— not attempting again. Manual intervention required.",
                        len(_restart_times), _SENSOR_RESTART_WINDOW,
                    )
                    _broadcast({"type": "status",
                                "message": "Sensor offline — max restarts exceeded. "
                                           "Check hardware and restart manually."})
                    await asyncio.sleep(30)  # long sleep before re-checking
                    continue

                logger.warning("Sensor acquisition not running — attempting restart…")
                _broadcast({"type": "status",
                            "message": "Sensor connection lost — reconnecting…"})
                try:
                    picoquake_reader.stop()  # clean up stale state / shared memory
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, _start_sensor)
                    _restart_times.append(now)
                    logger.info("Sensor restarted successfully (attempt %d/%d in window)",
                                len(_restart_times), _SENSOR_MAX_RESTARTS)
                    _broadcast({"type": "status",
                                "message": "Sensor reconnected successfully."})
                except Exception:
                    logger.exception("Sensor restart failed — will retry in 10s")
                    _broadcast({"type": "status",
                                "message": "Sensor restart failed — retrying…"})
                await asyncio.sleep(10)
                continue

            if not picoquake_reader.is_running:
                continue
            if picoquake_reader._ring is None:
                continue

            flag = picoquake_reader._ring.recording_flag

            # Guard: reset stale recording_flag=2 that was never consumed
            # (e.g. after exception recovery, startup race, or manual brew)
            if flag == 2:
                logger.warning("Auto-trigger: found stale recording_flag=2 — resetting to 0")
                picoquake_reader._ring.recording_flag = 0
                _prev_flag = 0
                continue

            # Detect recording start (flag transitions 0→1) and stream data live
            if flag == 1 and _prev_flag == 0:
                logger.info("Auto-trigger: recording started (capturing %ds)…",
                            config.SENSOR_DURATION_S)
                _broadcast({"type": "status",
                            "message": "Vibration detected! Recording sensor data…"})

                try:
                    # Stream sensor data to the kiosk chart in real time.
                    # stream_capture() yields batches until flag==2, then resets flag→0.
                    all_sensor_data: list[dict[str, float]] = []
                    t0: float | None = None
                    async for batch in picoquake_reader.stream_capture(batch_interval=0.3, auto_reset=False):
                        if not batch:
                            continue
                        # Normalise elapsed_s so chart starts at t=0
                        if t0 is None:
                            t0 = batch[0].get("elapsed_s", 0.0)
                        for sample in batch:
                            sample["elapsed_s"] = sample.get("elapsed_s", 0.0) - t0
                        all_sensor_data.extend(batch)
                        # Downsample large batches for the SSE payload
                        step = max(1, len(batch) // 30)
                        _broadcast({"type": "sensor_data", "data": batch[::step]})

                    # Recording complete — run classifier → llm → tts pipeline
                    # with pre-collected sensor data (no re-read required).
                    logger.info("Auto-trigger: streaming done (%d samples), running pipeline…",
                                len(all_sensor_data))
                    _broadcast({"type": "status", "message": "Recording complete. Running pipeline…"})

                    def _progress(msg: str) -> None:
                        _broadcast({"type": "status", "message": msg})

                    result = await run_pipeline(sensor_data=all_sensor_data, on_progress=_progress)
                    result.pop("sensor_data", None)  # already streamed to chart

                    _broadcast({"type": "result", "data": result})
                    logger.info("Auto-trigger pipeline complete: %s", result.get("label"))

                finally:
                    # ALWAYS re-arm the sensor, even if the pipeline fails.
                    # Without this, recording_flag stays at 2 and the
                    # acquisition subprocess never auto-triggers again.
                    if picoquake_reader._ring is not None:
                        picoquake_reader._ring.recording_flag = 0
                        logger.info("Auto-trigger: sensor re-armed")

                _prev_flag = 0
                continue

            _prev_flag = flag

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Auto-trigger loop error")
            _prev_flag = 0
            await asyncio.sleep(2)


def _broadcast(event: dict[str, Any]):
    """Push an event dict to all connected SSE clients."""
    dead: list[asyncio.Queue] = []
    for q in _auto_trigger_clients:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _auto_trigger_clients.remove(q)


# ── Sensor lifecycle helpers ─────────────────────────────────────

def _start_sensor() -> None:
    """Start the PicoQuake acquisition subprocess (blocking).

    Only spawns the subprocess + waits for shared memory. Does NOT create
    the async auto-trigger task — callers must call ``_ensure_auto_trigger()``
    on the event loop thread afterwards.

    Safe to call when already running (no-op) or when mode != picoquake.
    """
    global _sensor_started

    cfg = config.to_dict()
    if cfg.get("SENSOR_MODE") != "picoquake":
        return

    from sensor.picoquake_reader import picoquake_reader

    picoquake_reader.start(
        device_id=cfg.get("SENSOR_DEVICE_ID", "cf79"),
        sample_rate=cfg.get("SENSOR_SAMPLE_RATE_HZ", 100),
        duration=cfg.get("SENSOR_DURATION_S", 30),
        threshold=cfg.get("SENSOR_VIBRATION_THRESHOLD", 2.0),
        rms_window_s=cfg.get("SENSOR_RMS_WINDOW_S", 1.0),
        acc_range=cfg.get("SENSOR_ACC_RANGE_G", 4),
        gyro_range=cfg.get("SENSOR_GYRO_RANGE_DPS", 500),
        filter_hz=cfg.get("SENSOR_FILTER_HZ", 42),
        trigger_sources=cfg.get("SENSOR_TRIGGER_SOURCES", "accel"),
        trigger_combine_mode=cfg.get("SENSOR_TRIGGER_COMBINE_MODE", "or"),
        gyro_threshold=cfg.get("SENSOR_GYRO_THRESHOLD", 10.0),
        gyro_rms_window_s=cfg.get("SENSOR_GYRO_RMS_WINDOW_S", 1.0),
        warmup_s=cfg.get("SENSOR_WARMUP_S", 5),
        cooldown_s=cfg.get("SENSOR_COOLDOWN_S", 10),
    )
    _sensor_started = True


def _ensure_auto_trigger() -> None:
    """Create (or re-create) the auto-trigger background task.

    Must be called from the async event-loop thread.
    """
    global _auto_trigger_task
    if _auto_trigger_task and not _auto_trigger_task.done():
        _auto_trigger_task.cancel()
    _auto_trigger_task = asyncio.create_task(_auto_trigger_loop())


async def _stop_sensor() -> None:
    """Stop the auto-trigger task and the PicoQuake acquisition process."""
    global _auto_trigger_task, _sensor_started

    if _auto_trigger_task and not _auto_trigger_task.done():
        _auto_trigger_task.cancel()
        try:
            await _auto_trigger_task
        except asyncio.CancelledError:
            pass
    _auto_trigger_task = None

    if _sensor_started:
        from sensor.picoquake_reader import picoquake_reader
        picoquake_reader.stop()
        _sensor_started = False


async def restart_sensor() -> dict[str, Any]:
    """Stop then start the sensor – called from admin or the restart API."""
    await _stop_sensor()
    # Run the blocking start in a thread to avoid freezing the event loop
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _start_sensor)
    _ensure_auto_trigger()

    # Return current sensor info
    mode = config.SENSOR_MODE
    if mode == "picoquake":
        from sensor.picoquake_reader import picoquake_reader
        return picoquake_reader.info
    return {"mode": mode, "restarted": True}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    logger.info("rpiCoffee starting up")
    cfg = config.to_dict()
    for svc in ("CLASSIFIER", "LLM", "TTS", "REMOTE_SAVE"):
        enabled = cfg.get(f"{svc}_ENABLED", False)
        endpoint = cfg.get(f"{svc}_ENDPOINT", "n/a")
        logger.info("  %s: %s (%s)", svc, "enabled" if enabled else "disabled", endpoint)

    # Sensor mode
    sensor_mode = cfg.get("SENSOR_MODE", "mock")
    logger.info("  SENSOR: mode=%s", sensor_mode)

    # Run blocking sensor start in a thread so uvicorn can finish booting
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _start_sensor)
    _ensure_auto_trigger()

    yield

    # Signal all SSE generators to exit
    _shutdown_event.set()
    # Push sentinel so SSE generators unblock immediately
    _broadcast({"type": "_shutdown"})

    await _stop_sensor()

    logger.info("rpiCoffee shutting down")


app = FastAPI(title="rpiCoffee", version="1.0.0", lifespan=lifespan)

# Templates
templates = Jinja2Templates(directory=str(Path(__file__).parent / "admin" / "templates"))

# Routers
app.include_router(admin_router, prefix="/admin", tags=["admin"])

# Serve generated audio files
app.mount("/audio", StaticFiles(directory=str(AUDIO_DIR)), name="audio")


# ── API endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Kiosk display – no auth required."""
    return templates.TemplateResponse("index.html", {"request": request, "config": config.to_dict()})


@app.post("/api/brew")
async def brew():
    """Run the full pipeline: sensor → classifier → llm → tts."""
    result = await run_pipeline()
    return result


@app.post("/api/sensor/restart")
async def sensor_restart():
    """Restart the sensor acquisition process (e.g. after settings change)."""
    logger.info("Sensor restart requested")
    try:
        info = await restart_sensor()
        return info
    except Exception:
        logger.exception("Sensor restart failed")
        mode = config.SENSOR_MODE
        if mode == "picoquake":
            from sensor.picoquake_reader import picoquake_reader
            return picoquake_reader.info
        return {"enabled": True, "healthy": False, "mode": mode, "error": "Restart failed"}


@app.get("/api/brew/stream")
async def brew_stream():
    """Stream the brew pipeline as Server-Sent Events."""
    return StreamingResponse(
        run_pipeline_streaming(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/test/stream")
async def test_stream():
    """Stream a test run: replay a random CSV file through the full pipeline (no save)."""
    return StreamingResponse(
        run_pipeline_streaming(force_mock=True, skip_save=True),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/auto-trigger/stream")
async def auto_trigger_stream():
    """SSE stream for auto-triggered brew results (kiosk listens here)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _auto_trigger_clients.append(q)

    async def event_generator():
        try:
            # Send a heartbeat so the client knows it connected
            yield "event: connected\ndata: {}\n\n"
            while not (_shutdown_event and _shutdown_event.is_set()):
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    # Send SSE comment as keepalive
                    yield ": keepalive\n\n"
                    continue
                if event.get("type") == "_shutdown":
                    break
                etype = event.get("type", "message")
                payload = json.dumps(event.get("data", event.get("message", "")))
                yield f"event: {etype}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _auto_trigger_clients:
                _auto_trigger_clients.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/sensor/stream")
async def sensor_live_stream():
    """SSE endpoint streaming continuous live sensor data for the admin dashboard."""
    mode = config.SENSOR_MODE

    async def generate():
        if mode == "picoquake":
            from sensor.picoquake_reader import picoquake_reader
            if not picoquake_reader.is_running:
                yield "event: error\ndata: \"Sensor not running\"\n\n"
                return
            try:
                async for batch in picoquake_reader.stream_live(batch_interval=0.3):
                    step = max(1, len(batch) // 60)
                    payload = json.dumps(batch[::step])
                    yield f"data: {payload}\n\n"
            except asyncio.CancelledError:
                return
        else:
            yield "event: error\ndata: \"Live stream only available in picoquake mode\"\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/services/status")
async def services_status():
    """Check health of all backend services."""
    statuses: dict[str, dict] = {}

    if config.CLASSIFIER_ENABLED:
        statuses["classifier"] = await ClassifierClient.health()
    else:
        statuses["classifier"] = {"enabled": False}

    if config.LLM_ENABLED:
        if config.LLM_BACKEND == "ollama":
            statuses["llm"] = await OllamaClient.health()
        else:
            statuses["llm"] = await LLMClient.health()
    else:
        statuses["llm"] = {"enabled": False}

    if config.TTS_ENABLED:
        statuses["tts"] = await TTSClient.health()
    else:
        statuses["tts"] = {"enabled": False}

    if config.REMOTE_SAVE_ENABLED:
        statuses["remote_save"] = await RemoteSaveClient.health()
    else:
        statuses["remote_save"] = {"enabled": False}

    # Sensor status depends on mode
    sensor_mode = config.SENSOR_MODE
    if sensor_mode == "picoquake":
        from sensor.picoquake_reader import picoquake_reader
        statuses["sensor"] = picoquake_reader.info
    elif sensor_mode == "mock":
        statuses["sensor"] = {"enabled": True, "healthy": True, "mode": "mock"}
    else:
        statuses["sensor"] = {"enabled": True, "healthy": True, "mode": "serial"}

    return statuses


# ── Service settings proxy API ───────────────────────────────────

_SERVICE_CLIENTS = {
    "classifier": ClassifierClient,
    "llm": LLMClient,
    "llm-ollama": OllamaClient,
    "tts": TTSClient,
    "remote-save": RemoteSaveClient,
}


@app.get("/api/services/{name}/settings")
async def service_settings_get(name: str):
    """Proxy GET /settings to the named backend service."""
    client = _SERVICE_CLIENTS.get(name)
    if client is None:
        return {"error": f"Unknown service: {name}"}
    result = await client.get_settings()
    if result is None:
        return {"error": f"Failed to fetch settings from {name}"}
    return result


@app.patch("/api/services/{name}/settings")
async def service_settings_update(name: str, request: Request):
    """Proxy PATCH /settings to the named backend service."""
    client = _SERVICE_CLIENTS.get(name)
    if client is None:
        return {"error": f"Unknown service: {name}"}
    body = await request.json()
    settings = body.get("settings", body)
    result = await client.update_settings(settings)
    if result is None:
        return {"error": f"Failed to update settings on {name}"}
    # Sync any keys that also exist in app config
    _APP_CONFIG_KEYS = {"LLM_BACKEND", "LLM_MODEL", "LLM_KEEP_ALIVE"}
    sync = {k: v for k, v in settings.items() if k in _APP_CONFIG_KEYS}
    if sync:
        config.update_many(sync)
    return result


# ── Data Collection API ──────────────────────────────────────────

@app.post("/api/collect/start")
async def collect_start(request: Request):
    """Enable data collection mode with a specific label."""
    body = await request.json()
    label = body.get("label", "").strip()
    if not label:
        return {"error": "Label is required"}

    config.update_many({
        "DATA_COLLECT_ENABLED": True,
        "DATA_COLLECT_LABEL": label,
    })
    logger.info("Data collection started for label: %s", label)
    return {"status": "collecting", "label": label}


@app.post("/api/collect/stop")
async def collect_stop():
    """Disable data collection mode."""
    config.update_many({
        "DATA_COLLECT_ENABLED": False,
        "DATA_COLLECT_LABEL": "",
    })
    logger.info("Data collection stopped")
    return {"status": "stopped"}


# ── Training Data Management API ─────────────────────────────────

@app.get("/api/training-data")
async def api_list_training_data():
    """List all training CSV files grouped by label."""
    return {"training_data": training_data.list_training_data()}


@app.delete("/api/training-data/{label}/{filename}")
async def api_delete_training_file(label: str, filename: str):
    """Delete a specific training CSV file."""
    ok = training_data.delete_training_file(label, filename)
    if ok:
        return {"status": "deleted", "label": label, "filename": filename}
    return {"error": "File not found"}


@app.delete("/api/training-data/{label}")
async def api_delete_training_label(label: str):
    """Delete all training data for a specific label."""
    count = training_data.delete_all_training_data(label=label)
    return {"status": "deleted", "label": label, "count": count}


@app.delete("/api/training-data")
async def api_delete_all_training():
    """Delete all training data."""
    count = training_data.delete_all_training_data()
    return {"status": "deleted", "count": count}


# ── Sample File Management API ───────────────────────────────────

@app.get("/api/data-files")
async def api_list_data_files():
    """List sample CSV files in /data/."""
    return {"files": training_data.list_sample_files()}


@app.delete("/api/data-files/{filename}")
async def api_delete_data_file(filename: str):
    """Delete a sample CSV file from /data/."""
    ok = training_data.delete_sample_file(filename)
    if ok:
        return {"status": "deleted", "filename": filename}
    return {"error": "File not found or not a .csv.sample file"}


@app.post("/api/data-files/promote")
async def api_promote_to_sample(request: Request):
    """Promote a training CSV to a sample file in /data/."""
    body = await request.json()
    label = body.get("label", "")
    filename = body.get("filename", "")
    if not label or not filename:
        return {"error": "label and filename are required"}

    new_name = training_data.promote_training_to_sample(label, filename)
    if new_name:
        return {"status": "promoted", "filename": new_name}
    return {"error": "Source file not found"}


@app.get("/api/training-data/{label}/{filename}/download")
async def api_download_training_file(label: str, filename: str):
    """Download a specific training CSV file."""
    file_path = training_data.get_training_file_path(label, filename)
    if file_path is None:
        return {"error": "File not found"}
    logger.info("Training file download: %s/%s", label, filename)
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/csv",
    )


@app.post("/api/training-data/{label}/upload")
async def api_upload_training_file(label: str, file: UploadFile = File(...)):
    """Upload a CSV file as training data for the given label."""
    if not file.filename:
        return {"error": "No file provided"}
    content = await file.read()
    try:
        saved_name = training_data.save_uploaded_training_file(label, file.filename, content)
    except ValueError as exc:
        return {"error": str(exc)}
    logger.info("Uploaded training file: %s/%s", label, saved_name)
    return {"status": "uploaded", "label": label, "filename": saved_name}


@app.get("/api/data-files/{filename}/download")
async def api_download_sample_file(filename: str):
    """Download a sample CSV file from /data/."""
    file_path = training_data.get_sample_file_path(filename)
    if file_path is None:
        return {"error": "File not found or not a .csv.sample file"}
    logger.info("Sample file download: %s", filename)
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/csv",
    )


@app.post("/api/data-files/upload")
async def api_upload_sample_file(file: UploadFile = File(...)):
    """Upload a .csv.sample file to /data/."""
    if not file.filename:
        return {"error": "No file provided"}
    content = await file.read()
    try:
        saved_name = training_data.save_uploaded_sample_file(file.filename, content)
    except ValueError as exc:
        return {"error": str(exc)}
    logger.info("Uploaded sample file: %s", saved_name)
    return {"status": "uploaded", "filename": saved_name}


# ── Model Training API (proxied to classifier service) ───────────

@app.post("/api/train")
async def api_train(request: Request):
    """Trigger model training on the classifier service."""
    result = await ClassifierClient.train()
    if result:
        return result
    return {"error": "Failed to start training — classifier service unreachable"}


@app.get("/api/train/status")
async def api_train_status():
    """Get training progress from the classifier service."""
    result = await ClassifierClient.train_status()
    if result:
        return result
    return {"error": "Failed to get training status — classifier service unreachable"}


@app.post("/api/upload-model")
async def api_upload_model(request: Request):
    """Upload a .joblib model file to the classifier service."""
    form = await request.form()
    file = form.get("file")
    if not file:
        return {"error": "No file uploaded"}

    result = await ClassifierClient.upload_model(file)
    if result:
        return result
    return {"error": "Failed to upload model — classifier service unreachable"}


@app.get("/api/model/info")
async def api_model_info():
    """Get model info from the classifier service."""
    result = await ClassifierClient.model_info()
    if result:
        return result
    return {"error": "Classifier service unreachable"}
