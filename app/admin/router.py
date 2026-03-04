"""Admin web interface – routes for login, dashboard, and settings."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from config import _DESCRIPTIONS, config

logger = logging.getLogger("rpicoffee.admin")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Keys that can be edited from the admin UI
_EDITABLE_KEYS = [
    "LLM_ENABLED", "LLM_BACKEND", "LLM_ENDPOINT", "LLM_OLLAMA_ENDPOINT", "LLM_MODEL",
    "TTS_ENABLED", "TTS_ENDPOINT",
    "CLASSIFIER_ENABLED", "CLASSIFIER_ENDPOINT",
    "REMOTE_SAVE_ENABLED", "REMOTE_SAVE_ENDPOINT",
    "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TOP_P", "LLM_TTS", "LLM_KEEP_ALIVE",
    "SENSOR_MODE", "SENSOR_DEVICE_ID", "SENSOR_SERIAL_PORT",
    "SENSOR_SAMPLE_RATE_HZ", "SENSOR_DURATION_S",
    "SENSOR_VIBRATION_THRESHOLD", "SENSOR_RMS_WINDOW_S", "SENSOR_AUTO_TRIGGER",
    "SENSOR_TRIGGER_SOURCES", "SENSOR_TRIGGER_COMBINE_MODE",
    "SENSOR_GYRO_THRESHOLD", "SENSOR_GYRO_RMS_WINDOW_S",
    "SENSOR_ACC_RANGE_G", "SENSOR_GYRO_RANGE_DPS", "SENSOR_FILTER_HZ",
    "SENSOR_WARMUP_S",
    "SENSOR_COOLDOWN_S",
    "DATA_COLLECT_ENABLED", "DATA_COLLECT_LABEL", "DATA_COLLECT_LABELS",
    "VIRTUAL_KEYBOARD_ENABLED",
]

_BOOL_KEYS = {"LLM_ENABLED", "TTS_ENABLED", "CLASSIFIER_ENABLED", "SENSOR_AUTO_TRIGGER", "LLM_TTS",
              "REMOTE_SAVE_ENABLED", "DATA_COLLECT_ENABLED", "VIRTUAL_KEYBOARD_ENABLED"}


def _get_signer() -> URLSafeSerializer:
    return URLSafeSerializer(config.SECRET_KEY)


def _verify_session(session: str | None) -> bool:
    """Check session token is valid (any age)."""
    if not session:
        return False
    try:
        data = _get_signer().loads(session)
        return data.get("authenticated") is True
    except BadSignature:
        return False


def _verify_session_fresh(session: str | None, max_age: float = 10.0) -> bool:
    """Check session token is valid AND was issued within *max_age* seconds."""
    if not session:
        return False
    try:
        data = _get_signer().loads(session)
        if data.get("authenticated") is not True:
            return False
        ts = data.get("ts", 0)
        return (time.time() - ts) < max_age
    except BadSignature:
        return False


def _make_session_token() -> str:
    """Create a fresh session token with a timestamp."""
    return _get_signer().dumps({"authenticated": True, "ts": time.time()})


# ── Login ────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error, "config": config.to_dict()})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if config.verify_password(password):
        response = RedirectResponse(url="/admin/", status_code=303)
        response.set_cookie(key="session", value=_make_session_token(),
                            httponly=True, samesite="lax", max_age=600)
        logger.info("Admin login successful")
        return response
    logger.warning("Admin login failed – wrong password")
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password", "config": config.to_dict()})


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session")
    return response


# ── Dashboard ────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: str | None = Cookie(default=None)):
    if not _verify_session_fresh(session):
        return RedirectResponse(url="/admin/login", status_code=303)

    cfg = config.to_dict()
    # Don't show secrets in UI
    cfg.pop("SECRET_KEY", None)
    cfg.pop("ADMIN_PASSWORD_HASH", None)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "config": cfg,
        "editable_keys": _EDITABLE_KEYS,
        "bool_keys": _BOOL_KEYS,
        "descriptions": _DESCRIPTIONS,
        "message": request.query_params.get("message", ""),
    })


# Keys whose change requires a sensor restart
_SENSOR_KEYS = {"SENSOR_MODE", "SENSOR_DEVICE_ID", "SENSOR_SAMPLE_RATE_HZ",
                "SENSOR_DURATION_S", "SENSOR_VIBRATION_THRESHOLD", "SENSOR_RMS_WINDOW_S",
                "SENSOR_AUTO_TRIGGER", "SENSOR_ACC_RANGE_G", "SENSOR_GYRO_RANGE_DPS",
                "SENSOR_FILTER_HZ", "SENSOR_TRIGGER_SOURCES", "SENSOR_TRIGGER_COMBINE_MODE",
                "SENSOR_GYRO_THRESHOLD", "SENSOR_GYRO_RMS_WINDOW_S", "SENSOR_WARMUP_S",
                "SENSOR_COOLDOWN_S"}


# ── Settings update ─────────────────────────────────────────────

@router.post("/settings")
async def update_settings(request: Request, session: str | None = Cookie(default=None)):
    if not _verify_session(session):
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    updates: dict = {}

    # Snapshot current config for change detection
    old_backend = config.get("LLM_BACKEND")
    old_sensor = {k: config.get(k) for k in _SENSOR_KEYS}

    for key in _EDITABLE_KEYS:
        if key in _BOOL_KEYS:
            # Checkboxes: present = true, absent = false
            updates[key] = key in form
        elif key in form:
            updates[key] = form[key]

    config.update_many(updates)
    logger.info("Settings updated: %s", list(updates.keys()))

    # If LLM backend changed, start/stop hailo-ollama service
    new_backend = config.get("LLM_BACKEND")
    if old_backend != new_backend:
        from services.hailo_ollama_manager import start_and_enable, stop_and_disable
        if new_backend == "ollama":
            logger.info("LLM backend → ollama: starting hailo-ollama service")
            try:
                healthy = await start_and_enable()
                if not healthy:
                    logger.warning("hailo-ollama started but health check did not pass")
            except Exception:
                logger.exception("Failed to start hailo-ollama service")
        else:
            logger.info("LLM backend → %s: stopping hailo-ollama service", new_backend)
            try:
                await stop_and_disable()
            except Exception:
                logger.exception("Failed to stop hailo-ollama service")

    # If any sensor-related setting changed, restart the sensor
    new_sensor = {k: config.get(k) for k in _SENSOR_KEYS}
    if old_sensor != new_sensor:
        logger.info("Sensor settings changed – restarting sensor")
        from main import restart_sensor
        try:
            await restart_sensor()
        except Exception:
            logger.exception("Failed to restart sensor after settings change")

    return _redirect_with_fresh_session("/admin/?message=Settings+saved")


def _redirect_with_fresh_session(url: str) -> RedirectResponse:
    """Redirect and set a fresh session cookie so the dashboard accepts the request."""
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie(key="session", value=_make_session_token(),
                        httponly=True, samesite="lax", max_age=600)
    return response


# ── Sensor config (JSON API) ────────────────────────────────────

_SENSOR_CONFIG_KEYS = [
    "SENSOR_DEVICE_ID", "SENSOR_SAMPLE_RATE_HZ", "SENSOR_DURATION_S",
    "SENSOR_VIBRATION_THRESHOLD", "SENSOR_RMS_WINDOW_S",
    "SENSOR_ACC_RANGE_G", "SENSOR_GYRO_RANGE_DPS", "SENSOR_FILTER_HZ",
    "SENSOR_CHART_WINDOW_S", "SENSOR_ACC_ENABLED", "SENSOR_GYRO_ENABLED",
    "SENSOR_NEUTRALIZE_GRAVITY",
    "SENSOR_TRIGGER_SOURCES", "SENSOR_TRIGGER_COMBINE_MODE",
    "SENSOR_GYRO_THRESHOLD", "SENSOR_GYRO_RMS_WINDOW_S",
    "SENSOR_WARMUP_S",
    "SENSOR_COOLDOWN_S",
]


@router.post("/sensor-config")
async def update_sensor_config(request: Request, session: str | None = Cookie(default=None)):
    """Save sensor-specific settings via JSON and restart the sensor."""
    if not _verify_session(session):
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    body = await request.json()
    updates: dict = {}
    for key in _SENSOR_CONFIG_KEYS:
        if key in body:
            updates[key] = body[key]

    if not updates:
        return JSONResponse({"error": "No valid keys"}, status_code=400)

    config.update_many(updates)
    logger.info("Sensor config updated: %s", list(updates.keys()))

    return JSONResponse({"ok": True, "updated": list(updates.keys())})


# ── Password change ──────────────────────────────────────────

@router.post("/password")
async def change_password(
    request: Request,
    session: str | None = Cookie(default=None),
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    if not _verify_session(session):
        return RedirectResponse(url="/admin/login", status_code=303)

    if not config.verify_password(current_password):
        return _redirect_with_fresh_session("/admin/?message=Current+password+is+incorrect")

    if len(new_password) < 4:
        return _redirect_with_fresh_session("/admin/?message=Password+must+be+at+least+4+characters")

    config.set_password(new_password)
    logger.info("Admin password changed")
    return _redirect_with_fresh_session("/admin/?message=Password+changed")
