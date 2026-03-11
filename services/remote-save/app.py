"""FastAPI web service that creates a Dataverse record, optionally uploads
file content supplied in the request body, and returns the new record ID.

Dataverse connection details are configured via environment variables:
    DATAVERSE_ENV_URL, DATAVERSE_TABLE, DATAVERSE_COLUMN,
    DATAVERSE_TENANT_ID, DATAVERSE_CLIENT_ID, DATAVERSE_CLIENT_SECRET.

POST /save
    Body (JSON):
        name          – record name (required)
        data          – text content to store in the record (required)
        file_content  – raw bytes (base64-encoded) to upload to the file
                        column (optional)
        file_name     – filename for the file column upload (optional,
                        defaults to "{name}.txt")
        record_data   – dict of additional field values merged into the
                        new record (optional)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SCOPE_SUFFIX = "/.default"
SETTINGS_PATH = Path(os.environ.get("SETTINGS_DIR", "/data")) / "settings.json"

# ── Coffee type enum mapping (text → Dataverse option-set int) ──────────────
COFFEE_TYPE_MAP: dict[str, int] = {
    "black": 737200000,
    "espresso": 737200001,
    "cappuccino": 737200002,
}

app = FastAPI(
    title="Dataverse Upload Service",
    description="Pipeline stage that persists data to Microsoft Dataverse.",
    version="1.0.0",
)

# ── Settings persistence ────────────────────────────────────────────────────
_runtime: dict[str, Any] = {}

# All configuration items the service exposes via /settings.
# Entries with "secret": True are write-only: GET /settings returns "***set***"
# when they are populated and "" when they are not — the actual value is never
# included in the response.  Secrets can be updated via PATCH /settings but
# cannot be read back through the API.
_SETTINGS_REGISTRY: list[dict[str, Any]] = [
    # ── Dataverse environment ─────────────────────────────────────────────
    {
        "key": "DATAVERSE_ENV_URL", "name": "Dataverse Environment URL",
        "description": "Base URL of the Dataverse environment (e.g. https://<org>.crm.dynamics.com)",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_TABLE", "name": "Dataverse Table",
        "description": "Logical name of the Dataverse table to write records to",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_COLUMN", "name": "Dataverse File Column",
        "description": "Logical name of the file column for CSV uploads",
        "type": "str", "secret": False,
    },
    # ── Azure AD / app registration (write-only secrets) ─────────────────
    # These values control which Azure AD tenant and app registration are used
    # to authenticate against Dataverse.  They are stored in the runtime dict
    # and persisted to settings.json so that the service can be switched to a
    # different app registration or tenant without restarting the container.
    # They are NEVER returned in GET /settings responses — only a masked
    # placeholder is shown to indicate whether the value has been configured.
    {
        "key": "DATAVERSE_TENANT_ID", "name": "Azure AD Tenant ID",
        "description": "Azure Active Directory tenant ID for the app registration (write-only)",
        "type": "str", "secret": True,
    },
    {
        "key": "DATAVERSE_CLIENT_ID", "name": "Azure App Registration Client ID",
        "description": "Client ID of the Azure app registration used to authenticate with Dataverse (write-only)",
        "type": "str", "secret": True,
    },
    {
        "key": "DATAVERSE_CLIENT_SECRET", "name": "Azure App Registration Client Secret",
        "description": "Client secret of the Azure app registration (write-only, never returned in GET responses)",
        "type": "str", "secret": True,
    },
    # ── Dataverse column name overrides ──────────────────────────────────
    {
        "key": "DATAVERSE_COL_NAME", "name": "Column: Record Name",
        "description": "Dataverse column logical name for the record name field",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_COL_DATA", "name": "Column: Data Content",
        "description": "Dataverse column logical name for the data content field",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_COL_TEXT", "name": "Column: Generated Text",
        "description": "Dataverse column logical name for the generated comment text field",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_COL_CONFIDENCE", "name": "Column: Confidence Score",
        "description": "Dataverse column logical name for the classification confidence score field",
        "type": "str", "secret": False,
    },
    {
        "key": "DATAVERSE_COL_COFFEE_TYPE", "name": "Column: Coffee Type",
        "description": "Dataverse column logical name for the coffee type option-set field",
        "type": "str", "secret": False,
    },
]

# Set of keys whose values must never appear in GET /settings responses.
_SECRET_KEYS: set[str] = {e["key"] for e in _SETTINGS_REGISTRY if e.get("secret")}

# Default values for optional settings (column name overrides).
_DEFAULTS: dict[str, str] = {
    "DATAVERSE_COL_NAME": "jenssch_name",
    "DATAVERSE_COL_DATA": "jenssch_data",
    "DATAVERSE_COL_TEXT": "jenssch_text",
    "DATAVERSE_COL_CONFIDENCE": "jenssch_confidence",
    "DATAVERSE_COL_COFFEE_TYPE": "jenssch_type",
}


def _get_setting(key: str) -> str:
    """Return the runtime value for *key*, falling back to _DEFAULTS then ''.

    This treats the presence of a key in _runtime as authoritative, even if the
    value is an empty string, avoiding unintended fallback to defaults for
    falsy values explicitly set via PATCH /settings.
    """
    if key in _runtime:
        return str(_runtime[key])
    return _DEFAULTS.get(key, "")


def _load_settings() -> None:
    """Populate _runtime from environment variables, then overlay persisted JSON.

    Priority (highest wins): persisted settings.json > environment variables > defaults.
    Secret values (DATAVERSE_TENANT_ID, DATAVERSE_CLIENT_ID, DATAVERSE_CLIENT_SECRET)
    are loaded like any other setting so that the service can be reconfigured at
    runtime without restarting the container.
    """
    for entry in _SETTINGS_REGISTRY:
        key = entry["key"]
        _runtime[key] = os.environ.get(key, _DEFAULTS.get(key, ""))

    if SETTINGS_PATH.exists():
        try:
            persisted = json.loads(SETTINGS_PATH.read_text())
            for entry in _SETTINGS_REGISTRY:
                key = entry["key"]
                if key in persisted:
                    _runtime[key] = str(persisted[key])
        except (json.JSONDecodeError, OSError):
            pass


def _save_settings() -> None:
    """Atomically persist runtime settings to *SETTINGS_PATH*.

    Writes to a sibling temp file first, then uses :func:`os.replace` to
    swap it into place.  This guarantees that a reader always sees either
    the old complete file or the new complete file — never a partial write.
    """
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=SETTINGS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(_runtime, indent=2) + "\n")
        os.replace(tmp_name, SETTINGS_PATH)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@app.on_event("startup")
async def _startup():
    _load_settings()


# ── Configuration from environment ──────────────────────────────────────────
def _env(name: str) -> str:
    """Return an environment variable or raise at startup."""
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# ── Request / Response models ───────────────────────────────────────────────
class SaveRequest(BaseModel):
    name: str = Field(..., description="Record name")
    data: str = Field("", description="Text content to store in the record")
    text: str = Field("", description="Text content for the jenssch_text column")
    confidence: float = Field(0.0, description="Classification confidence score")
    coffee_type: str = Field(
        ...,
        description="Coffee type (Black, Espresso, or Cappuccino)",
    )
    sensor_data: list[dict[str, Any]] | None = Field(
        None,
        description="Raw sensor data array — service converts to CSV + base64 automatically",
    )
    file_content: str | None = Field(
        None,
        description="Base64-encoded file bytes to upload to the file column (legacy)",
    )
    file_name: str | None = Field(
        None,
        description="Filename for the file-column upload (defaults to '<name>.txt')",
    )
    record_data: dict[str, Any] | None = Field(
        None,
        description="Additional field values merged into the new record",
    )


class SaveResponse(BaseModel):
    record_id: str
    message: str


# ── Dataverse helpers (ported from upload_to_dataverse.py) ──────────────────
def get_token(tenant_id: str, client_id: str, client_secret: str, resource: str) -> str:
    """Authenticate with Azure AD and return an access token."""
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    body = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": f"{resource}{DEFAULT_SCOPE_SUFFIX}",
        "grant_type": "client_credentials",
    }
    response = requests.post(token_url, data=body, timeout=30)
    response.raise_for_status()
    return response.json()["access_token"]


def create_record(
    environment_url: str,
    table: str,
    token: str,
    record_data: dict[str, Any] | None = None,
) -> str:
    """Create a new record and return its ID."""
    api_base = environment_url.rstrip("/")
    url = f"{api_base}/api/data/v9.2/{table}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
        "Prefer": "return=representation",
    }

    response = requests.post(url, json=record_data or {}, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    for key, value in data.items():
        if key.endswith("id") and isinstance(value, str) and len(value) == 36:
            return value

    entity_id = response.headers.get("OData-EntityId", "")
    return entity_id.split("(")[-1].rstrip(")")


def upload_file(
    environment_url: str,
    table: str,
    record_id: str,
    column: str,
    file_path: Path,
    token: str,
) -> None:
    """Upload file bytes into the file column of the given record."""
    api_base = environment_url.rstrip("/")
    url = f"{api_base}/api/data/v9.2/{table}({record_id})/{column}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "x-ms-file-name": file_path.name,
    }

    with file_path.open("rb") as payload:
        response = requests.patch(url, data=payload, headers=headers, timeout=120)
    response.raise_for_status()


# ── CSV helpers ─────────────────────────────────────────────────────────────

_CSV_COLUMNS = ["label", "elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]


def _sensor_data_to_csv(sensor_data: list[dict[str, Any]], label: str) -> str:
    """Convert raw sensor dicts to a CSV string with the classification label."""
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in sensor_data:
        writer.writerow({"label": label, **row})
    return buf.getvalue()


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/manifest")
def manifest() -> dict:
    return {
        "name": "remote-save",
        "version": "1.0.0",
        "description": "Persist brew results to Microsoft Dataverse",
        "inputs": [
            {"name": "name", "type": "string", "required": True, "description": "Record name"},
            {"name": "coffee_type", "type": "string", "required": True, "description": "Coffee type label"},
            {"name": "confidence", "type": "float", "required": True, "description": "Classification confidence"},
            {"name": "text", "type": "string", "required": False, "description": "Generated comment text"},
            {"name": "sensor_data", "type": "array", "required": False,
             "description": "Raw sensor data for CSV upload"},
        ],
        "outputs": [
            {"name": "record_id", "type": "string", "description": "Dataverse record ID"},
        ],
        "endpoints": {
            "execute": {"method": "POST", "path": "/save"},
            "health": {"method": "GET", "path": "/health"},
            "settings": {"method": "GET", "path": "/settings"},
            "update_settings": {"method": "PATCH", "path": "/settings"},
        },
        "failure_modes": ["skip"],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/save", response_model=SaveResponse)
def save(req: SaveRequest) -> SaveResponse:
    """Create a Dataverse record and optionally upload file content."""
    # Read all settings from _runtime (env vars loaded at startup; can be
    # overridden at runtime via PATCH /settings without restarting the service).
    env_url = _runtime.get("DATAVERSE_ENV_URL", "")
    table = _runtime.get("DATAVERSE_TABLE", "")
    column = _runtime.get("DATAVERSE_COLUMN", "")
    tenant_id = _runtime.get("DATAVERSE_TENANT_ID", "")
    client_id = _runtime.get("DATAVERSE_CLIENT_ID", "")
    client_secret = _runtime.get("DATAVERSE_CLIENT_SECRET", "")

    missing = [k for k, v in [
        ("DATAVERSE_ENV_URL", env_url),
        ("DATAVERSE_TABLE", table),
        ("DATAVERSE_COLUMN", column),
        ("DATAVERSE_TENANT_ID", tenant_id),
        ("DATAVERSE_CLIENT_ID", client_id),
        ("DATAVERSE_CLIENT_SECRET", client_secret),
    ] if not v]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"Missing required configuration: {', '.join(missing)}",
        )

    # Column names (configurable via PATCH /settings)
    col_name = _get_setting("DATAVERSE_COL_NAME")
    col_data = _get_setting("DATAVERSE_COL_DATA")
    col_text = _get_setting("DATAVERSE_COL_TEXT")
    col_confidence = _get_setting("DATAVERSE_COL_CONFIDENCE")
    col_coffee_type = _get_setting("DATAVERSE_COL_COFFEE_TYPE")

    # Convert coffee_type text to integer
    coffee_key = req.coffee_type.strip().lower()
    logger.info("Received coffee_type: '%s' (normalised: '%s')", req.coffee_type, coffee_key)
    if coffee_key not in COFFEE_TYPE_MAP:
        valid = ", ".join(COFFEE_TYPE_MAP.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Invalid coffee_type '{req.coffee_type}'. Valid values: {valid}",
        )
    coffee_type_value = COFFEE_TYPE_MAP[coffee_key]
    logger.info("Mapped coffee_type '%s' -> %d", coffee_key, coffee_type_value)

    # Build record payload
    record_payload: dict[str, Any] = dict(req.record_data) if req.record_data else {}
    record_payload.setdefault(col_name, req.name)
    record_payload.setdefault(col_data, req.data)
    record_payload.setdefault(col_text, req.text)
    record_payload.setdefault(col_confidence, req.confidence)
    record_payload.setdefault(col_coffee_type, coffee_type_value)

    # Authenticate
    try:
        token = get_token(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            resource=env_url.rstrip("/"),
        )
    except requests.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to authenticate with Azure AD: {exc}",
        ) from exc

    # Create record
    try:
        record_id = create_record(
            environment_url=env_url,
            table=table,
            token=token,
            record_data=record_payload,
        )
    except requests.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to create Dataverse record: {exc}",
        ) from exc

    # Upload file content if provided (or auto-generate from sensor_data)
    file_content = req.file_content
    file_name = req.file_name

    if not file_content and req.sensor_data:
        csv_str = _sensor_data_to_csv(req.sensor_data, coffee_key)
        data_field = csv_str  # also store CSV in the data column
        file_content = base64.b64encode(csv_str.encode("utf-8")).decode("ascii")
        file_name = file_name or f"{req.name}.csv"
        # Update data column with CSV if it was empty
        if not req.data:
            record_payload[col_data] = data_field

    if file_content:
        file_name = file_name or f"{req.name}.txt"
        try:
            file_bytes = base64.b64decode(file_content)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"file_content is not valid base64: {exc}",
            ) from exc

        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=f"_{file_name}"
            ) as tmp:
                tmp.write(file_bytes)
                tmp_path = Path(tmp.name)

            upload_file(
                environment_url=env_url,
                table=table,
                record_id=record_id,
                column=column,
                file_path=tmp_path,
                token=token,
            )
        except requests.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to upload file to Dataverse: {exc}",
            ) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

    return SaveResponse(
        record_id=record_id,
        message=f"Record created in {table} with id {record_id}",
    )


# ── Settings ────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


@app.get("/settings")
def get_settings():
    """Return all runtime settings.

    Secret values (DATAVERSE_TENANT_ID, DATAVERSE_CLIENT_ID,
    DATAVERSE_CLIENT_SECRET) are never included in the response.
    Instead, a placeholder is returned:
      - ``"***set***"`` — the value is configured (but not readable via API)
      - ``""``          — the value has not been set
    """
    result = []
    for entry in _SETTINGS_REGISTRY:
        key = entry["key"]
        raw = _runtime.get(key, "")
        if key in _SECRET_KEYS:
            value = "***set***" if raw else ""
        else:
            value = raw
        result.append({**entry, "value": value})
    return result


# Keys accepted by PATCH /settings (all registered keys).
_VALID_SETTING_KEYS: set[str] = {e["key"] for e in _SETTINGS_REGISTRY}


@app.patch("/settings")
def update_settings(req: SettingsUpdate):
    """Update one or more runtime settings.

    All keys in the settings registry are accepted, including secrets.
    Secret values (DATAVERSE_TENANT_ID, DATAVERSE_CLIENT_ID,
    DATAVERSE_CLIENT_SECRET) are stored but never returned in GET /settings
    responses.  This allows switching the Dataverse environment or Azure app
    registration without code changes or container restarts.

    Unknown keys are silently ignored.  Changes are persisted to settings.json
    inside the container's /data volume so they survive container restarts.
    """
    updated = []
    for key, value in req.settings.items():
        if key not in _VALID_SETTING_KEYS:
            continue
        _runtime[key] = str(value)
        updated.append(key)
    _save_settings()
    return {"updated": updated}
