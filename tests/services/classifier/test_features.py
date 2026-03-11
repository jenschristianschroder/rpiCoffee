"""Tests for services/classifier/features.py — feature extraction."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SVC_DIR = str(Path(__file__).resolve().parent.parent.parent.parent / "services" / "classifier")


def _import_features():
    mod_key = "svc_classifier_features"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    if _SVC_DIR not in sys.path:
        sys.path.insert(0, _SVC_DIR)
    spec = importlib.util.spec_from_file_location(mod_key, Path(_SVC_DIR) / "features.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = mod
    spec.loader.exec_module(mod)
    return mod


_features = _import_features()
SENSOR_COLS = _features.SENSOR_COLS
extract_features = _features.extract_features
extract_features_from_array = _features.extract_features_from_array
get_feature_names = _features.get_feature_names
normalise_columns = _features.normalise_columns


@pytest.fixture()
def sample_recording() -> list[dict[str, float]]:
    np.random.seed(42)
    return [
        {col: float(np.random.randn()) for col in SENSOR_COLS}
        for _ in range(100)
    ]


class TestNormaliseColumns:
    def test_maps_alternate_names(self):
        df = pd.DataFrame({"a_x": [1], "a_y": [2], "a_z": [3], "g_x": [4], "g_y": [5], "g_z": [6]})
        result = normalise_columns(df)
        assert list(result.columns) == SENSOR_COLS

    def test_keeps_canonical_names(self):
        df = pd.DataFrame({col: [0.0] for col in SENSOR_COLS})
        result = normalise_columns(df)
        assert list(result.columns) == SENSOR_COLS


class TestExtractFeaturesFromArray:
    def test_basic_features(self):
        values = np.array([1.0, -1.0, 2.0, -2.0, 0.0])
        features = extract_features_from_array(values, "test")
        assert "test_mean" in features
        assert "test_std" in features
        assert "test_min" in features
        assert "test_max" in features
        assert "test_rms" in features
        assert "test_p2p" in features
        assert "test_mav" in features
        assert "test_zcr" in features
        assert features["test_min"] == -2.0
        assert features["test_max"] == 2.0

    def test_zero_crossing_rate(self):
        # Signal: [1, -1, 1, -1] has 3 zero crossings out of 4 samples
        values = np.array([1.0, -1.0, 1.0, -1.0])
        features = extract_features_from_array(values, "t")
        assert features["t_zcr"] == 3 / 4


class TestExtractFeatures:
    def test_returns_52_features(self, sample_recording):
        features = extract_features(sample_recording)
        assert len(features) == 52

    def test_feature_names_match(self, sample_recording):
        features = extract_features(sample_recording)
        expected_names = get_feature_names()
        assert set(features.keys()) == set(expected_names)

    def test_cross_axis_features(self, sample_recording):
        features = extract_features(sample_recording)
        assert "accel_mag_mean" in features
        assert "gyro_mag_std" in features
        assert features["accel_mag_mean"] > 0


class TestGetFeatureNames:
    def test_length(self):
        names = get_feature_names()
        assert len(names) == 52

    def test_starts_with_axis(self):
        names = get_feature_names()
        assert names[0] == "acc_x_mean"
        assert names[-1] == "gyro_mag_std"
