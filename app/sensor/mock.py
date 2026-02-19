"""
Mock vibration sensor.

On Linux: creates a PTY (pseudo-terminal) pair and replays CSV data through it,
simulating a USB-connected IMU sensor streaming at the configured sample rate.

On Windows: provides a direct in-memory replay that bypasses the serial port,
returning parsed sensor data directly for local development/testing.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import platform
import random
from pathlib import Path
from typing import Callable

from config import config

logger = logging.getLogger("rpicoffee.sensor.mock")

_IS_WINDOWS = platform.system() == "Windows"

# Resolve data directory – /data in Docker, ./data locally
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
if not DATA_DIR.exists():
    # Fallback for local development
    DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Gyro spike threshold – values beyond this are sensor artifacts
_GYRO_SPIKE_THRESHOLD = 200.0


def _is_spike_row(row: dict[str, str]) -> bool:
    """Return True if any gyro channel has an artifact spike."""
    try:
        return any(
            abs(float(row[k])) > _GYRO_SPIKE_THRESHOLD
            for k in ("gyro_x", "gyro_y", "gyro_z")
        )
    except (ValueError, KeyError):
        return False


def _load_csv_rows(csv_path: Path) -> list[str]:
    """
    Load CSV and return data rows as pre-formatted serial lines
    (without the label column), filtering out spike artifacts.
    """
    lines: list[str] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _is_spike_row(row):
                continue
            line = ",".join(
                row[k]
                for k in ("elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z")
            )
            lines.append(line + "\n")
    return lines


def _load_csv_as_dicts(csv_path: Path) -> list[dict[str, float]]:
    """Load CSV and return parsed dicts (for Windows direct-replay mode)."""
    data: list[dict[str, float]] = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _is_spike_row(row):
                continue
            data.append({
                "elapsed_s": float(row["elapsed_s"]),
                "acc_x": float(row["acc_x"]),
                "acc_y": float(row["acc_y"]),
                "acc_z": float(row["acc_z"]),
                "gyro_x": float(row["gyro_x"]),
                "gyro_y": float(row["gyro_y"]),
                "gyro_z": float(row["gyro_z"]),
            })
    return data


class MockSensor:
    """
    Replays CSV IMU data, simulating a USB-connected sensor.

    On Linux:  streams through a PTY at the configured sample rate.
    On Windows: loads data into memory for direct retrieval (no serial port).

    Usage::

        mock = MockSensor()
        mock.start()       # begins async replay in background
        port = mock.port   # Linux: /dev/pts/3 | Windows: "__mock__"
        ...
        mock.stop()
    """

    def __init__(self) -> None:
        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._slave_path: str | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        # Windows direct-replay buffer
        self._buffered_data: list[dict[str, float]] | None = None

    @property
    def port(self) -> str | None:
        """The port path. On Windows returns '__mock__' sentinel value."""
        return self._slave_path

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def buffered_data(self) -> list[dict[str, float]] | None:
        """On Windows, returns the pre-loaded sensor data directly."""
        return self._buffered_data

    def start(self, on_done: Callable[[], None] | None = None) -> str:
        """Create the mock and begin replaying data. Returns the port path."""
        # On Windows, always reload the buffer to pick up config changes
        if self._running and _IS_WINDOWS:
            self._running = False
        elif self._running:
            raise RuntimeError("Mock sensor is already running")

        if _IS_WINDOWS:
            return self._start_windows(on_done)
        else:
            return self._start_linux(on_done)

    def _start_windows(self, on_done: Callable[[], None] | None = None) -> str:
        """Windows: load CSV data into memory buffer."""
        csv_files = list(DATA_DIR.glob("*.csv"))
        if not csv_files:
            logger.error("No CSV files found in %s", DATA_DIR)
            self._slave_path = "__mock__"
            self._buffered_data = []
            return self._slave_path

        chosen = random.choice(csv_files)
        logger.info("Mock sensor (Windows): loading %s", chosen.name)

        duration = config.SENSOR_DURATION_S
        all_data = _load_csv_as_dicts(chosen)
        # Filter by elapsed_s so we always get the correct time span
        self._buffered_data = [d for d in all_data if d["elapsed_s"] < duration]
        self._slave_path = "__mock__"
        self._running = True

        logger.info("Mock sensor ready: %d samples buffered (%.1fs)",
                    len(self._buffered_data),
                    self._buffered_data[-1]["elapsed_s"] if self._buffered_data else 0)
        return self._slave_path

    def _start_linux(self, on_done: Callable[[], None] | None = None) -> str:
        """Linux: create PTY and stream data through it."""
        import pty as _pty

        self._master_fd, self._slave_fd = _pty.openpty()
        self._slave_path = os.ttyname(self._slave_fd)
        self._running = True

        logger.info("Mock sensor PTY created: %s", self._slave_path)
        self._task = asyncio.get_event_loop().create_task(self._replay(on_done))
        return self._slave_path

    def stop(self) -> None:
        """Stop the replay and clean up."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._close_fds()
        self._buffered_data = None

    async def _replay(self, on_done: Callable[[], None] | None = None) -> None:
        """Replay a randomly chosen CSV file through the PTY master fd."""
        try:
            csv_files = list(DATA_DIR.glob("*.csv"))
            if not csv_files:
                logger.error("No CSV files found in %s", DATA_DIR)
                return

            chosen = random.choice(csv_files)
            logger.info("Replaying %s", chosen.name)
            lines = _load_csv_rows(chosen)

            interval = 1.0 / config.SENSOR_SAMPLE_RATE_HZ
            max_samples = config.SENSOR_DURATION_S * config.SENSOR_SAMPLE_RATE_HZ

            header = "elapsed_s,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z\n"
            os.write(self._master_fd, header.encode())

            for i, line in enumerate(lines):
                if not self._running or i >= max_samples:
                    break
                os.write(self._master_fd, line.encode())
                await asyncio.sleep(interval)

            logger.info("Mock sensor replay complete (%d samples)", min(len(lines), max_samples))
        except asyncio.CancelledError:
            logger.info("Mock sensor replay cancelled")
        except Exception:
            logger.exception("Mock sensor replay error")
        finally:
            self._running = False
            self._close_fds()
            if on_done:
                on_done()

    def _close_fds(self) -> None:
        for fd in (self._master_fd, self._slave_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._master_fd = None
        self._slave_fd = None


# Module-level singleton
mock_sensor = MockSensor()
