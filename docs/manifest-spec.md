# Service Manifest Specification

Version: 1.0.0

## Overview

Every pipeline service in rpiCoffee **must** expose a `GET /manifest` endpoint
that returns a JSON document describing the service's identity, capabilities,
inputs, outputs, and endpoints.  The manifest is the contract between a service
and the pipeline engine — it is fetched automatically when a service is
registered in the Service Registry.

## Manifest Schema

```jsonc
{
  // ── Identity ──────────────────────────────────────────────────
  "name":        "<string>",   // unique kebab-case identifier (required)
  "version":     "<semver>",   // semantic version, e.g. "1.0.0" (required)
  "description": "<string>",   // human-readable summary (required)

  // ── Data contract ─────────────────────────────────────────────
  "inputs": [                  // list of named inputs (required, may be empty)
    {
      "name":        "<string>",  // machine name, used in input_map references
      "type":        "<type>",    // one of the supported types (see below)
      "required":    <bool>,      // true if the pipeline must provide this input
      "description": "<string>"   // human-readable description
    }
  ],
  "outputs": [                 // list of named outputs (required, may be empty)
    {
      "name":        "<string>",
      "type":        "<type>",
      "description": "<string>"
    }
  ],

  // ── Endpoints ─────────────────────────────────────────────────
  "endpoints": {
    "execute":         { "method": "<HTTP method>", "path": "<path>" },  // required
    "health":          { "method": "GET",           "path": "<path>" },  // required
    "settings":        { "method": "GET",           "path": "<path>" },  // optional
    "update_settings": { "method": "PATCH",         "path": "<path>" }   // optional
  },

  // ── Failure handling ──────────────────────────────────────────
  "failure_modes": ["skip", "halt"]   // which modes this service supports
}
```

## Supported Types

| Type     | JSON representation                    | Notes                        |
|----------|----------------------------------------|------------------------------|
| `string` | JSON string                            |                              |
| `int`    | JSON number (integer)                  |                              |
| `float`  | JSON number                            |                              |
| `bool`   | JSON boolean                           |                              |
| `object` | JSON object                            | Arbitrary key/value map      |
| `array`  | JSON array                             | Heterogeneous or homogeneous |
| `binary` | Not sent as JSON; returned as raw bytes| Used for audio, files, etc.  |

When the pipeline engine calls a service's execute endpoint, **all non-binary
inputs are sent as a single JSON object** where each key corresponds to an
input `name`.

When an output has type `binary`, the execute endpoint must return raw bytes
(e.g. `audio/wav`).  The pipeline engine will store the binary data separately
and expose a URL reference (e.g. `audio_url`) in the pipeline context.

## Input Mapping References

In the pipeline configuration, each step's `input_map` connects its inputs to
outputs from previous steps using reference strings:

| Reference pattern          | Resolves to                                    |
|---------------------------|------------------------------------------------|
| `$sensor.data`            | The raw sensor data array (fixed first stage)  |
| `$sensor.timestamp`       | ISO-8601 timestamp of sensor capture           |
| `$<service_name>.<key>`   | Output `key` from a previous pipeline step     |
| `$pipeline.result`        | Aggregated result object from all prior steps  |

## Required Endpoints

### `GET /manifest`

Returns the full manifest JSON with `Content-Type: application/json`.

No authentication required.  Must respond within **5 seconds**.

### Execute endpoint (defined in manifest)

Called by the pipeline engine during brew execution.

- **Request**: JSON body with keys matching input `name` fields.
- **Response**: JSON body with keys matching output `name` fields, **or** raw
  binary for services that produce `binary` output.
- **Timeout**: Configurable per-step; default 30 seconds.

### `GET /health`

Must return a JSON object with at least `{"status": "ok"}` when healthy.

Any other `status` value indicates degraded or unhealthy state.

### `GET /settings` (optional)

Returns a JSON array of setting descriptors:

