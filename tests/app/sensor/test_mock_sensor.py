"""Tests for app/sensor/mock.py — MockSensor."""

from __future__ import annotations

from unittest.mock import patch

from sensor.mock import MockSensor, _is_spike_row, _load_csv_as_dicts, _load_csv_rows

_CSV_HEADER = "label,elapsed_s,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z\n"
_CSV_NORMAL_ROW = "espresso,0.01,0.1,-0.2,9.8,0.5,-0.3,0.1\n"
_CSV_SPIKE_ROW = "espresso,0.02,0.1,-0.2,9.8,300.0,-0.3,0.1\n"


class TestLoadCsvAsDicts:
    def test_load_sample_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW + "espresso,0.02,0.2,-0.1,9.7,0.4,-0.2,0.0\n")
        data = _load_csv_as_dicts(csv_file)
        assert len(data) == 2
        assert data[0]["acc_x"] == 0.1
        assert data[1]["elapsed_s"] == 0.02

    def test_filters_spikes(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW + _CSV_SPIKE_ROW)
        data = _load_csv_as_dicts(csv_file)
        assert len(data) == 1  # spike row filtered out


class TestLoadCsvRows:
    def test_load_csv_rows(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW)
        rows = _load_csv_rows(csv_file)
        assert len(rows) == 1
        # Should not contain the label column
        assert "espresso" not in rows[0]
        assert "0.01" in rows[0]

    def test_filters_spikes(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW + _CSV_SPIKE_ROW)
        rows = _load_csv_rows(csv_file)
        assert len(rows) == 1


class TestIsSpikeRow:
    def test_normal_row(self):
        row = {"gyro_x": "5.0", "gyro_y": "-3.0", "gyro_z": "1.0"}
        assert _is_spike_row(row) is False

    def test_spike_row(self):
        row = {"gyro_x": "300.0", "gyro_y": "0.0", "gyro_z": "0.0"}
        assert _is_spike_row(row) is True

    def test_invalid_value(self):
        row = {"gyro_x": "NaN", "gyro_y": "0.0", "gyro_z": "0.0"}
        # ValueError in float() → returns False
        assert _is_spike_row(row) is False

    def test_missing_key(self):
        row = {"gyro_x": "5.0"}  # missing gyro_y, gyro_z
        assert _is_spike_row(row) is False


class TestMockSensor:
    def test_singleton_pattern(self):
        from sensor.mock import mock_sensor
        assert isinstance(mock_sensor, MockSensor)

    def test_initial_state(self):
        sensor = MockSensor()
        assert sensor.port is None
        assert sensor._running is False
        assert sensor.is_running is False
        assert sensor.buffered_data is None

    def test_stop_when_not_running(self):
        sensor = MockSensor()
        sensor.stop()  # should not raise
        assert sensor.port is None

    @patch("sensor.mock._IS_WINDOWS", True)
    @patch("sensor.mock.config")
    def test_start_windows_no_csvs(self, mock_config, tmp_path):
        mock_config.SENSOR_DURATION_S = 30
        sensor = MockSensor()
        with patch("sensor.mock.DATA_DIR", tmp_path):
            port = sensor.start()
        assert port == "__mock__"
        assert sensor.buffered_data == []

    @patch("sensor.mock._IS_WINDOWS", True)
    @patch("sensor.mock.config")
    def test_start_windows_with_csv(self, mock_config, tmp_path):
        mock_config.SENSOR_DURATION_S = 30
        csv_file = tmp_path / "espresso.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW)
        sensor = MockSensor()
        with patch("sensor.mock.DATA_DIR", tmp_path):
            port = sensor.start()
        assert port == "__mock__"
        assert len(sensor.buffered_data) == 1
        assert sensor.is_running is True

    @patch("sensor.mock._IS_WINDOWS", True)
    @patch("sensor.mock.config")
    def test_start_windows_restarts(self, mock_config, tmp_path):
        mock_config.SENSOR_DURATION_S = 30
        csv_file = tmp_path / "espresso.csv"
        csv_file.write_text(_CSV_HEADER + _CSV_NORMAL_ROW)
        sensor = MockSensor()
        sensor._running = True  # simulate already running
        with patch("sensor.mock.DATA_DIR", tmp_path):
            port = sensor.start()  # should not raise on Windows
        assert port == "__mock__"
