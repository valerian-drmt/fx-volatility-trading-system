"""Contract tests for core.config.vol_params : bounds + cross-field validators."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import (
    CalibrationConfig,
    SignalConfig,
    SurfaceConfig,
    VolTradingConfig,
)


class TestSignalConfig:
    def test_defaults_match_docs(self):
        s = SignalConfig()
        assert s.threshold_vol_pts == 1.0
        assert s.model_p == "har"
        assert s.z_threshold_arm == 1.5
        assert s.z_threshold_strong == 2.0
        assert s.z_threshold_extreme == 3.0

    @pytest.mark.parametrize("field,bad", [
        ("threshold_vol_pts", 0.0), ("threshold_vol_pts", 100.0),
        ("z_threshold_arm", 0.1), ("z_threshold_arm", 5.0),
        ("pca_rolling_months", 0), ("pca_rolling_months", 24),
        ("variance_explained_min", 0.5), ("variance_explained_min", 1.0),
    ])
    def test_out_of_bounds_rejected(self, field, bad):
        with pytest.raises(ValidationError):
            SignalConfig(**{field: bad})

    def test_z_thresholds_must_be_monotonic(self):
        with pytest.raises(ValidationError, match="must be > z_threshold_arm"):
            SignalConfig(z_threshold_arm=2.0, z_threshold_strong=1.8)
        with pytest.raises(ValidationError, match="must be > z_threshold_strong"):
            SignalConfig(z_threshold_strong=2.5, z_threshold_extreme=2.5)

    def test_model_p_enum(self):
        with pytest.raises(ValidationError):
            SignalConfig(model_p="xgboost")


class TestSurfaceConfig:
    def test_tenors_must_be_sorted_positive(self):
        with pytest.raises(ValidationError, match="sorted"):
            SurfaceConfig(tenors_days=[90, 30, 60])
        with pytest.raises(ValidationError, match="positive"):
            SurfaceConfig(tenors_days=[0, 30, 60])

    def test_delta_pillars_strictly_in_0_100(self):
        with pytest.raises(ValidationError):
            SurfaceConfig(delta_pillars=[0, 25, 50])
        with pytest.raises(ValidationError):
            SurfaceConfig(delta_pillars=[25, 50, 100])


class TestCalibrationConfig:
    def test_w1_clip_max_above_min(self):
        with pytest.raises(ValidationError, match="w1_clip_max must be >"):
            CalibrationConfig(w1_clip_min=0.5, w1_clip_max=0.5)

    def test_ewma_lambda_upper_bound(self):
        CalibrationConfig(ewma_lambda_fair_smile=0.999)
        with pytest.raises(ValidationError):
            CalibrationConfig(ewma_lambda_fair_smile=1.0)


class TestVolTradingConfig:
    def test_default_config_is_valid(self):
        cfg = VolTradingConfig()
        assert cfg.signal.threshold_vol_pts == 1.0
        assert cfg.sizing.base_size == 10
        assert cfg.delta_hedge.mode == "threshold"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError, match="extra"):
            VolTradingConfig(unknown_section={})

    def test_round_trip_json(self):
        original = VolTradingConfig()
        dumped = original.model_dump()
        restored = VolTradingConfig.model_validate(dumped)
        assert restored == original

    def test_partial_patch_via_deep_merge(self):
        original = VolTradingConfig()
        patched = original.model_copy(
            update={"signal": original.signal.model_copy(update={"threshold_vol_pts": 2.5})}
        )
        assert patched.signal.threshold_vol_pts == 2.5
        assert patched.signal.model_p == "har"  # unchanged
        assert patched.sizing.base_size == 10  # other section unchanged

    def test_json_schema_export_contains_all_sections(self):
        schema = VolTradingConfig.model_json_schema()
        assert "properties" in schema
        props = schema["properties"]
        for section in ("regime", "signal", "sizing", "exit_rules",
                        "surface", "calibration", "delta_hedge", "structures"):
            assert section in props, f"section {section} missing from JSON schema"
