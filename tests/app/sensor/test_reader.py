"""Tests for app/sensor/reader.py — filter_sensor_channels, read_sensor, read_sensor_streaming."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sensor.reader import filter_sensor_channels, read_sensor, read_sensor_streaming


def _sample_row():
    return {"acc_x": 1.0, "acc_y": 2.0, "acc_z": 3.0,
            "gyro_x": 4.0, "gyro_y": 5.0, "gyro_z": 6.0}


class TestFilterSensorChannels:
    def test_all_channels_enabled(self):
        data = [_sample_row()]
        with patch("sensor.reader.config") as mock_cfg:
            mock_cfg.SENSOR_ACC_ENABLED = True
            mock_cfg.SENSOR_GYRO_ENABLED = True
            result = filter_sensor_channels(data)
        assert result[0]["acc_x"] == 1.0
        assert result[0]["gyro_x"] == 4.0

    def test_acc_disabled(self):
        data = [_sample_row()]
        with patch("sensor.reader.config") as mock_cfg:
            mock_cfg.SENSOR_ACC_ENABLED = False
            mock_cfg.SENSOR_GYRO_ENABLED = True
            result = filter_sensor_channels(data)
        assert result[0]["acc_x"] == 0.0
        assert result[0]["acc_y"] == 0.0
        assert result[0]["acc_z"] == 0.0
        assert result[0]["gyro_x"] == 4.0

    def test_gyro_disabled(self):
        data = [_sample_row()]
        with patch("sensor.reader.config") as mock_cfg:
            mock_cfg.SENSOR_ACC_ENABLED = True
            mock_cfg.SENSOR_GYRO_ENABLED = False
            result = filter_sensor_channels(data)
        assert result[0]["acc_x"] == 1.0
        assert result[0]["gyro_x"] == 0.0
        assert result[0]["gyro_y"] == 0.0
        assert result[0]["gyro_z"] == 0.0

    def test_both_disabled(self):
        data = [_sample_row()]
        with patch("sensor.reader.config") as mock_cfg:
            mock_cfg.SENSOR_ACC_ENABLED = False
            mock_cfg.SENSOR_GYRO_ENABLED = False
            result = filter_sensor_channels(data)
        assert all(v == 0.0 for v in result[0].values())

    def test_empty_data(self):
        with patch("sensor.reader.config") as mock_cfg:
            mock_cfg.SENSOR_ACC_ENABLED = True
            mock_cfg.SENSOR_GYRO_ENABLED = True
            result = filter_sensor_channels([])
        assert result == []


class TestReadSensor:
    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_read_mock_buffer(self, mock_cfg):
        mock_cfg.SENSOR_MODE = "mock"
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True
        expected = [_sample_row()]
        with patch("sensor.reader._read_from_mock_buffer", new_callable=AsyncMock, return_value=expected):
            result = await read_sensor(port=None)
        assert result == expected

    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_read_mock_by_port_sentinel(self, mock_cfg):
        mock_cfg.SENSOR_MODE = "serial"  # overridden by __mock__ port
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True
        expected = [_sample_row()]
        with patch("sensor.reader._read_from_mock_buffer", new_callable=AsyncMock, return_value=expected):
            result = await read_sensor(port="__mock__")
        assert result == expected


class TestReadFromMockBuffer:
    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_empty_buffer(self, mock_cfg):
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True
        mock_ms = MagicMock()
        mock_ms.buffered_data = None
        with patch.dict("sys.modules", {"sensor.mock": MagicMock(mock_sensor=mock_ms)}):
            from sensor.reader import _read_from_mock_buffer
            result = await _read_from_mock_buffer()
        assert result == []

    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_non_empty_buffer(self, mock_cfg):
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True
        expected = [_sample_row()]
        mock_ms = MagicMock()
        mock_ms.buffered_data = expected
        with patch.dict("sys.modules", {"sensor.mock": MagicMock(mock_sensor=mock_ms)}):
            from sensor.reader import _read_from_mock_buffer
            result = await _read_from_mock_buffer()
        assert len(result) == 1


class TestReadSensorStreaming:
    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_streaming_mock(self, mock_cfg):
        mock_cfg.SENSOR_MODE = "mock"
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True

        async def fake_stream():
            yield [_sample_row()]
            yield [_sample_row()]

        with patch("sensor.reader._stream_from_mock_buffer", side_effect=fake_stream):
            batches = []
            async for batch in read_sensor_streaming(port=None):
                batches.append(batch)
        assert len(batches) == 2

    @pytest.mark.asyncio
    @patch("sensor.reader.config")
    async def test_streaming_mock_by_port(self, mock_cfg):
        mock_cfg.SENSOR_MODE = "serial"  # overridden by __mock__ port
        mock_cfg.SENSOR_ACC_ENABLED = True
        mock_cfg.SENSOR_GYRO_ENABLED = True

        async def fake_stream():
            yield [_sample_row()]

        with patch("sensor.reader._stream_from_mock_buffer", side_effect=fake_stream):
            batches = []
            async for batch in read_sensor_streaming(port="__mock__"):
                batches.append(batch)
        assert len(batches) == 1
