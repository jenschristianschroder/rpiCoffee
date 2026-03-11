"""
PicoQuake acquisition subprocess.

Connects to a PicoQuake vibration sensor, continuously reads IMU frames
from the device FIFO, and publishes them into a shared-memory ring buffer.

Run standalone::

    python -m sensor.picoquake_acq --device cf79 --rate 100

Or spawned automatically by the main app when ``SENSOR_MODE=picoquake``.

Shared-memory layout
--------------------
``picoquake_ring``  – float32 array of shape ``(RATE*WINDOW, 7)``
                      columns: t_sec, acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

``picoquake_meta``  – packed int64 fields (see META_* constants below).

The acquisition process monitors RMS accelerometer magnitude.  When it
exceeds *threshold* it sets ``recording_flag = 1`` and begins counting
forward for *duration* seconds.  After the window is complete it sets
``recording_flag = 2`` (capture ready) and waits for the app to reset it
to ``0`` before another auto-trigger can fire.
"""

from __future__ import annotations

import argparse
import logging
import struct
import sys
import time
from multiprocessing import shared_memory

import numpy as np

# ── Shared-memory constants ──────────────────────────────────────

COLUMNS = 7  # t_sec, acc_x, acc_y, acc_z, gyro_x, gyro_y, gyro_z

SHM_RING_NAME = "picoquake_ring"
SHM_META_NAME = "picoquake_meta"

# Metadata struct layout (8 bytes each, packed as qqqqqqq = 56 bytes)
#   0  write_idx           – current write position in ring (wraps)
#   1  sample_counter      – total samples since process start
#   2  drop_counter        – detected FIFO drops
#   3  recording_flag      – 0=idle, 1=recording, 2=capture ready
#   4  status              – 0=stopped, 1=running, 2=error
#   5  recording_start_idx – ring index when recording began
#   6  recording_samples   – number of samples to capture
META_FIELDS = 7
META_SIZE = META_FIELDS * 8  # 56 bytes
META_FMT = f"{META_FIELDS}q"

# Device sample-rate enum lookup (lazy import of picoquake)
_RATE_MAP: dict[int, object] | None = None


def _rate_enum(hz: int):
    """Return the picoquake.SampleRate enum for *hz*."""
    global _RATE_MAP
    if _RATE_MAP is None:
        import picoquake
        _RATE_MAP = {
            100: picoquake.SampleRate.hz_100,
            200: picoquake.SampleRate.hz_200,
            500: picoquake.SampleRate.hz_500,
            1000: picoquake.SampleRate.hz_1000,
        }
    if hz not in _RATE_MAP:
        raise ValueError(f"Unsupported sample rate {hz} Hz; choose from {sorted(_RATE_MAP)}")
    return _RATE_MAP[hz]


# ── SharedRingBuffer ─────────────────────────────────────────────

