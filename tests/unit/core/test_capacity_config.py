"""The capacity dial (#371 / #372): presets fill the fields, custom keeps
them, bounds hold, and every preset is a valid config."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import CAPACITY_PRESETS, CapacityConfig, VolTradingConfig


def test_defaults_are_the_balanced_preset() -> None:
    cfg = VolTradingConfig()
    assert cfg.capacity.preset == "balanced"
    assert cfg.capacity.model_dump(exclude={"preset"}) == CAPACITY_PRESETS["balanced"]


@pytest.mark.parametrize("preset", sorted(CAPACITY_PRESETS))
def test_a_named_preset_wins_over_the_fields(preset: str) -> None:
    cap = CapacityConfig.model_validate({"preset": preset, "strikes_per_side": 40, "thread_cap": 8})
    assert cap.model_dump(exclude={"preset"}) == CAPACITY_PRESETS[preset]


def test_custom_keeps_the_fields_and_bounds_hold() -> None:
    cap = CapacityConfig.model_validate({"preset": "custom", "strikes_per_side": 9, "thread_cap": 3})
    assert (cap.strikes_per_side, cap.thread_cap, cap.analytics_interval_min) == (9, 3, 60)
    with pytest.raises(ValidationError):
        CapacityConfig.model_validate({"preset": "custom", "strikes_per_side": 2})
    with pytest.raises(ValidationError):
        CapacityConfig.model_validate({"preset": "custom", "max_concurrent_scans": 9})
    with pytest.raises(ValidationError):
        CapacityConfig.model_validate({"preset": "turbo"})


def test_eco_is_lighter_than_full_on_every_axis() -> None:
    eco, full = CAPACITY_PRESETS["eco"], CAPACITY_PRESETS["full"]
    assert eco["analytics_interval_min"] > full["analytics_interval_min"]
    assert eco["thread_cap"] <= full["thread_cap"]
    assert eco["strikes_per_side"] < full["strikes_per_side"]
    assert eco["max_concurrent_scans"] <= full["max_concurrent_scans"]
    assert eco["rollup_interval_s"] > full["rollup_interval_s"]


def test_capacity_round_trips_through_the_versioned_config_payload() -> None:
    payload = VolTradingConfig.model_validate({"capacity": {"preset": "eco"}}).model_dump()
    again = VolTradingConfig.model_validate(payload)
    assert again.capacity.preset == "eco" and again.capacity.strikes_per_side == 12
    # the JSON schema the Settings tab reads exposes the section
    schema = VolTradingConfig.model_json_schema()
    assert "capacity" in schema["properties"]
