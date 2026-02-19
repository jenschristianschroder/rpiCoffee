"""
App-side PicoQuake reader.

Spawns / manages the acquisition subprocess (``picoquake_acq.py``) and
exposes helpers for the pipeline to trigger recordings and read data from
the shared-memory ring buffer.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("rpicoffee.sensor.picoquake_reader")

# Lazy import – only needed when actually connecting to shared memory
_SharedRingBuffer = None


def _get_ring_class():
    global _SharedRingBuffer
    if _SharedRingBuffer is None:
        from sensor.picoquake_acq import SharedRingBuffer as _SRB
        _SharedRingBuffer = _SRB
    return _SharedRingBuffer


class PicoQuakeReader:
    """Manages the acquisition subprocess and reads from the ring buffer."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._ring = None
        self._config: dict[str, Any] = {}
        self._last_error: str | None = None
        self._log_thread: threading.Thread | None = None

    # ── Lifecycle ─────────────────────────────────────────────────

    def start(
        self,
        device_id: str,
        sample_rate: int = 100,
        duration: int = 30,
        threshold: float = 2.0,
        window: int = 60,
        rms_window_s: float = 1.0,
        acc_range: int = 4,
        gyro_range: int = 500,
        filter_hz: int = 42,
    ) -> None:
        """Spawn the acquisition subprocess and wait for it to be ready."""
        if self._process and self._process.poll() is None:
            logger.warning("Acquisition process already running (PID %d)", self._process.pid)
            return

        self._config = {
            "device_id": device_id,
            "sample_rate": sample_rate,
            "duration": duration,
            "threshold": threshold,
            "window": window,
            "rms_window_s": rms_window_s,
            "acc_range": acc_range,
            "gyro_range": gyro_range,
            "filter_hz": filter_hz,
        }

        acq_script = str(Path(__file__).with_name("picoquake_acq.py"))
        cmd = [
            sys.executable, acq_script,
            "--device", device_id,
            "--rate", str(sample_rate),
            "--threshold", str(threshold),
            "--duration", str(duration),
            "--window", str(window),
            "--rms-window", str(rms_window_s),
            "--acc-range", str(acc_range),
            "--gyro-range", str(gyro_range),
            "--filter-hz", str(filter_hz),
        ]

        logger.info("Spawning acquisition: %s", " ".join(cmd))
        self._last_error = None
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Drain subprocess stdout in a background thread to prevent pipe
        # buffer deadlock and forward log lines to the app logger.
        self._log_thread = threading.Thread(
            target=self._drain_subprocess_logs,
            daemon=True,
            name="picoquake-log-drain",
        )
        self._log_thread.start()

        # Wait up to 10 s for shared memory to appear and status == 1
        ring_samples = sample_rate * window
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                RingClass = _get_ring_class()
                self._ring = RingClass(ring_samples, create=False)
                if self._ring.status == 1:
                    logger.info("Acquisition process ready (PID %d)", self._process.pid)
                    return
                self._ring.close()
                self._ring = None
            except (FileNotFoundError, ImportError, OSError) as exc:
                # FileNotFoundError: shared memory not created yet
                # ImportError: numpy/picoquake not installed
                # OSError: shared memory size mismatch / other OS error
                logger.debug("Waiting for acquisition: %s", exc)
            time.sleep(0.2)

        # If we got here, the process didn't start properly
        if self._process.poll() is not None:
            out = self._process.stdout.read().decode(errors="replace") if self._process.stdout else ""
            # Extract the last meaningful error line
            err_lines = [l for l in out.strip().splitlines() if "ERROR" in l or "not found" in l.lower()]
            self._last_error = err_lines[-1].strip() if err_lines else f"Process exited with code {self._process.returncode}"
            logger.error("Acquisition process exited early (code %d): %s",
                         self._process.returncode, out[-500:])
        else:
            self._last_error = "Acquisition process not ready after 10 s"
            logger.error(self._last_error)

    def stop(self) -> None:
        """Terminate the acquisition subprocess and clean up shared memory."""
        if self._process and self._process.poll() is None:
            logger.info("Terminating acquisition process (PID %d)…", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)

        # Close stdout so the drain thread unblocks and exits
        if self._process and self._process.stdout:
            try:
                self._process.stdout.close()
            except OSError:
                pass
        if self._log_thread and self._log_thread.is_alive():
            self._log_thread.join(timeout=2)
        self._log_thread = None
        self._process = None

        if self._ring:
            try:
                self._ring.cleanup()
            except Exception:
                self._ring.close()
            self._ring = None

    @property
    def is_running(self) -> bool:
        if self._process is None or self._process.poll() is not None:
            return False
        if self._ring is None:
            return False
        try:
            return self._ring.status == 1
        except Exception:
            return False

    @property
    def info(self) -> dict[str, Any]:
        """Return status info for the /api/services/status endpoint."""
        if not self.is_running:
            result = {"enabled": True, "healthy": False, "mode": "picoquake",
                      "device_id": self._config.get("device_id", "?")}
            if self._last_error:
                result["error"] = self._last_error
            return result
        return {
            "enabled": True,
            "healthy": True,
            "mode": "picoquake",
            "device_id": self._config.get("device_id"),
            "sample_counter": self._ring.sample_counter if self._ring else 0,
            "drop_counter": self._ring.drop_counter if self._ring else 0,
            "recording_flag": self._ring.recording_flag if self._ring else 0,
        }

    # ── Subprocess log forwarding ─────────────────────────────────

    def _drain_subprocess_logs(self) -> None:
        """Read subprocess stdout line-by-line and forward to the app logger.

        Runs in a daemon thread. Prevents the OS pipe buffer from filling up,
        which would deadlock the acquisition subprocess.
        """
        acq_logger = logging.getLogger("rpicoffee.sensor.acq")
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                # Map subprocess log levels to Python logging
                if "ERROR" in line:
                    acq_logger.error("[acq] %s", line)
                elif "WARNING" in line:
                    acq_logger.warning("[acq] %s", line)
                else:
                    acq_logger.info("[acq] %s", line)
        except (OSError, ValueError):
            pass  # pipe closed

    # ── Recording ─────────────────────────────────────────────────

    def trigger_recording(self) -> None:
        """
        Tell the acquisition process to start recording forward.

        Sets ``recording_flag = 1`` in shared memory.  The acquisition process
        will record for *duration* seconds and then set ``recording_flag = 2``.
        """
        if not self._ring:
            raise RuntimeError("PicoQuake not connected – cannot trigger recording")
        if self._ring.recording_flag != 0:
            logger.warning("Recording already in progress (flag=%d)", self._ring.recording_flag)
            return
        logger.info("Triggering forward recording…")
        # Let the acquisition process set start_idx on next write cycle
        self._ring.recording_start_idx = 0
        rate = self._config.get("sample_rate", 100)
        dur = self._config.get("duration", 30)
        self._ring.recording_samples = rate * dur
        self._ring.recording_flag = 1

    async def wait_for_capture(self, timeout: float | None = None) -> list[dict[str, float]]:
        """
        Async wait until ``recording_flag == 2``, then return captured data.

        Parameters
        ----------
        timeout : float, optional
            Maximum seconds to wait.  Defaults to ``duration + 10``.

        Returns
        -------
        list of dicts with keys acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z.
        """
        if not self._ring:
            raise RuntimeError("PicoQuake not connected")

        if timeout is None:
            timeout = self._config.get("duration", 30) + 10.0

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            flag = self._ring.recording_flag
            if flag == 2:
                break
            if flag == 0:
                logger.warning("Recording flag reset to 0 unexpectedly")
                return []
            await asyncio.sleep(0.25)
        else:
            logger.error("Timed out waiting for capture (%.0fs)", timeout)
            return []

        data = self._read_capture()

        # Reset flag so another trigger can fire
        self._ring.recording_flag = 0
        return data

    async def stream_capture(self, batch_interval: float = 0.1, auto_reset: bool = True):
        """
        Async generator that yields progressive batches during recording.

        Yields ``list[dict]`` approximately every *batch_interval* seconds.
        Final yield is the last batch when ``recording_flag == 2``.

        Parameters
        ----------
        batch_interval : float
            Seconds between read cycles.
        auto_reset : bool
            If True (default), reset recording_flag to 0 when done.
            Set to False to keep flag at 2 so the caller controls when
            the sensor is allowed to trigger again.
        """
        if not self._ring:
            raise RuntimeError("PicoQuake not connected")

        timeout = self._config.get("duration", 30) + 10.0
        deadline = time.monotonic() + timeout
        last_read_idx = self._ring.recording_start_idx

        while time.monotonic() < deadline:
            flag = self._ring.recording_flag
            current_idx = self._ring.write_idx

            if current_idx > last_read_idx:
                count = current_idx - last_read_idx
                arr = self._ring.snapshot_range(last_read_idx, count)
                last_read_idx = current_idx
                yield self._array_to_dicts(arr)

            if flag == 2:
                # One final read
                current_idx = self._ring.write_idx
                if current_idx > last_read_idx:
                    arr = self._ring.snapshot_range(last_read_idx, current_idx - last_read_idx)
                    yield self._array_to_dicts(arr)
                if auto_reset:
                    self._ring.recording_flag = 0
                return

            if flag == 0:
                return

            await asyncio.sleep(batch_interval)

        logger.error("Stream capture timed out")
        if auto_reset:
            self._ring.recording_flag = 0

    # ── Data conversion ───────────────────────────────────────────

    def _read_capture(self) -> list[dict[str, float]]:
        """Read the completed recording from the ring buffer."""
        start = self._ring.recording_start_idx
        count = self._ring.recording_samples
        arr = self._ring.snapshot_range(start, count)
        # Normalise elapsed_s (column 0) so the recording starts at t=0
        if arr.shape[0] > 0:
            arr[:, 0] -= arr[0, 0]
        return self._array_to_dicts(arr)

    @staticmethod
    def _array_to_dicts(arr) -> list[dict[str, float]]:
        """Convert a numpy array (N, 7) → list of dicts matching pipeline format."""
        keys = ("elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z")
        return [
            {k: float(arr[i, j]) for j, k in enumerate(keys)}
            for i in range(arr.shape[0])
        ]


# Module-level singleton
picoquake_reader = PicoQuakeReader()
