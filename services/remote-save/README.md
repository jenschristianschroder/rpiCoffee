# rpiCoffee — Remote Save Service

Dockerized FastAPI service that persists brew results and raw sensor data to Microsoft Dataverse. Acts as the final stage of the brew pipeline — receives processed results via a POST request and creates a new Dataverse record.

## Overview

After the main app completes a brew (sense → classify → comment → speak), it sends the result to this service for cloud persistence. The service authenticates to Dataverse using an Azure AD service principal and creates a record with the coffee type, confidence score, generated text, and optionally the raw sensor CSV as a file attachment.

This service is **optional** — the brew pipeline runs fine without it.

## Prerequisites

- Docker
- A Microsoft Dataverse environment with an existing table
- An Azure AD app registration (service principal) with Dataverse API permissions

## Environment Variables

Connection details can be supplied via environment variables or a `.env` file in the project root. Copy the template to get started:

```bash
cp .env.example .env
# Fill in your values
```

All connection details are supplied via environment variables:

| Variable | Description | Example |
|---|---|---|
| `DATAVERSE_ENV_URL` | Dataverse environment URL | `https://org.crm.dynamics.com` |
| `DATAVERSE_TABLE` | Logical name of the target table | `jenssch_mytable` |
| `DATAVERSE_COLUMN` | Logical name of the file column | `jenssch_filecol` |
| `DATAVERSE_TENANT_ID` | Azure AD tenant ID | `xxxxxxxx-xxxx-...` |
| `DATAVERSE_CLIENT_ID` | App registration client ID | `xxxxxxxx-xxxx-...` |
| `DATAVERSE_CLIENT_SECRET` | App registration client secret | `********` |
| `DATAVERSE_COL_NAME` | Column for record name (optional) | `jenssch_name` |
| `DATAVERSE_COL_DATA` | Column for data content (optional) | `jenssch_data` |
| `DATAVERSE_COL_TEXT` | Column for text content (optional) | `jenssch_text` |
| `DATAVERSE_COL_CONFIDENCE` | Column for confidence score (optional) | `jenssch_confidence` |
| `DATAVERSE_COL_COFFEE_TYPE` | Column for coffee type (optional) | `jenssch_type` |

> **Sensitive values** (`DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`) can also be set or updated at runtime via `PATCH /settings` without restarting the container. They are **never** returned in `GET /settings` responses — only a masked placeholder is shown.

## Build & Run

### Local (without Docker)

```bash
pip install -r requirements.txt
# Ensure .env is populated or env vars are exported
uvicorn app:app --host 0.0.0.0 --port 7000
```

### Docker

With individual environment variables:

```bash
docker build -t dataverse-saver .

docker run -p 7000:7000 \
  -e DATAVERSE_ENV_URL=https://org.crm.dynamics.com \
  -e DATAVERSE_TABLE=jenssch_mytable \
  -e DATAVERSE_COLUMN=jenssch_filecol \
  -e DATAVERSE_TENANT_ID=<tenant-id> \
  -e DATAVERSE_CLIENT_ID=<client-id> \
  -e DATAVERSE_CLIENT_SECRET=<client-secret> \
  dataverse-saver
```

Or with a `.env` file:

```bash
docker run -p 7000:7000 --env-file .env dataverse-saver
```

The service listens on **port 7000**.

## API Endpoints

### `GET /health`

Health check endpoint.

**Response:**
```json
{ "status": "ok" }
```

---

### `POST /save`

Creates a new record in the configured Dataverse table. Optionally uploads binary content to the table's file column.

#### Request Body (JSON)

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | **Yes** | Record name → mapped to `jenssch_name` |
| `data` | `string` | **Yes** | Data content → mapped to `jenssch_data` |
| `text` | `string` | **Yes** | Text content → mapped to `jenssch_text` |
| `confidence` | `float` | **Yes** | Classification confidence score → mapped to `jenssch_confidence` |
| `coffee_type` | `string` | **Yes** | Coffee type (`Black`, `Espresso`, or `Cappuccino`) → converted to int and mapped to `jenssch_coffeetype` |
| `file_content` | `string` | No | Base64-encoded file bytes to upload to the file column |
| `file_name` | `string` | No | Filename for the upload (defaults to `<name>.txt`) |
| `record_data` | `object` | No | Additional key-value pairs merged into the new record |

