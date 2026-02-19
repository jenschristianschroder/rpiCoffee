"""
Sensor reader.

Opens a serial port (real USB IMU or mock PTY) and reads IMU data lines.
Buffers a complete reading window and returns structured data for classification.

On Windows with mock enabled, reads directly from the mock's in-memory buffer.
"""

from __future__ import annotations

import asyncio
import logging
import time

import serial

from config import config

logger = logging.getLogger("rpicoffee.sensor.reader")

_FIELDS = ("elapsed_s", "acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z")
_CLASSIFICATION_FIELDS = ("acc_x", "acc_y", "acc_z", "gyro_x", "gyro_y", "gyro_z")


async def read_sensor(port: str | None = None) -> list[dict[str, float]]:
    """
    Read sensor data from the configured serial port.

    Parameters
    ----------
    port : str, optional
        Override the serial port path.  If ``"__mock__"``, reads directly
        from the mock sensor's in-memory buffer (Windows fallback).
        If None, uses config.SENSOR_SERIAL_PORT.

    Returns
    -------
    list of dicts
        Each dict has keys: acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z
    """
    serial_port = port or config.SENSOR_SERIAL_PORT

    # Windows mock – bypass serial entirely
    if serial_port == "__mock__":
        return await _read_from_mock_buffer()

    sample_rate = config.SENSOR_SAMPLE_RATE_HZ
    duration = config.SENSOR_DURATION_S
    expected_samples = sample_rate * duration

    logger.info("Reading sensor on %s (%d Hz, %ds → %d samples expected)",
                serial_port, sample_rate, duration, expected_samples)

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _blocking_read, serial_port, expected_samples)

    logger.info("Sensor read complete: %d samples collected", len(data))
    return data


async def _read_from_mock_buffer() -> list[dict[str, float]]:
    """Read data directly from the mock sensor's in-memory buffer (Windows)."""
    from sensor.mock import mock_sensor

    data = mock_sensor.buffered_data
    if data is None:
        logger.error("Mock sensor buffer is empty")
        return []

    logger.info("Read %d samples from mock buffer (Windows mode)", len(data))
    return list(data)


async def read_sensor_streaming(port: str | None = None):
    """
    Async generator that yields batches of sensor data as they are collected.

    Yields list[dict[str, float]] batches (~10 per second).
    """
    serial_port = port or config.SENSOR_SERIAL_PORT

    if serial_port == "__mock__":
        async for batch in _stream_from_mock_buffer():
            yield batch
        return

    sample_rate = config.SENSOR_SAMPLE_RATE_HZ
    duration = config.SENSOR_DURATION_S
    expected_samples = sample_rate * duration
    batch_size = max(1, sample_rate // 10)  # ~10 batches/sec

    logger.info("Streaming sensor on %s (%d Hz, %ds)", serial_port, sample_rate, duration)

    queue: asyncio.Queue[list[dict[str, float]] | None] = asyncio.Queue()
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _blocking_read_to_queue, serial_port, expected_samples, batch_size, queue)

    while True:
        batch = await queue.get()
        if batch is None:
            break
        yield batch


def _blocking_read_to_queue(
    port: str,
    max_samples: int,
    batch_size: int,
    queue: asyncio.Queue,
) -> None:
    """Blocking serial read that pushes batches into an asyncio queue."""
    batch: list[dict[str, float]] = []
    total = 0
    try:
        ser = serial.Serial(port, baudrate=115200, timeout=1.0)
    except serial.SerialException as exc:
        logger.error("Failed to open serial port %s: %s", port, exc)
        queue.put_nowait(None)
        return

    try:
        header_line = ser.readline().decode("utf-8", errors="replace").strip()
        if header_line:
            logger.debug("Serial header: %s", header_line)

        while total < max_samples:
            raw = ser.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                parts = line.split(",")
                if len(parts) < 7:
                    continue
                row = {
                    "acc_x": float(parts[1]),
                    "acc_y": float(parts[2]),
                    "acc_z": float(parts[3]),
                    "gyro_x": float(parts[4]),
                    "gyro_y": float(parts[5]),
                    "gyro_z": float(parts[6]),
                }
                batch.append(row)
                total += 1
                if len(batch) >= batch_size:
                    queue.put_nowait(batch)
                    batch = []
            except (ValueError, IndexError):
                continue
    except Exception:
        logger.exception("Error reading sensor")
    finally:
        ser.close()
        if batch:
            queue.put_nowait(batch)
        queue.put_nowait(None)  # sentinel


async def _stream_from_mock_buffer():
    """Stream mock buffer data progressively, paced to wall-clock time."""
    from sensor.mock import mock_sensor

    data = mock_sensor.buffered_data
    if not data:
        logger.error("Mock sensor buffer is empty")
        return

    # Determine actual data duration from timestamps
    data_duration = data[-1].get("elapsed_s", 0) - data[0].get("elapsed_s", 0)
    if data_duration <= 0:
        data_duration = config.SENSOR_DURATION_S

    batch_count = 300  # target ~300 batches over the duration (~10/sec for 30s)
    batch_size = max(1, len(data) // batch_count)
    batches = [data[i : i + batch_size] for i in range(0, len(data), batch_size)]
    n = len(batches)

    logger.info("Streaming %d mock samples over %.1fs (%d batches)",
                len(data), data_duration, n)

    t0 = time.monotonic()

    for idx, batch in enumerate(batches):
        yield batch

        # Pace to wall-clock using actual data timestamps
        target = t0 + data_duration * (idx + 1) / n
        wait = target - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)


def _blocking_read(port: str, max_samples: int) -> list[dict[str, float]]:
    """Blocking serial read – runs in a thread executor."""
    data: list[dict[str, float]] = []

    try:
        ser = serial.Serial(port, baudrate=115200, timeout=1.0)
    except serial.SerialException as exc:
        logger.error("Failed to open serial port %s: %s", port, exc)
        return data

    try:
        # Read and discard header line
        header_line = ser.readline().decode("utf-8", errors="replace").strip()
        if header_line:
            logger.debug("Serial header: %s", header_line)

        while len(data) < max_samples:
            raw = ser.readline()
            if not raw:
                # Timeout – no more data
                break

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                parts = line.split(",")
                if len(parts) < 7:
                    continue

                row = {
                    "acc_x": float(parts[1]),
                    "acc_y": float(parts[2]),
                    "acc_z": float(parts[3]),
                    "gyro_x": float(parts[4]),
                    "gyro_y": float(parts[5]),
                    "gyro_z": float(parts[6]),
                }
                data.append(row)
            except (ValueError, IndexError) as exc:
                logger.debug("Skipping malformed line: %s (%s)", line, exc)
                continue

    except Exception:
        logger.exception("Error reading sensor")
    finally:
        ser.close()

    return data
