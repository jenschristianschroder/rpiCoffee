"""
ML model manager for the coffee classifier.

Handles training, prediction, model persistence, and hot-swapping.
Uses scikit-learn RandomForestClassifier with StandardScaler in a Pipeline.
"""

from __future__ import annotations

import glob
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, StandardScaler

from features import extract_features, extract_features_from_csv, get_feature_names

logger = logging.getLogger("classifier.model_manager")

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/data/models"))
TRAINING_DIR = Path(os.environ.get("TRAINING_DIR", "/data/training"))
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.6"))


class TrainingStatus:
    """Holds the status of the last/current training run."""

    def __init__(self):
        self.is_training: bool = False
        self.progress: str = ""
        self.error: str | None = None
        self.accuracy: float | None = None
        self.cv_accuracy: float | None = None
        self.cv_std: float | None = None
        self.class_report: str | None = None
        self.classes: list[str] = []
        self.samples_per_class: dict[str, int] = {}
        self.total_samples: int = 0
        self.completed_at: str | None = None
        self.model_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_training": self.is_training,
            "progress": self.progress,
            "error": self.error,
            "accuracy": self.accuracy,
            "cv_accuracy": self.cv_accuracy,
            "cv_std": self.cv_std,
            "class_report": self.class_report,
            "classes": self.classes,
            "samples_per_class": self.samples_per_class,
            "total_samples": self.total_samples,
            "completed_at": self.completed_at,
            "model_path": self.model_path,
        }


