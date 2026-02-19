"""Admin web interface – routes for login, dashboard, and settings."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Cookie, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from config import config

logger = logging.getLogger("rpicoffee.admin")

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Keys that can be edited from the admin UI
_EDITABLE_KEYS = [
    "ADMIN_PASSWORD",
    "LOCALLM_ENABLED", "LOCALLM_ENDPOINT",
    "LOCALTTS_ENABLED", "LOCALTTS_ENDPOINT",
    "LOCALML_ENABLED", "LOCALML_ENDPOINT",
    "REMOTE_SAVE_ENABLED", "REMOTE_SAVE_ENDPOINT",
    "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "LLM_TOP_P", "LLM_TTS",
    "SENSOR_MOCK_ENABLED", "SENSOR_SERIAL_PORT",
    "SENSOR_SAMPLE_RATE_HZ", "SENSOR_DURATION_S",
]

_BOOL_KEYS = {"LOCALLM_ENABLED", "LOCALTTS_ENABLED", "LOCALML_ENABLED", "SENSOR_MOCK_ENABLED", "LLM_TTS", "REMOTE_SAVE_ENABLED"}


def _get_signer() -> URLSafeSerializer:
    return URLSafeSerializer(config.SECRET_KEY)


def _verify_session(session: str | None) -> bool:
    if not session:
        return False
    try:
        data = _get_signer().loads(session)
        return data.get("authenticated") is True
    except BadSignature:
        return False


# ── Login ────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password == str(config.ADMIN_PASSWORD):
        response = RedirectResponse(url="/admin/", status_code=303)
        token = _get_signer().dumps({"authenticated": True})
        response.set_cookie(key="session", value=token, httponly=True, samesite="lax")
        logger.info("Admin login successful")
        return response
    logger.warning("Admin login failed – wrong password")
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("session")
    return response


# ── Dashboard ────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: str | None = Cookie(default=None)):
    if not _verify_session(session):
        return RedirectResponse(url="/admin/login", status_code=303)

    cfg = config.to_dict()
    # Don't show secret key in UI
    cfg.pop("SECRET_KEY", None)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "config": cfg,
        "editable_keys": _EDITABLE_KEYS,
        "bool_keys": _BOOL_KEYS,
        "message": request.query_params.get("message", ""),
    })


# ── Settings update ─────────────────────────────────────────────

@router.post("/settings")
async def update_settings(request: Request, session: str | None = Cookie(default=None)):
    if not _verify_session(session):
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    updates: dict = {}

    for key in _EDITABLE_KEYS:
        if key in _BOOL_KEYS:
            # Checkboxes: present = true, absent = false
            updates[key] = key in form
        elif key in form:
            updates[key] = form[key]

    config.update_many(updates)
    logger.info("Settings updated: %s", list(updates.keys()))

    return RedirectResponse(url="/admin/?message=Settings+saved", status_code=303)