class SharedRingBuffer:
    """Manages the shared-memory ring buffer + metadata block."""

    def __init__(self, ring_samples: int, *, create: bool = False):
        self.ring_samples = ring_samples
        self.ring_size = ring_samples * COLUMNS * 4  # float32

        if create:
            self._create_or_reuse()
        else:
            self.shm_ring = shared_memory.SharedMemory(name=SHM_RING_NAME)
            self.shm_meta = shared_memory.SharedMemory(name=SHM_META_NAME)

        self.ring = np.ndarray(
            (ring_samples, COLUMNS), dtype=np.float32, buffer=self.shm_ring.buf
        )

        if create:
            self.ring[:] = 0
            self._write_meta(0, 0, 0, 0, 0, 0, 0)

    # -- creation helpers --------------------------------------------------

    def _create_or_reuse(self):
        """Try to attach to existing shm; create if missing."""
        for name, size in [(SHM_RING_NAME, self.ring_size), (SHM_META_NAME, META_SIZE)]:
            try:
                existing = shared_memory.SharedMemory(name=name)
                existing.close()
                try:
                    existing.unlink()
                except Exception:
                    pass
            except FileNotFoundError:
                pass

        try:
            self.shm_ring = shared_memory.SharedMemory(name=SHM_RING_NAME)
            self.shm_meta = shared_memory.SharedMemory(name=SHM_META_NAME)
        except FileNotFoundError:
            self.shm_ring = shared_memory.SharedMemory(
                name=SHM_RING_NAME, create=True, size=self.ring_size
            )
            self.shm_meta = shared_memory.SharedMemory(
                name=SHM_META_NAME, create=True, size=META_SIZE
            )

    # -- metadata accessors ------------------------------------------------

    def _write_meta(self, *fields: int):
        self.shm_meta.buf[:META_SIZE] = struct.pack(META_FMT, *fields)

    def _read_meta(self) -> tuple[int, ...]:
        return struct.unpack(META_FMT, bytes(self.shm_meta.buf[:META_SIZE]))

    def _get_field(self, idx: int) -> int:
        return self._read_meta()[idx]

    def _set_field(self, idx: int, value: int):
        fields = list(self._read_meta())
        fields[idx] = value
        self._write_meta(*fields)

    @property
    def write_idx(self) -> int:
        return self._get_field(0)

    @write_idx.setter
    def write_idx(self, v: int):
        self._set_field(0, v)

    @property
    def sample_counter(self) -> int:
        return self._get_field(1)

    @sample_counter.setter
    def sample_counter(self, v: int):
        self._set_field(1, v)

    @property
    def drop_counter(self) -> int:
        return self._get_field(2)

    @drop_counter.setter
    def drop_counter(self, v: int):
        self._set_field(2, v)

    @property
    def recording_flag(self) -> int:
        return self._get_field(3)

    @recording_flag.setter
    def recording_flag(self, v: int):
        self._set_field(3, v)

    @property
    def status(self) -> int:
        return self._get_field(4)

    @status.setter
    def status(self, v: int):
        self._set_field(4, v)

    @property
    def recording_start_idx(self) -> int:
        return self._get_field(5)

    @recording_start_idx.setter
    def recording_start_idx(self, v: int):
        self._set_field(5, v)

    @property
    def recording_samples(self) -> int:
        return self._get_field(6)

    @recording_samples.setter
    def recording_samples(self, v: int):
        self._set_field(6, v)

    # -- data operations ---------------------------------------------------

    def write_samples(self, samples: list[list[float]]):
        """Append rows to the ring buffer (each row = 7 floats)."""
        for row in samples:
            idx = self.write_idx % self.ring_samples
            self.ring[idx, :] = row
            self.write_idx += 1
            self.sample_counter += 1

    def snapshot_range(self, start_idx: int, count: int) -> np.ndarray:
        """Copy *count* samples starting at absolute *start_idx*."""
        result = np.zeros((count, COLUMNS), dtype=np.float32)
        for i in range(count):
            ring_idx = (start_idx + i) % self.ring_samples
            result[i, :] = self.ring[ring_idx, :].copy()
        return result

    def snapshot_last_n(self, n: int) -> np.ndarray:
        end = self.write_idx
        start = max(0, end - n)
        return self.snapshot_range(start, end - start)

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        self.shm_ring.close()
        self.shm_meta.close()

    def cleanup(self):
        self.shm_ring.close()
        try:
            self.shm_ring.unlink()
        except Exception:
            pass
        self.shm_meta.close()
        try:
            self.shm_meta.unlink()
        except Exception:
            pass


# ── Main acquisition loop ────────────────────────────────────────

