"""Create a new record in Microsoft Dataverse and upload a local file to
its file column, authenticating with a service principal.

Usage example:
    python upload_to_dataverse.py \
        --environment-url https://org.crm.dynamics.com \
        --table logicaltablename \
        --column filecolumnlogicalname \
        --file data/example.csv \
        --tenant-id <tenant> --client-id <app> --client-secret <secret>

    Optionally pass initial field values as JSON:
        --record-data '{"name": "Run 1", "description": "test"}'

Environment variables (optional overrides):
    DATAVERSE_ENV_URL, DATAVERSE_TABLE, DATAVERSE_COLUMN,
    DATAVERSE_TENANT_ID, DATAVERSE_CLIENT_ID, DATAVERSE_CLIENT_SECRET.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Final

import requests

DEFAULT_SCOPE_SUFFIX: Final[str] = "/.default"


def get_token(tenant_id: str, client_id: str, client_secret: str, resource: str) -> str:
    """Authenticate with Azure AD and return an access token for Dataverse."""
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": f"{resource}{DEFAULT_SCOPE_SUFFIX}",
        "grant_type": "client_credentials",
    }
    response = requests.post(token_url, data=data, timeout=30)
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

    body = record_data or {}
    response = requests.post(url, json=body, headers=headers, timeout=30)
    response.raise_for_status()

    # The new record ID comes back in the OData-EntityId header or in the body.
    data = response.json()
    # Primary key is usually <tablename>id – grab the first GUID key returned.
    for key, value in data.items():
        if key.endswith("id") and isinstance(value, str) and len(value) == 36:
            return value
    # Fallback: parse from the OData-EntityId header
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
    """Stream the file bytes into the file column of the given record."""
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a file to Dataverse")
    parser.add_argument("--environment-url", default=os.getenv("DATAVERSE_ENV_URL"), required=False)
    parser.add_argument("--table", default=os.getenv("DATAVERSE_TABLE"), required=False)
    parser.add_argument("--column", default=os.getenv("DATAVERSE_COLUMN"), required=False)
    parser.add_argument(
        "--record-data",
        default=None,
        help='Optional JSON string of field values for the new record, e.g. \'{"name": "run1"}\'')
    parser.add_argument(
        "--file",
        dest="file_path",
        default=os.getenv("DATAVERSE_FILE_PATH"),
        required=False,
        help="Path to the local file under ./data",
    )
    parser.add_argument("--tenant-id", default=os.getenv("DATAVERSE_TENANT_ID"), required=False)
    parser.add_argument("--client-id", default=os.getenv("DATAVERSE_CLIENT_ID"), required=False)
    parser.add_argument("--client-secret", default=os.getenv("DATAVERSE_CLIENT_SECRET"), required=False)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    missing = [
        ("environment-url", args.environment_url),
        ("table", args.table),
        ("column", args.column),
        ("file", args.file_path),
        ("tenant-id", args.tenant_id),
        ("client-id", args.client_id),
        ("client-secret", args.client_secret),
    ]

    undefined = [name for name, value in missing if not value]
    if undefined:
        joined = ", ".join(undefined)
        raise SystemExit(f"Missing required arguments: {joined}")

    file_path = Path(args.file_path).resolve()
    if not file_path.exists():
        raise SystemExit(f"File not found: {file_path}")

    record_data: dict[str, Any] = {}
    if args.record_data:
        record_data = json.loads(args.record_data)
    record_data.setdefault("jenssch_name", file_path.stem)
    record_data.setdefault("jenssch_data", file_path.read_text(encoding="utf-8"))

    token = get_token(
        tenant_id=args.tenant_id,
        client_id=args.client_id,
        client_secret=args.client_secret,
        resource=args.environment_url.rstrip("/"),
    )

    record_id = create_record(
        environment_url=args.environment_url,
        table=args.table,
        token=token,
        record_data=record_data,
    )
    print(f"Created record {record_id}")

    upload_file(
        environment_url=args.environment_url,
        table=args.table,
        record_id=record_id,
        column=args.column,
        file_path=file_path,
        token=token,
    )

    print(f"Uploaded {file_path.name} to {args.table}({record_id}).{args.column}")


if __name__ == "__main__":
    main()
