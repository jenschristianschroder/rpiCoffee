"""
Simplified feature extraction for coffee vibration classification.

Extracts statistical features from each recording (sequence of IMU samples).
Designed for fast computation on Raspberry Pi.

Features per axis (8 × 6 axes = 48):
    mean, std, min, max, rms, peak-to-peak, zero-crossing rate, mean absolute value

Cross-axis features (4):
    accel_magnitude_mean, accel_magnitude_std, gyro_magnitude_mean, gyro_magnitude_std

Total: 52 features per recording.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Column names used in the rpiCoffee codebase
SENSOR_COLS = ["acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]

# Alternate column names used in some CSV datasets (e.g. notebook format)
_COL_MAP = {
    "a_x": "acc_x", "a_y": "acc_y", "a_z": "acc_z",
    "g_x": "gyro_x", "g_y": "gyro_y", "g_z": "gyro_z",
}


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename alternate column names to the canonical sensor column names."""
    return df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns})


def extract_features_from_array(values: np.ndarray, prefix: str) -> dict[str, float]:
    """Extract statistical features from a 1-D signal array."""
    features: dict[str, float] = {}

    features[f"{prefix}_mean"] = float(np.mean(values))
    features[f"{prefix}_std"] = float(np.std(values))
    features[f"{prefix}_min"] = float(np.min(values))
    features[f"{prefix}_max"] = float(np.max(values))
    features[f"{prefix}_rms"] = float(np.sqrt(np.mean(values ** 2)))
    features[f"{prefix}_p2p"] = float(np.ptp(values))
    features[f"{prefix}_mav"] = float(np.mean(np.abs(values)))

    # Zero-crossing rate
    zero_crossings = np.where(np.diff(np.signbit(values)))[0]
    features[f"{prefix}_zcr"] = float(len(zero_crossings) / max(len(values), 1))

    return features


def extract_features(sensor_data: list[dict[str, float]]) -> dict[str, float]:
    """
    Extract a feature vector from a single recording (list of sensor dicts).

    Parameters
    ----------
    sensor_data : list of dicts
        Each dict has keys: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
        (elapsed_s is ignored if present)

    Returns
    -------
    dict mapping feature name → float value (52 features total)
    """
    df = pd.DataFrame(sensor_data)
    df = normalise_columns(df)

    features: dict[str, float] = {}

    # Per-axis features
    for col in SENSOR_COLS:
        if col not in df.columns:
            # If column missing, fill with zeros
            vals = np.zeros(len(df))
        else:
            vals = df[col].values.astype(np.float64)
        features.update(extract_features_from_array(vals, col))

    # Cross-axis magnitude features
    acc_cols = ["acc_x", "acc_y", "acc_z"]
    gyro_cols = ["gyro_x", "gyro_y", "gyro_z"]

    if all(c in df.columns for c in acc_cols):
        accel_mag = np.sqrt(df["acc_x"] ** 2 + df["acc_y"] ** 2 + df["acc_z"] ** 2)
        features["accel_mag_mean"] = float(np.mean(accel_mag))
        features["accel_mag_std"] = float(np.std(accel_mag))
    else:
        features["accel_mag_mean"] = 0.0
        features["accel_mag_std"] = 0.0

    if all(c in df.columns for c in gyro_cols):
        gyro_mag = np.sqrt(df["gyro_x"] ** 2 + df["gyro_y"] ** 2 + df["gyro_z"] ** 2)
        features["gyro_mag_mean"] = float(np.mean(gyro_mag))
        features["gyro_mag_std"] = float(np.std(gyro_mag))
    else:
        features["gyro_mag_mean"] = 0.0
        features["gyro_mag_std"] = 0.0

    return features


def extract_features_from_csv(csv_path: str) -> tuple[dict[str, float], str]:
    """
    Load a CSV file and extract features.

    The CSV is expected to have columns:
        label, elapsed_s, acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

    Returns
    -------
    (features_dict, label)
    """
    df = pd.read_csv(csv_path)
    df = normalise_columns(df)

    # Determine label from the 'label' or 'program' column
    label = "unknown"
    if "label" in df.columns:
        label = str(df["label"].iloc[0])
    elif "program" in df.columns:
        label = str(df["program"].iloc[0])

    # Extract features using only sensor columns
    sensor_dicts = df[SENSOR_COLS].to_dict("records")
    features = extract_features(sensor_dicts)

    return features, label


def get_feature_names() -> list[str]:
    """Return the ordered list of feature names produced by extract_features()."""
    names: list[str] = []
    for col in SENSOR_COLS:
        for suffix in ("mean", "std", "min", "max", "rms", "p2p", "mav", "zcr"):
            names.append(f"{col}_{suffix}")
    names.extend(["accel_mag_mean", "accel_mag_std", "gyro_mag_mean", "gyro_mag_std"])
    return names