```json
[
  {
    "key": "CONFIDENCE_THRESHOLD",
    "name": "Confidence Threshold",
    "value": 0.6,
    "description": "Minimum confidence to accept",
    "type": "float"
  }
]
```

### `PATCH /settings` (optional)

Accepts a JSON object of `{ key: value }` pairs to update.

## Examples

### Classifier

```json
{
  "name": "classifier",
  "version": "2.0.0",
  "description": "Coffee type classifier using scikit-learn RandomForest",
  "inputs": [
    { "name": "sensor_data", "type": "array", "required": true, "description": "6-axis IMU sensor readings" }
  ],
  "outputs": [
    { "name": "label", "type": "string", "description": "Classified coffee type" },
    { "name": "confidence", "type": "float", "description": "Classification confidence score" }
  ],
  "endpoints": {
    "execute": { "method": "POST", "path": "/classify" },
    "health": { "method": "GET", "path": "/health" },
    "settings": { "method": "GET", "path": "/settings" },
    "update_settings": { "method": "PATCH", "path": "/settings" }
  },
  "failure_modes": ["skip", "halt"]
}
```

### LLM (llama-cpp)

```json
{
  "name": "llm",
  "version": "2.0.0",
  "description": "Coffee comment generator using fine-tuned Qwen2.5-0.5B (llama-cpp)",
  "inputs": [
    { "name": "coffee_label", "type": "string", "required": true, "description": "Coffee type label from classifier" },
    { "name": "timestamp", "type": "string", "required": true, "description": "ISO-8601 timestamp of brew" }
  ],
  "outputs": [
    { "name": "response", "type": "string", "description": "Generated witty comment" },
    { "name": "tokens", "type": "int", "description": "Number of tokens generated" },
    { "name": "elapsed_s", "type": "float", "description": "Generation time in seconds" },
    { "name": "tokens_per_s", "type": "float", "description": "Tokens per second" }
  ],
  "endpoints": {
    "execute": { "method": "POST", "path": "/generate" },
    "health": { "method": "GET", "path": "/health" },
    "settings": { "method": "GET", "path": "/settings" },
    "update_settings": { "method": "PATCH", "path": "/settings" }
  },
  "failure_modes": ["skip", "halt"]
}
```

### TTS

```json
{
  "name": "tts",
  "version": "1.0.0",
  "description": "Offline speech synthesis using Piper TTS",
  "inputs": [
    { "name": "text", "type": "string", "required": true, "description": "Text to synthesize" },
    { "name": "speed", "type": "float", "required": false, "description": "Speech speed multiplier (default 1.0)" }
  ],
  "outputs": [
    { "name": "audio", "type": "binary", "description": "Synthesized WAV audio bytes" }
  ],
  "endpoints": {
    "execute": { "method": "POST", "path": "/synthesize" },
    "health": { "method": "GET", "path": "/health" },
    "settings": { "method": "GET", "path": "/settings" },
    "update_settings": { "method": "PATCH", "path": "/settings" }
  },
  "failure_modes": ["skip", "halt"]
}
```

### Remote Save

```json
{
  "name": "remote-save",
  "version": "1.0.0",
  "description": "Persist brew results to Microsoft Dataverse",
  "inputs": [
    { "name": "name", "type": "string", "required": true, "description": "Record name" },
    { "name": "coffee_type", "type": "string", "required": true, "description": "Coffee type label" },
    { "name": "confidence", "type": "float", "required": true, "description": "Classification confidence" },
    { "name": "text", "type": "string", "required": false, "description": "Generated comment text" },
    { "name": "sensor_data", "type": "array", "required": false, "description": "Raw sensor data for CSV upload" }
  ],
  "outputs": [
    { "name": "record_id", "type": "string", "description": "Dataverse record ID" }
  ],
  "endpoints": {
    "execute": { "method": "POST", "path": "/save" },
    "health": { "method": "GET", "path": "/health" },
    "settings": { "method": "GET", "path": "/settings" },
    "update_settings": { "method": "PATCH", "path": "/settings" }
  },
  "failure_modes": ["skip"]
}
```