class ModelManager:
    """Thread-safe ML model manager for coffee classification."""

    def __init__(self):
        self._lock = Lock()
        self._pipeline: Pipeline | None = None
        self._label_encoder: LabelEncoder | None = None
        self._feature_names: list[str] = get_feature_names()
        self._model_name: str | None = None
        self._model_path: str | None = None
        self._trained_at: str | None = None
        self._classes: list[str] = []
        self.training_status = TrainingStatus()

        # Try to load the latest model on startup
        self._load_latest_model()

    @property
    def is_ready(self) -> bool:
        """Check if a model is loaded and ready for prediction."""
        with self._lock:
            return self._pipeline is not None and self._label_encoder is not None

    def get_info(self) -> dict[str, Any]:
        """Return metadata about the currently loaded model."""
        with self._lock:
            if self._pipeline is None:
                return {"loaded": False, "message": "No model loaded"}
            return {
                "loaded": True,
                "model_name": self._model_name,
                "model_path": self._model_path,
                "trained_at": self._trained_at,
                "classes": self._classes,
                "feature_count": len(self._feature_names),
                "feature_names": self._feature_names,
            }

    def predict(self, sensor_data: list[dict[str, float]]) -> dict[str, Any]:
        """
        Classify a single recording.

        Parameters
        ----------
        sensor_data : list of dicts
            Raw sensor readings with keys acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

        Returns
        -------
        dict with 'label' and 'confidence'
        """
        with self._lock:
            if self._pipeline is None or self._label_encoder is None:
                return {"label": "unknown", "confidence": 0.0}

            # Extract features
            features = extract_features(sensor_data)

            # Build feature vector in the correct order
            feature_vector = np.array(
                [features.get(name, 0.0) for name in self._feature_names]
            ).reshape(1, -1)

            # Replace NaN/Inf
            feature_vector = np.nan_to_num(feature_vector, nan=0.0, posinf=0.0, neginf=0.0)

            # Predict
            pred = self._pipeline.predict(feature_vector)
            proba = self._pipeline.predict_proba(feature_vector)

            label = self._label_encoder.inverse_transform(pred)[0]
            confidence = float(np.max(proba))

            if confidence < CONFIDENCE_THRESHOLD:
                label = "other"

            return {"label": label, "confidence": round(confidence, 4)}

    def train(self, data_dir: str | None = None) -> dict[str, Any]:
        """
        Train a new model from CSV files in the training directory.

        Directory structure expected:
            <data_dir>/<label>/<timestamp>.csv

        Each CSV has columns: label, elapsed_s, acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

        Also scans for *.csv.sample files in the parent of data_dir (e.g. /data/).
        """
        data_path = Path(data_dir) if data_dir else TRAINING_DIR
        parent_dir = data_path.parent  # /data/ — where .csv.sample files live

        self.training_status = TrainingStatus()
        self.training_status.is_training = True
        self.training_status.progress = "Scanning for training data..."

        try:
            all_features: list[dict[str, float]] = []
            all_labels: list[str] = []

            # 1. Load from training subdirectories: /data/training/<label>/*.csv
            if data_path.exists():
                for label_dir in sorted(data_path.iterdir()):
                    if not label_dir.is_dir():
                        continue
                    label = label_dir.name
                    csv_files = sorted(label_dir.glob("*.csv"))
                    for csv_file in csv_files:
                        try:
                            features, _ = extract_features_from_csv(str(csv_file))
                            all_features.append(features)
                            all_labels.append(label)
                        except Exception as e:
                            logger.warning("Failed to process %s: %s", csv_file, e)

            # 2. Load from .csv.sample files in /data/
            sample_files = sorted(parent_dir.glob("*.csv.sample")) + sorted(parent_dir.glob("*.csv"))
            for csv_file in sample_files:
                # Skip files in subdirectories
                if csv_file.parent != parent_dir:
                    continue
                # Skip non-sample files that might be something else
                try:
                    features, label = extract_features_from_csv(str(csv_file))
                    all_features.append(features)
                    all_labels.append(label)
                except Exception as e:
                    logger.warning("Failed to process %s: %s", csv_file, e)

            if not all_features:
                self.training_status.is_training = False
                self.training_status.error = "No training data found"
                self.training_status.progress = "Failed"
                return {"error": "No training data found", "status": "failed"}

            # Count samples per class
            from collections import Counter
            class_counts = Counter(all_labels)
            self.training_status.samples_per_class = dict(class_counts)
            self.training_status.total_samples = len(all_labels)
            self.training_status.progress = f"Found {len(all_labels)} samples across {len(class_counts)} classes"
            logger.info("Training data: %s", dict(class_counts))

            # Need at least 2 classes
            if len(class_counts) < 2:
                self.training_status.is_training = False
                self.training_status.error = f"Need at least 2 classes, found {len(class_counts)}: {list(class_counts.keys())}"
                self.training_status.progress = "Failed"
                return {"error": self.training_status.error, "status": "failed"}

            # Build feature matrix
            self.training_status.progress = "Building feature matrix..."
            feature_names = get_feature_names()
            X = np.array([
                [f.get(name, 0.0) for name in feature_names]
                for f in all_features
            ])
            X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

            # Encode labels
            le = LabelEncoder()
            y = le.fit_transform(all_labels)

            # Train RandomForest pipeline
            self.training_status.progress = "Training Random Forest model..."
            pipeline = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", RandomForestClassifier(
                    n_estimators=200,
                    max_depth=None,
                    min_samples_split=2,
                    min_samples_leaf=1,
                    random_state=42,
                    n_jobs=-1,
                ))
            ])

            # Cross-validation (if enough samples per class)
            min_samples = min(class_counts.values())
            n_splits = min(5, min_samples)
            if n_splits >= 2:
                self.training_status.progress = f"Running {n_splits}-fold cross-validation..."
                cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
                cv_scores = cross_val_score(pipeline, X, y, cv=cv, scoring="accuracy")
                self.training_status.cv_accuracy = round(float(cv_scores.mean()), 4)
                self.training_status.cv_std = round(float(cv_scores.std()), 4)
                logger.info("CV accuracy: %.4f ± %.4f", cv_scores.mean(), cv_scores.std())

            # Train on full dataset
            self.training_status.progress = "Training on full dataset..."
            pipeline.fit(X, y)

            train_acc = float(accuracy_score(y, pipeline.predict(X)))
            self.training_status.accuracy = round(train_acc, 4)

            # Classification report
            y_pred = pipeline.predict(X)
            report = classification_report(y, y_pred, target_names=le.classes_)
            self.training_status.class_report = report
            logger.info("Training accuracy: %.4f\n%s", train_acc, report)

            # Save model
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            model_filename = f"coffee_classifier_{timestamp}.joblib"
            model_path = MODEL_DIR / model_filename

            model_data = {
                "pipeline": pipeline,
                "label_encoder": le,
                "feature_names": feature_names,
                "model_name": "RandomForest",
                "trained_at": timestamp,
                "classes": list(le.classes_),
                "accuracy": train_acc,
                "cv_accuracy": self.training_status.cv_accuracy,
                "samples_per_class": dict(class_counts),
            }
            joblib.dump(model_data, model_path)
            logger.info("Model saved to %s", model_path)

            # Hot-swap the active model
            with self._lock:
                self._pipeline = pipeline
                self._label_encoder = le
                self._feature_names = feature_names
                self._model_name = "RandomForest"
                self._model_path = str(model_path)
                self._trained_at = timestamp
                self._classes = list(le.classes_)

            self.training_status.is_training = False
            self.training_status.progress = "Training complete"
            self.training_status.completed_at = timestamp
            self.training_status.model_path = str(model_path)
            self.training_status.classes = list(le.classes_)

            return {
                "status": "complete",
                "accuracy": train_acc,
                "cv_accuracy": self.training_status.cv_accuracy,
                "cv_std": self.training_status.cv_std,
                "classes": list(le.classes_),
                "samples_per_class": dict(class_counts),
                "total_samples": len(all_labels),
                "model_path": str(model_path),
            }

        except Exception as e:
            logger.exception("Training failed")
            self.training_status.is_training = False
            self.training_status.error = str(e)
            self.training_status.progress = "Failed"
            return {"error": str(e), "status": "failed"}

    def load_model(self, model_path: str) -> dict[str, Any]:
        """
        Load a model from a .joblib file.

        Expected keys in the joblib dict:
            pipeline, label_encoder, feature_names
        """
        try:
            data = joblib.load(model_path)

            required_keys = {"pipeline", "label_encoder", "feature_names"}
            if not required_keys.issubset(data.keys()):
                missing = required_keys - set(data.keys())
                return {"error": f"Missing keys in model file: {missing}"}

            with self._lock:
                self._pipeline = data["pipeline"]
                self._label_encoder = data["label_encoder"]
                self._feature_names = data["feature_names"]
                self._model_name = data.get("model_name", "Unknown")
                self._model_path = model_path
                self._trained_at = data.get("trained_at", "unknown")
                self._classes = list(data["label_encoder"].classes_)

            logger.info("Loaded model from %s (classes: %s)", model_path, self._classes)
            return {
                "status": "loaded",
                "model_path": model_path,
                "classes": self._classes,
                "model_name": self._model_name,
            }

        except Exception as e:
            logger.exception("Failed to load model from %s", model_path)
            return {"error": str(e)}

    def _load_latest_model(self):
        """Try to load the most recent model from MODEL_DIR on startup."""
        if not MODEL_DIR.exists():
            logger.info("No model directory found at %s", MODEL_DIR)
            return

        model_files = sorted(MODEL_DIR.glob("*.joblib"))
        if not model_files:
            logger.info("No model files found in %s", MODEL_DIR)
            return

        latest = model_files[-1]  # sorted by name (includes timestamp)
        logger.info("Loading latest model: %s", latest)
        result = self.load_model(str(latest))
        if "error" in result:
            logger.error("Failed to load latest model: %s", result["error"])


# Singleton
model_manager = ModelManager()
