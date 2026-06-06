"""Contract tests for core.config.vol_params : sections, defaults, bounds."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import VolTradingConfig

SECTIONS = (
    "regime", "signal", "sizing", "exit_rules",
    "surface", "calibration", "delta_hedge", "structures",
)


def test_defaults_construct_with_all_sections() -> None:
    cfg = VolTradingConfig()
    for name in SECTIONS:
        assert hasattr(cfg, name)
    # spot-check a few representative defaults
    assert cfg.signal.z_threshold_arm == 1.5
    assert cfg.sizing.base_size == 10
    assert cfg.surface.tenors_days == [30, 60, 90, 120, 150, 180]
    assert cfg.regime.gmm_components == 3


def test_json_round_trip_is_stable() -> None:
    cfg = VolTradingConfig()
    assert VolTradingConfig.model_validate(cfg.model_dump()) == cfg


@pytest.mark.parametrize(
    ("section", "field", "bad"),
    [
        ("signal", "z_threshold_arm", 0.1),       # < ge=0.5
        ("sizing", "base_size", 0),               # < ge=1
        ("surface", "svi_rmse_max_warn", 1.0),    # > le=0.01
        ("calibration", "ewma_lambda_fair_smile", 0.1),  # < ge=0.5
    ],
)
def test_out_of_range_fields_rejected(section: str, field: str, bad: float) -> None:
    with pytest.raises(ValidationError):
        VolTradingConfig(**{section: {field: bad}})