def main_acquisition():
    parser = argparse.ArgumentParser(description="PicoQuake acquisition process")
    parser.add_argument("--device", "-d", required=True, help="PicoQuake device ID (e.g. cf79)")
    parser.add_argument("--rate", "-r", type=int, default=100,
                        choices=[100, 200, 500, 1000], help="Sample rate in Hz")
    parser.add_argument("--threshold", "-t", type=float, default=2.0,
                        help="RMS accel threshold (g) for auto-trigger")
    parser.add_argument("--duration", type=int, default=30,
                        help="Recording window in seconds (forward capture)")
    parser.add_argument("--rms-window", type=float, default=1.0,
                        help="RMS averaging window in seconds (e.g. 0.2 for faster trigger)")
    parser.add_argument("--window", "-w", type=int, default=60,
                        help="Ring buffer length in seconds")
    parser.add_argument("--acc-range", type=int, default=4,
                        choices=[2, 4, 8, 16], help="Accelerometer range in g")
    parser.add_argument("--gyro-range", type=int, default=500,
                        choices=[250, 500, 1000, 2000], help="Gyroscope range in dps")
    parser.add_argument("--filter-hz", type=int, default=42,
                        choices=[42, 84, 170, 734],
                        help="Low-pass filter cutoff in Hz")
    parser.add_argument("--trigger-sources", default="accel",
                        choices=["accel", "gyro", "both"],
                        help="Which signal(s) trigger auto-capture")
    parser.add_argument("--trigger-combine-mode", default="or",
                        choices=["or", "and"],
                        help="Combine mode when both sources active: 'or' or 'and'")
    parser.add_argument("--gyro-threshold", type=float, default=10.0,
                        help="RMS gyro threshold (dps) for auto-trigger")
    parser.add_argument("--gyro-rms-window", type=float, default=1.0,
                        help="Gyro RMS averaging window in seconds")
    parser.add_argument("--warmup", type=int, default=5,
                        help="Seconds after start to suppress auto-trigger (sensor stabilisation)")
    parser.add_argument("--cooldown", type=int, default=10,
                        help="Seconds to wait after a capture before allowing a new auto-trigger")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [ACQUIRE] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("picoquake_acq")

    ring_samples = args.rate * args.window
    record_samples = args.rate * args.duration
    sample_interval = 1.0 / args.rate
    batch_size = min(50, max(1, args.rate // 10))

    logger.info("Creating shared-memory ring buffer (%d samples, %ds)…", ring_samples, args.window)
    ring = SharedRingBuffer(ring_samples, create=True)
    ring.status = 0

    import picoquake

    _ACC_RANGE_MAP = {
        2: picoquake.AccRange.g_2,
        4: picoquake.AccRange.g_4,
        8: picoquake.AccRange.g_8,
        16: picoquake.AccRange.g_16,
    }
    _GYRO_RANGE_MAP = {
        250: picoquake.GyroRange.dps_250,
        500: picoquake.GyroRange.dps_500,
        1000: picoquake.GyroRange.dps_1000,
        2000: picoquake.GyroRange.dps_2000,
    }
    _FILTER_MAP = {
        42: picoquake.Filter.hz_42,
        84: picoquake.Filter.hz_84,
        170: picoquake.Filter.hz_170,
        734: picoquake.Filter.hz_734,
    }

    # ── Retry / reconnection constants ───────────────────────────
    MAX_RETRIES = 5
    INITIAL_RETRY_DELAY = 3.0
    MAX_RETRY_DELAY = 30.0

    def _connect_and_configure():
        """Connect to the PicoQuake sensor and configure it. Returns device."""
        logger.info("Connecting to PicoQuake device '%s'…", args.device)
        dev = picoquake.PicoQuake(args.device)
        dev.configure(
            sample_rate=_rate_enum(args.rate),
            filter_hz=_FILTER_MAP[args.filter_hz],
            acc_range=_ACC_RANGE_MAP[args.acc_range],
            gyro_range=_GYRO_RANGE_MAP[args.gyro_range],
        )
        logger.info(
            "Device configured: %d Hz | acc_range=%dg | gyro_range=%d dps | filter=%d Hz"
            " | accel_thr=%.3fg | gyro_thr=%.1fdps | trigger=%s(%s) | duration=%ds | warmup=%ds | cooldown=%ds",
            args.rate, args.acc_range, args.gyro_range, args.filter_hz,
            args.threshold, args.gyro_threshold,
            args.trigger_sources, args.trigger_combine_mode, args.duration,
            args.warmup, args.cooldown)
        return dev

    # ── Initial connection ────────────────────────────────────────
    try:
        device = _connect_and_configure()
    except Exception as exc:
        logger.error("Could not connect to device: %s", exc)
        ring.status = 2
        ring.cleanup()
        sys.exit(1)

    # ── Acquisition with retry on connection loss ────────────────
    retry_delay = INITIAL_RETRY_DELAY
    attempt = 0

    while True:
        logger.info("Starting continuous acquisition…")
        device.start_continuos()
        ring.status = 1

        t0 = time.monotonic()
        warmup_until = t0 + args.warmup  # suppress auto-trigger until sensor stabilises
        cooldown_until = 0.0  # suppress auto-trigger after a capture completes
        prev_flag = 0  # track flag transitions for cooldown
        if args.warmup > 0:
            logger.info("Warmup period: suppressing auto-trigger for %ds", args.warmup)
        last_log = t0
        batch: list[list[float]] = []
        samples_since_log = 0

        # Auto-trigger state
        rms_window_size = max(1, int(args.rate * args.rms_window))  # configurable accel RMS window
        gyro_rms_window_size = max(1, int(args.rate * args.gyro_rms_window))  # configurable gyro RMS window
        recent_accel: list[float] = []
        recent_gyro: list[float] = []
        recording_count = 0  # samples captured since recording started
        use_accel = args.trigger_sources in ("accel", "both")
        use_gyro = args.trigger_sources in ("gyro", "both")

        try:
            while True:
                frames = device.read(num=batch_size, timeout=1.0)
                if not frames:
                    continue

                now = time.monotonic()
                # Reset retry state on successful reads
                attempt = 0
                retry_delay = INITIAL_RETRY_DELAY

                for frame in frames:
                    sample_idx = ring.sample_counter + len(batch)
                    t = sample_idx * sample_interval
                    row = [t, frame.acc_x, frame.acc_y, frame.acc_z,
                           frame.gyro_x, frame.gyro_y, frame.gyro_z]
                    batch.append(row)

                    # Track accel magnitude for auto-trigger
                    if use_accel:
                        mag = (frame.acc_x**2 + frame.acc_y**2 + frame.acc_z**2) ** 0.5
                        recent_accel.append(mag)
                        if len(recent_accel) > rms_window_size:
                            recent_accel.pop(0)

                    # Track gyro magnitude for auto-trigger
                    if use_gyro:
                        gyro_mag = (frame.gyro_x**2 + frame.gyro_y**2 + frame.gyro_z**2) ** 0.5
                        recent_gyro.append(gyro_mag)
                        if len(recent_gyro) > gyro_rms_window_size:
                            recent_gyro.pop(0)

                samples_since_log += len(frames)

                # Flush batch to ring
                if len(batch) >= 50 or (now - t0) - (ring.sample_counter * sample_interval) > 0.1:
                    ring.write_samples(batch)
                    batch.clear()

                # ── Auto-trigger logic ───────────────────────────────
                flag = ring.recording_flag

                # Detect capture-complete → idle transition (2 → 0) to start cooldown
                if prev_flag == 2 and flag == 0 and args.cooldown > 0:
                    cooldown_until = time.monotonic() + args.cooldown
                    logger.info("Cooldown period: suppressing auto-trigger for %ds", args.cooldown)
                prev_flag = flag

                if flag == 0:
                    # Suppress auto-trigger during warmup period
                    if time.monotonic() < warmup_until:
                        continue

                    # Suppress auto-trigger during cooldown period
                    if time.monotonic() < cooldown_until:
                        continue

                    accel_triggered = False
                    gyro_triggered = False
                    accel_rms = 0.0
                    gyro_rms = 0.0

                    # Accel RMS check
                    if use_accel and len(recent_accel) >= rms_window_size:
                        mean_mag = sum(recent_accel) / len(recent_accel)
                        accel_rms = (sum((a - mean_mag) ** 2 for a in recent_accel) / len(recent_accel)) ** 0.5
                        accel_triggered = accel_rms > args.threshold

                    # Gyro RMS check
                    if use_gyro and len(recent_gyro) >= gyro_rms_window_size:
                        mean_mag_g = sum(recent_gyro) / len(recent_gyro)
                        gyro_rms = (sum((g - mean_mag_g) ** 2 for g in recent_gyro) / len(recent_gyro)) ** 0.5
                        gyro_triggered = gyro_rms > args.gyro_threshold

                    # Combine trigger decision
                    if args.trigger_sources == "accel":
                        should_trigger = accel_triggered
                    elif args.trigger_sources == "gyro":
                        should_trigger = gyro_triggered
                    else:  # both
                        if args.trigger_combine_mode == "and":
                            should_trigger = accel_triggered and gyro_triggered
                        else:  # or
                            should_trigger = accel_triggered or gyro_triggered

                    if should_trigger:
                        parts = []
                        if accel_triggered:
                            parts.append("accel_rms=%.3fg>%.1fg" % (accel_rms, args.threshold))
                        if gyro_triggered:
                            parts.append("gyro_rms=%.1fdps>%.1fdps" % (gyro_rms, args.gyro_threshold))
                        logger.info("Trigger detected! %s → recording %ds",
                                    ", ".join(parts), args.duration)
                        ring.recording_start_idx = ring.write_idx
                        ring.recording_samples = record_samples
                        ring.recording_flag = 1
                        recording_count = 0

                elif flag == 1:
                    # Manual trigger (set by app) also enters here
                    if ring.recording_start_idx == 0:
                        # App just set flag=1 but didn't set start idx yet
                        ring.recording_start_idx = ring.write_idx
                        ring.recording_samples = record_samples

                    recording_count = ring.write_idx - ring.recording_start_idx
                    if recording_count >= ring.recording_samples:
                        logger.info("Recording complete (%d samples captured)", recording_count)
                        ring.recording_flag = 2

                # flag == 2: capture ready, waiting for app to reset to 0

                # ── Periodic logging ─────────────────────────────────
                if now - last_log >= 5.0:
                    actual_rate = samples_since_log / (now - last_log)
                    # Accel RMS
                    _na = max(1, len(recent_accel))
                    _mean_a = sum(recent_accel) / _na if recent_accel else 0
                    accel_rms_log = (sum((a - _mean_a) ** 2 for a in recent_accel) / _na) ** 0.5 if recent_accel else 0
                    # Gyro RMS
                    _ng = max(1, len(recent_gyro))
                    _mean_g = sum(recent_gyro) / _ng if recent_gyro else 0
                    gyro_rms_log = (sum((g - _mean_g) ** 2 for g in recent_gyro) / _ng) ** 0.5 if recent_gyro else 0
                    logger.info(
                        "samples=%d  rate=%.1f Hz  drops=%d  accel_rms=%.3fg"
                        "  gyro_rms=%.1fdps  trigger=%s  recording=%d",
                        ring.sample_counter, actual_rate, ring.drop_counter,
                        accel_rms_log, gyro_rms_log, args.trigger_sources, flag)
                    last_log = now
                    samples_since_log = 0

        except KeyboardInterrupt:
            logger.info("Stopping acquisition (Ctrl+C)")
            break  # exit retry loop — clean shutdown

        except Exception as exc:
            # Flush any pending samples
            if batch:
                ring.write_samples(batch)
                batch.clear()

            attempt += 1
            logger.error("Acquisition error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)

            # Try to stop the device gracefully
            try:
                device.stop()
            except Exception:
                pass

            if attempt >= MAX_RETRIES:
                logger.error("Max retries (%d) exhausted — giving up", MAX_RETRIES)
                ring.status = 2
                break

            # Exponential backoff before reconnecting
            logger.info("Retrying in %.0fs…", retry_delay)
            ring.status = 0  # signal "stopped" while we reconnect
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, MAX_RETRY_DELAY)

            # Reconnect
            try:
                device = _connect_and_configure()
            except Exception as reconn_exc:
                logger.error("Reconnection failed: %s", reconn_exc)
                if attempt >= MAX_RETRIES:
                    ring.status = 2
                    break
                continue  # back to top of retry loop

    # ── Final cleanup ────────────────────────────────────────────
    try:
        device.stop()
    except Exception:
        pass
    ring.status = 0
    logger.info("Total samples acquired: %d", ring.sample_counter)
    ring.cleanup()


if __name__ == "__main__":
    main_acquisition()