#### Dataverse Column Mapping

| Request Field | Dataverse Column | Env Var Override |
|---|---|---|
| `name` | `jenssch_name` | `DATAVERSE_COL_NAME` |
| `data` | `jenssch_data` | `DATAVERSE_COL_DATA` |
| `text` | `jenssch_text` | `DATAVERSE_COL_TEXT` |
| `confidence` | `jenssch_confidence` | `DATAVERSE_COL_CONFIDENCE` |
| `coffee_type` | `jenssch_coffeetype` | `DATAVERSE_COL_COFFEE_TYPE` |

#### Coffee Type Values

| Text Input | Dataverse Value |
|---|---|
| `Black` | `737200000` |
| `Espresso` | `737200001` |
| `Cappuccino` | `737200002` |

#### Example Request

```bash
curl -X POST http://localhost:7000/save \
  -H "Content-Type: application/json" \
  -d '{
    "name": "run-2026-02-17",
    "data": "processed CSV content here...",
    "text": "extracted text from document",
    "confidence": 0.95,
    "coffee_type": "Espresso"
  }'
```

#### Example Request with File Upload

```bash
curl -X POST http://localhost:7000/save \
  -H "Content-Type: application/json" \
  -d '{
    "name": "run-2026-02-17",
    "data": "processed CSV content",
    "text": "extracted text",
    "confidence": 0.87,
    "coffee_type": "Black",
    "file_content": "SGVsbG8gV29ybGQ=",
    "file_name": "output.csv"
  }'
```

#### Example Request with Additional Fields

```bash
curl -X POST http://localhost:7000/save \
  -H "Content-Type: application/json" \
  -d '{
    "name": "run-2026-02-17",
    "data": "processed data",
    "text": "extracted text",
    "confidence": 0.92,
    "coffee_type": "Cappuccino",
    "record_data": {
      "jenssch_description": "Pipeline run output",
      "jenssch_source": "batch-processor"
    }
  }'
```

#### Success Response

