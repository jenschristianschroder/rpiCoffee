"""
Training data storage helpers.

Manages CSV files for ML training data collection:
  - Save new recordings to /data/training/<label>/<timestamp>.csv
  - List / delete training files
  - List / delete sample files in /data/
  - Promote training files to sample files
"""

from __future__ import annotations

import csv
import io
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("rpicoffee.training_data")

DATA_DIR = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data")))
TRAINING_DIR = DATA_DIR / "training"

# CSV column header for training files (matches the existing .csv.sample format)
_CSV_HEADER = ["label", "elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z"]

# Required columns for validating uploaded CSV files
_REQUIRED_CSV_COLUMNS = frozenset(_CSV_HEADER)


def save_recording(label: str, sensor_data: list[dict[str, float]]) -> str:
    """
    Save a sensor recording as a labelled CSV file.

    Parameters
    ----------
    label : str
        Coffee type label (e.g. "black", "espresso", "cappuccino").
    sensor_data : list of dicts
        Each dict has keys: elapsed_s, acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

    Returns
    -------
    str – the path of the saved CSV file.
    """
    label_dir = TRAINING_DIR / label
    label_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{timestamp}.csv"
    filepath = label_dir / filename

    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADER, extrasaction="ignore")
        writer.writeheader()
        for sample in sensor_data:
            row = {
                "label": label,
                "elapsed_s": sample.get("elapsed_s", 0.0),
                "acc_x": sample.get("acc_x", 0.0),
                "acc_y": sample.get("acc_y", 0.0),
                "acc_z": sample.get("acc_z", 0.0),
                "gyro_x": sample.get("gyro_x", 0.0),
                "gyro_y": sample.get("gyro_y", 0.0),
                "gyro_z": sample.get("gyro_z", 0.0),
            }
            writer.writerow(row)

    logger.info("Saved recording: %s (%d samples)", filepath, len(sensor_data))
    return str(filepath)


def list_training_data() -> dict[str, list[dict[str, Any]]]:
    """
    List all training CSV files grouped by label.

    Returns
    -------
    dict mapping label → list of file info dicts
    """
    result: dict[str, list[dict[str, Any]]] = {}

    if not TRAINING_DIR.exists():
        return result

    for label_dir in sorted(TRAINING_DIR.iterdir()):
        if not label_dir.is_dir():
            continue
        label = label_dir.name
        files = []
        for csv_file in sorted(label_dir.glob("*.csv")):
            stat = csv_file.stat()
            files.append({
                "filename": csv_file.name,
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
            })
        if files:
            result[label] = files

    return result


def delete_training_file(label: str, filename: str) -> bool:
    """Delete a specific training CSV file. Returns True if deleted."""
    file_path = TRAINING_DIR / label / filename

    if not file_path.exists():
        return False

    # Safety check: ensure path is within TRAINING_DIR
    try:
        file_path.resolve().relative_to(TRAINING_DIR.resolve())
    except ValueError:
        return False

    file_path.unlink()
    logger.info("Deleted training file: %s/%s", label, filename)

    # Remove empty label directory
    label_dir = TRAINING_DIR / label
    if label_dir.exists() and not any(label_dir.iterdir()):
        label_dir.rmdir()

    return True


def delete_all_training_data(label: str | None = None) -> int:
    """
    Delete all training data, optionally filtered by label.

    Returns the number of files deleted.
    """
    count = 0

    if not TRAINING_DIR.exists():
        return count

    if label:
        label_dir = TRAINING_DIR / label
        if label_dir.exists():
            for csv_file in label_dir.glob("*.csv"):
                csv_file.unlink()
                count += 1
            if not any(label_dir.iterdir()):
                label_dir.rmdir()
    else:
        for label_dir in TRAINING_DIR.iterdir():
            if label_dir.is_dir():
                for csv_file in label_dir.glob("*.csv"):
                    csv_file.unlink()
                    count += 1
                if not any(label_dir.iterdir()):
                    label_dir.rmdir()

    logger.info("Deleted %d training file(s) (label=%s)", count, label or "all")
    return count


def list_sample_files() -> list[dict[str, Any]]:
    """List *.csv.sample files in /data/."""
    files = []
    for csv_file in sorted(DATA_DIR.glob("*.csv.sample")):
        stat = csv_file.stat()
        # Parse label from filename: "<label>-<timestamp>.csv.sample"
        name = csv_file.name
        label = name.split("-")[0] if "-" in name else "unknown"
        files.append({
            "filename": name,
            "label": label,
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
        })
    return files


