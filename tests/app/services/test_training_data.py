"""Tests for app/services/training_data.py."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from services.training_data import (
    delete_all_training_data,
    delete_sample_file,
    delete_training_file,
    get_sample_file_path,
    get_training_file_path,
    list_sample_files,
    list_training_data,
    promote_training_to_sample,
    save_recording,
    save_uploaded_sample_file,
    save_uploaded_training_file,
)


@pytest.fixture(autouse=True)
def _patch_dirs(tmp_path, monkeypatch):
    """Redirect training data storage to a temp directory."""
    import services.training_data as td_mod
    monkeypatch.setattr(td_mod, "TRAINING_DIR", tmp_path / "training")
    monkeypatch.setattr(td_mod, "DATA_DIR", tmp_path)


class TestSaveRecording:
    def test_saves_csv(self, tmp_path, sample_sensor_data):
        filepath = save_recording("espresso", sample_sensor_data[:5])
        p = Path(filepath)
        assert p.exists()
        assert p.suffix == ".csv"
        with open(p) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 5
        assert rows[0]["label"] == "espresso"

    def test_creates_label_directory(self, tmp_path, sample_sensor_data):
        save_recording("cappuccino", sample_sensor_data[:1])
        label_dir = tmp_path / "training" / "cappuccino"
        assert label_dir.is_dir()


class TestListTrainingData:
    def test_empty(self, tmp_path):
        result = list_training_data()
        assert result == {}

    def test_with_files(self, tmp_path, sample_sensor_data):
        save_recording("espresso", sample_sensor_data[:3])
        save_recording("black", sample_sensor_data[:2])
        result = list_training_data()
        assert "espresso" in result
        assert "black" in result
        assert len(result["espresso"]) == 1


class TestDeleteTrainingFile:
    def test_delete_existing(self, tmp_path, sample_sensor_data):
        filepath = save_recording("espresso", sample_sensor_data[:3])
        p = Path(filepath)
        filename = p.name
        assert delete_training_file("espresso", filename) is True
        assert not p.exists()

    def test_delete_nonexistent(self, tmp_path):
        assert delete_training_file("espresso", "nonexistent.csv") is False


class TestDeleteAllTrainingData:
    def test_delete_by_label(self, tmp_path, sample_sensor_data):
        save_recording("espresso", sample_sensor_data[:2])
        save_recording("black", sample_sensor_data[:2])
        count = delete_all_training_data(label="espresso")
        assert count == 1
        # black should still exist
        assert list_training_data().get("black") is not None

    def test_delete_all(self, tmp_path, sample_sensor_data):
        save_recording("espresso", sample_sensor_data[:2])
        save_recording("black", sample_sensor_data[:2])
        count = delete_all_training_data()
        assert count == 2
        assert list_training_data() == {}

    def test_delete_empty(self, tmp_path):
        count = delete_all_training_data()
        assert count == 0


class TestSampleFiles:
    def test_list_sample_files(self, tmp_path):
        (tmp_path / "espresso-20260303.csv.sample").write_text("a,b\n1,2\n")
        result = list_sample_files()
        assert len(result) == 1
        assert result[0]["label"] == "espresso"

    def test_delete_sample_file(self, tmp_path):
        (tmp_path / "test.csv.sample").write_text("data")
        assert delete_sample_file("test.csv.sample") is True
        assert not (tmp_path / "test.csv.sample").exists()

    def test_delete_sample_not_found(self):
        assert delete_sample_file("missing.csv.sample") is False

    def test_delete_sample_wrong_extension(self, tmp_path):
        (tmp_path / "test.txt").write_text("data")
        assert delete_sample_file("test.txt") is False

    def test_get_sample_file_path(self, tmp_path):
        (tmp_path / "test.csv.sample").write_text("data")
        p = get_sample_file_path("test.csv.sample")
        assert p is not None

    def test_get_sample_file_path_missing(self):
        assert get_sample_file_path("missing.csv.sample") is None


class TestTrainingFilePath:
    def test_get_training_file_path(self, tmp_path, sample_sensor_data):
        filepath = save_recording("espresso", sample_sensor_data[:2])
        p = Path(filepath)
        result = get_training_file_path("espresso", p.name)
        assert result is not None
        assert result.exists()

    def test_get_training_file_path_missing(self):
        assert get_training_file_path("espresso", "missing.csv") is None


class TestUploadTrainingFile:
    def test_upload_valid(self, tmp_path):
        content = b"label,elapsed_s,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z\nesp,0.1,1,2,3,4,5,6\n"
        name = save_uploaded_training_file("espresso", "test.csv", content)
        assert name == "test.csv"
        assert (tmp_path / "training" / "espresso" / "test.csv").exists()

    def test_upload_wrong_extension(self):
        with pytest.raises(ValueError, match="Only .csv"):
            save_uploaded_training_file("espresso", "test.txt", b"data")

    def test_upload_empty(self):
        with pytest.raises(ValueError, match="empty"):
            save_uploaded_training_file("espresso", "test.csv", b"")

    def test_upload_missing_columns(self):
        with pytest.raises(ValueError, match="header must contain"):
            save_uploaded_training_file("espresso", "test.csv", b"a,b\n1,2\n")


class TestUploadSampleFile:
    def test_upload_valid(self, tmp_path):
        content = b"label,elapsed_s,acc_x,acc_y,acc_z,gyro_x,gyro_y,gyro_z\nesp,0.1,1,2,3,4,5,6\n"
        name = save_uploaded_sample_file("test.csv.sample", content)
        assert name == "test.csv.sample"
        assert (tmp_path / "test.csv.sample").exists()

    def test_upload_wrong_extension(self):
        with pytest.raises(ValueError, match="Only .csv.sample"):
            save_uploaded_sample_file("test.csv", b"data")


class TestPromoteToSample:
    def test_promote_existing(self, tmp_path, sample_sensor_data):
        filepath = save_recording("espresso", sample_sensor_data[:2])
        p = Path(filepath)
        new_name = promote_training_to_sample("espresso", p.name)
        assert new_name is not None
        assert (tmp_path / new_name).exists()

    def test_promote_missing(self):
        result = promote_training_to_sample("espresso", "missing.csv")
        assert result is None