```json
{
  "record_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "message": "Record created in jenssch_mytable with id a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### Error Responses

| Status | Cause |
|---|---|
| `400` | Invalid base64 in `file_content` |
| `422` | Missing or invalid required fields |
| `500` | Missing environment variables |
| `502` | Azure AD authentication or Dataverse API failure |

### `GET /settings`

Returns the current runtime settings for the remote-save service.

**Sensitive values** (`DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`) are **never** returned as plain text. Instead, the `value` field contains:
- `"***set***"` — the value is configured and the service will use it
- `""` — the value has not been set

This allows operators to confirm that credentials are loaded without exposing them.

#### Response

```json
[
  {
    "key": "DATAVERSE_ENV_URL",
    "name": "Dataverse Environment URL",
    "description": "Base URL of the Dataverse environment (e.g. https://<org>.crm.dynamics.com)",
    "type": "str",
    "secret": false,
    "value": "https://org.crm.dynamics.com"
  },
  {
    "key": "DATAVERSE_TABLE",
    "name": "Dataverse Table",
    "description": "Logical name of the Dataverse table to write records to",
    "type": "str",
    "secret": false,
    "value": "jenssch_mytable"
  },
  {
    "key": "DATAVERSE_COLUMN",
    "name": "Dataverse File Column",
    "description": "Logical name of the file column for CSV uploads",
    "type": "str",
    "secret": false,
    "value": "jenssch_file"
  },
  {
    "key": "DATAVERSE_TENANT_ID",
    "name": "Azure AD Tenant ID",
    "description": "Azure Active Directory tenant ID for the app registration (write-only)",
    "type": "str",
    "secret": true,
    "value": "***set***"
  },
  {
    "key": "DATAVERSE_CLIENT_ID",
    "name": "Azure App Registration Client ID",
    "description": "Client ID of the Azure app registration used to authenticate with Dataverse (write-only)",
    "type": "str",
    "secret": true,
    "value": "***set***"
  },
  {
    "key": "DATAVERSE_CLIENT_SECRET",
    "name": "Azure App Registration Client Secret",
    "description": "Client secret of the Azure app registration (write-only, never returned in GET responses)",
    "type": "str",
    "secret": true,
    "value": "***set***"
  },
  {
    "key": "DATAVERSE_COL_NAME",
    "name": "Column: Record Name",
    "description": "Dataverse column logical name for the record name field",
    "type": "str",
    "secret": false,
    "value": "jenssch_name"
  }
]
```

### `PATCH /settings`

Update one or more runtime settings. All keys listed in the settings registry are accepted, including sensitive (secret) ones. Unknown keys are silently ignored. Changes are persisted to `settings.json` inside the container's `/data` volume so they survive container restarts.

> **Security warning:** This endpoint is a privileged management surface. It **MUST** be protected with strong authentication/authorization (for example, an admin-only API key, mTLS, or a properly configured reverse proxy) and **MUST NOT** be exposed to untrusted networks or other pods/services without equivalent protection. In production you should either restrict access to a trusted admin network or disable this endpoint entirely.

**Sensitive values** (`DATAVERSE_TENANT_ID`, `DATAVERSE_CLIENT_ID`, `DATAVERSE_CLIENT_SECRET`) can be set via this endpoint but are **never** returned in `GET /settings` responses — only the masked placeholder (`"***set***"`) is shown. This enables switching Dataverse environments or Azure app registrations without code changes or container restarts, but write access to this endpoint still allows an attacker to redirect pipeline data and tokens if it is not adequately protected.

#### Example — update a non-sensitive setting

```bash
curl -X PATCH http://localhost:7000/settings \
  -H "Content-Type: application/json" \
  -d '{"settings": {"DATAVERSE_TABLE": "jenssch_newtable"}}'
```

#### Example — switch to a different Azure app registration (write-only credentials)

```bash
curl -X PATCH http://localhost:7000/settings \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "DATAVERSE_TENANT_ID": "new-tenant-id",
      "DATAVERSE_CLIENT_ID": "new-client-id",
      "DATAVERSE_CLIENT_SECRET": "new-client-secret"
    }
  }'
```

#### Example — switch to a different Dataverse environment

```bash
curl -X PATCH http://localhost:7000/settings \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "DATAVERSE_ENV_URL": "https://neworg.crm.dynamics.com",
      "DATAVERSE_TABLE": "jenssch_newtable"
    }
  }'
```

#### Response

```json
{
  "updated": ["DATAVERSE_TABLE"]
}
```

## Interactive API Docs

When the service is running, auto-generated Swagger UI is available at:

```
http://localhost:7000/docs
```

## Project Structure

```
├── app.py                    # FastAPI application
├── upload_to_dataverse.py    # Original CLI script (reference)
├── requirements.txt          # Python dependencies
├── Dockerfile                # Container build definition
├── .dockerignore             # Build context exclusions
├── .env.example              # Environment variable template
└── README.md                 # This file
```

## Configuration (main app)

These settings control whether and where the main app sends save requests:

| Variable | Default | Description |
|----------|---------|-------------|
| `REMOTE_SAVE_ENABLED` | `true` | Enable the remote save service |
| `REMOTE_SAVE_ENDPOINT` | `http://remote-save:7000` | URL of this service |

## Docker Compose

Managed by `docker-compose.yml` under the `remote-save` profile. Requires a `.env` file in `services/remote-save/` with Dataverse credentials:

```bash
docker compose --profile remote-save up -d
```

## Development

```bash
cd services/remote-save
pip install -r requirements.txt
# Ensure .env is populated or env vars are exported
uvicorn app:app --host 0.0.0.0 --port 7000 --reload
```

## Dependencies

- `fastapi`, `uvicorn` — web framework
- `httpx` — async HTTP client for Dataverse API
- `python-dotenv` — `.env` file loading