def delete_sample_file(filename: str) -> bool:
    """Delete a sample CSV file from /data/. Returns True if deleted."""
    file_path = DATA_DIR / filename

    if not file_path.exists():
        return False

    # Safety: only allow .csv.sample files in DATA_DIR
    try:
        file_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return False

    if not file_path.name.endswith(".csv.sample"):
        return False

    file_path.unlink()
    logger.info("Deleted sample file: %s", filename)
    return True


def get_training_file_path(label: str, filename: str) -> Path | None:
    """
    Return the Path for a training CSV file, or None if it does not exist
    or is outside TRAINING_DIR (path-traversal guard).
    """
    file_path = TRAINING_DIR / label / filename
    try:
        file_path.resolve().relative_to(TRAINING_DIR.resolve())
    except ValueError:
        return None
    if not file_path.exists() or not file_path.name.endswith(".csv"):
        return None
    return file_path


def get_sample_file_path(filename: str) -> Path | None:
    """
    Return the Path for a sample CSV file, or None if it does not exist
    or is outside DATA_DIR (path-traversal guard).
    """
    file_path = DATA_DIR / filename
    try:
        file_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        return None
    if not file_path.exists() or not file_path.name.endswith(".csv.sample"):
        return None
    return file_path


def save_uploaded_training_file(label: str, filename: str, content: bytes) -> str:
    """
    Save uploaded CSV content as a training file under TRAINING_DIR/<label>/<filename>.

    Validates that the filename ends with .csv, the content is non-empty, and
    that the resulting path stays within TRAINING_DIR.  Returns the filename
    that was stored.

    Raises ValueError on validation failure.
    """
    if not filename.endswith(".csv"):
        raise ValueError("Only .csv files are accepted for training data")

    if not content:
        raise ValueError("Uploaded file is empty")

    # Sanitise: use only the basename so callers cannot inject path segments
    safe_name = Path(filename).name
    label_dir = TRAINING_DIR / label
    label_dir.mkdir(parents=True, exist_ok=True)
    file_path = label_dir / safe_name

    # Path-traversal guard
    try:
        file_path.resolve().relative_to(TRAINING_DIR.resolve())
    except ValueError:
        raise ValueError("Invalid file path")

    # Validate CSV structure (header row must be present)
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV file has no content")
    if not _REQUIRED_CSV_COLUMNS.issubset(set(header)):
        raise ValueError(f"CSV header must contain columns: {', '.join(sorted(_REQUIRED_CSV_COLUMNS))}")

    file_path.write_bytes(content)
    logger.info("Saved uploaded training file: %s/%s (%d bytes)", label, safe_name, len(content))
    return safe_name


def save_uploaded_sample_file(filename: str, content: bytes) -> str:
    """
    Save uploaded CSV content as a sample file in DATA_DIR.

    Validates that the filename ends with .csv.sample, the content is
    non-empty, and the path stays within DATA_DIR.  Returns the filename
    that was stored.

    Raises ValueError on validation failure.
    """
    if not filename.endswith(".csv.sample"):
        raise ValueError("Only .csv.sample files are accepted for sample data")

    if not content:
        raise ValueError("Uploaded file is empty")

    safe_name = Path(filename).name
    file_path = DATA_DIR / safe_name

    # Path-traversal guard
    try:
        file_path.resolve().relative_to(DATA_DIR.resolve())
    except ValueError:
        raise ValueError("Invalid file path")

    # Validate CSV structure
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV file has no content")
    if not _REQUIRED_CSV_COLUMNS.issubset(set(header)):
        raise ValueError(f"CSV header must contain columns: {', '.join(sorted(_REQUIRED_CSV_COLUMNS))}")

    file_path.write_bytes(content)
    logger.info("Saved uploaded sample file: %s (%d bytes)", safe_name, len(content))
    return safe_name


def promote_training_to_sample(label: str, filename: str) -> str | None:
    """
    Copy a training CSV to /data/ as a .csv.sample file.

    Returns the new filename, or None on failure.
    """
    src = TRAINING_DIR / label / filename

    if not src.exists():
        return None

    # Build sample filename: <label>-<original_timestamp>.csv.sample
    base = filename.replace(".csv", "")
    new_name = f"{label}-{base}.csv.sample"
    dst = DATA_DIR / new_name

    shutil.copy2(src, dst)
    logger.info("Promoted training file to sample: %s → %s", src, dst)
    return new_name
