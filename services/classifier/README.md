# rpiCoffee — Classifier Service

ML-based coffee drink classifier that identifies coffee types from vibration sensor data. Uses a scikit-learn RandomForest model with 52 statistical features extracted from 6-axis IMU recordings.

## Overview

The classifier receives raw accelerometer + gyroscope data from the main app, extracts statistical features, and returns a coffee type label with a confidence score. It also supports on-device model training from labelled CSV files and model hot-swapping via upload.

When confidence falls below the threshold, the label is returned as `"other"` to avoid low-confidence guesses.

## API Reference

### Health

```
GET /health
```

```json
{ "status": "ok", "model_loaded": true }
```

### Classify

```
POST /classify
Content-Type: application/json
```

**Request body:**

```json
{
  "data": [
    { "acc_x": 0.01, "acc_y": -0.02, "acc_z": 1.0, "gyro_x": 0.1, "gyro_y": -0.05, "gyro_z": 0.02 },
    ...
  ]
}
```

**Response:**

```json
{ "label": "espresso", "confidence": 0.92 }
```

### Train

```
POST /train
Content-Type: application/json
```

Triggers model training from CSV files. Training runs in a background thread.

**Optional request body:**

```json
{ "data_dir": "/custom/path" }
```

**Response:**

```json
{ "status": "training_started", "message": "Model training started in background" }
```

### Training Status

```
GET /train/status
```

**Response:**

```json
{
  "is_training": false,
  "progress": "Training complete",
  "accuracy": 0.95,
  "cv_accuracy": 0.91,
  "cv_std": 0.04,
  "classes": ["black", "cappuccino", "espresso"],
  "samples_per_class": { "black": 5, "cappuccino": 4, "espresso": 6 },
  "total_samples": 15,
  "completed_at": "2026-03-05T10:30:00+00:00",
  "model_path": "/data/models/coffee_model_20260305_103000.joblib"
}
```

### Upload Model

```
POST /upload-model
Content-Type: multipart/form-data
```

Upload a `.joblib` model file to hot-swap the active model.

### Model Info

```
GET /model/info
```

**Response:**

```json
{
  "loaded": true,
  "model_name": "coffee_model_20260305_103000.joblib",
  "trained_at": "2026-03-05T10:30:00+00:00",
  "classes": ["black", "cappuccino", "espresso"],
  "feature_count": 52,
  "feature_names": ["acc_x_mean", "acc_x_std", "..."]
}
```

### Labels

```
GET /labels
```

Returns labels found in the training data directory.

```json
{ "labels": ["black", "cappuccino", "espresso"] }
```

### Training Data

```
GET /training-data
```

List all training CSV files grouped by label.

```
DELETE /training-data/{label}/{filename}
```

Delete a specific training file.

### Settings

```
GET /settings
```

Returns configurable settings with current values.

**Response:**

```json
[
  {
    "key": "CONFIDENCE_THRESHOLD",
    "name": "Confidence Threshold",
    "description": "Minimum confidence score to accept a classification result",
    "type": "float",
    "value": 0.6
  }
]
```

```
PATCH /settings
Content-Type: application/json
```

**Request body:**

```json
{ "settings": { "CONFIDENCE_THRESHOLD": 0.75 } }
```

**Response:**

```json
{ "updated": ["CONFIDENCE_THRESHOLD"] }
```

Settings are persisted to `/data/settings.json` inside the container volume.

## Feature Extraction

52 statistical features are computed per recording:

**Per-axis features** (8 features × 6 axes = 48):

| Feature | Description |
|---------|-------------|
| `mean` | Average value |
| `std` | Standard deviation |
| `min` | Minimum value |
| `max` | Maximum value |
| `rms` | Root mean square |
| `p2p` | Peak-to-peak range |
| `zcr` | Zero-crossing rate |
| `mav` | Mean absolute value |

Applied to each axis: `acc_x`, `acc_y`, `acc_z`, `gyro_x`, `gyro_y`, `gyro_z`.

**Cross-axis features** (4):

| Feature | Description |
|---------|-------------|
| `accel_mag_mean` | Mean of accelerometer magnitude (√(x² + y² + z²)) |
| `accel_mag_std` | Std of accelerometer magnitude |
| `gyro_mag_mean` | Mean of gyroscope magnitude |
| `gyro_mag_std` | Std of gyroscope magnitude |

## Model Management

- **Algorithm:** scikit-learn `RandomForestClassifier` with `StandardScaler` in a `Pipeline`
- **Evaluation:** Stratified K-Fold cross-validation (5 folds when sufficient data)
- **Persistence:** Models saved as `.joblib` files in `/data/models/`
- **Hot-swap:** Upload a new model via the API — it replaces the active model immediately
- **Auto-load:** On startup, the latest `.joblib` file in the model directory is loaded automatically

### Training Workflow

1. **Collect data** — Use the main app's data collection mode to record labelled vibration CSVs
2. **Train** — Call `POST /train` or use the admin panel's "Train" button
3. **Evaluate** — Check `GET /train/status` for accuracy, cross-validation scores, and per-class breakdown
4. **Deploy** — The trained model is automatically loaded and replaces the previous one

Training data structure:

```
/data/training/
├── black/
│   ├── 20260303-101955.csv
│   └── 20260303-102102.csv
├── espresso/
│   └── ...
└── cappuccino/
    └── ...
```

Each CSV has columns: `label`, `elapsed_s`, `acc_x`, `acc_y`, `acc_z`, `gyro_x`, `gyro_y`, `gyro_z`.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence to return a label (below this → `"other"`) |
| `MODEL_DIR` | `/data/models` | Directory for model `.joblib` files |
| `TRAINING_DIR` | `/data/training` | Directory containing labelled training CSVs |

## Docker

### Build

```bash
docker build -t rpicoffee-classifier ./services/classifier
```

### Run

```bash
docker run -d -p 8001:8001 \
  -v ./app/data:/data \
  -e CONFIDENCE_THRESHOLD=0.6 \
  rpicoffee-classifier
```

The `/data` volume mount provides access to both training data (`/data/training/`) and model persistence (`/data/models/`).

### Docker Compose

The classifier is managed by `docker-compose.yml` under the `classifier` profile:

```bash
docker compose --profile classifier up -d
```

## Development

```bash
cd services/classifier
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

## Dependencies

- `fastapi`, `uvicorn` — web framework
- `scikit-learn` — RandomForest classifier + StandardScaler
- `pandas` — CSV loading and data manipulation
- `numpy` — numerical computation
- `joblib` — model serialization
