"""Response models for read routes that returned bare dicts (#416, first slice).

Each model is the shape the router builds, checked against payloads recorded
from prod on 2026-11-10 (tests/unit/api/test_read_models.py). They allow extra
fields: a key the model does not list still reaches the client, so adding a
model can never drop data; it only makes the known fields visible to the
OpenAPI drift gate and the generated frontend types.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class RiskLimit(_Open):
    """One active risk limit (`config_scalar`, namespace `risk`)."""

    value: float
    unit: str | None = None
    description: str | None = None


class HaltState(_Open):
    """The kill switch."""

    halted: bool
    reason: str | None = None
    set_by: str | None = None
    set_at: str | None = None


class ExitRuleConfig(_Open):
    rule_name: str
    is_active: bool
    priority: int
    params: dict[str, Any] | None = None
    description: str | None = None


class DeltaHedgeSetting(_Open):
    config_name: str
    config_value: float | None = None
    unit: str | None = None
    description: str | None = None


class RegimeSnapshotRow(_Open):
    timestamp: datetime
    label: str | None = None
    method: str | None = None
    vol_level_pct: float | None = None
    vol_of_vol_pct: float | None = None
    term_slope_pct: float | None = None
    event_dampener: bool | None = None
    days_to_next_event: float | None = None
    next_event_type: str | None = None
    p_calm: float | None = None
    p_stressed: float | None = None
    p_pre_event: float | None = None


class PcaSignalRow(_Open):
    timestamp: datetime
    z_score: float
    label: str | None = None
    actionable: bool | None = None


class CycleProgress(_Open):
    """The vol engine's progress through its current cycle."""

    cycle_started_at: str | None = None
    stage: str | None = None
    task: str | None = None
    completed: list[str] = []
