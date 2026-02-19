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
    data: str = Field(..., description="Text content to store in the record")
    text: str = Field(..., description="Text content for the jenssch_text column")
    confidence: float = Field(..., description="Classification confidence score")
    coffee_type: str = Field(
        ...,
        description="Coffee type (Black, Espresso, or Cappuccino)",
    )
    file_content: str | None = Field(
        None,
        description="Base64-encoded file bytes to upload to the file column",
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


# ── Endpoints ───────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/save", response_model=SaveResponse)
def save(req: SaveRequest) -> SaveResponse:
    """Create a Dataverse record and optionally upload file content."""
    try:
        env_url = _env("DATAVERSE_ENV_URL")
        table = _env("DATAVERSE_TABLE")
        column = _env("DATAVERSE_COLUMN")
        tenant_id = _env("DATAVERSE_TENANT_ID")
        client_id = _env("DATAVERSE_CLIENT_ID")
        client_secret = _env("DATAVERSE_CLIENT_SECRET")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Column names (configurable via env vars)
    col_name = os.getenv("DATAVERSE_COL_NAME", "jenssch_name")
    col_data = os.getenv("DATAVERSE_COL_DATA", "jenssch_data")
    col_text = os.getenv("DATAVERSE_COL_TEXT", "jenssch_text")
    col_confidence = os.getenv("DATAVERSE_COL_CONFIDENCE", "jenssch_confidence")
    col_coffee_type = os.getenv("DATAVERSE_COL_COFFEE_TYPE", "jenssch_type")

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

    # Upload file content if provided
    if req.file_content:
        file_name = req.file_name or f"{req.name}.txt"
        try:
            file_bytes = base64.b64decode(req.file_content)
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
