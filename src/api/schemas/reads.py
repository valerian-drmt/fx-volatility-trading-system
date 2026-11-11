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
    # Extra keys pass through; a key that is not a Python name (`$defs`, `from`)
    # is declared under an alias and serialised back under it. Routes using these
    # models set response_model_exclude_unset, so a field the payload did not
    # carry stays absent instead of coming back as null.
    model_config = ConfigDict(extra="allow", serialize_by_alias=True, validate_by_name=True, validate_by_alias=True)


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


# ---- slice 2 (#416) ---------------------------------------------------------------------------


class OpenPositionRow(_Open):
    """One `/positions/open` row (the IB mirror), as `persistence.rows.open_position_row` builds it."""

    id: int
    package_id: int | None = None
    trade_id: int | None = None
    contract_id: int | None = None
    product_label: str | None = None
    structure: str | None = None
    side: str | None = None
    quantity: float | None = None
    tenor: str | None = None
    tenor_now: str | None = None
    expiry: str | None = None
    current_pnl_usd: float | None = None
    market_price: float | None = None
    contract_price_entry: float | None = None
    nominal_eur: float | None = None
    delta_usd: float | None = None
    gamma_usd: float | None = None
    vega_usd: float | None = None
    theta_usd: float | None = None
    vanna_usd: float | None = None
    volga_usd: float | None = None
    iv: float | None = None
    timestamp: str | None = None
    entry_timestamp: str | None = None


class FitWindow(_Open):
    start: datetime | None = None
    end: datetime | None = None


class PcaModelState(_Open):
    """The active PCA model for a symbol and its refit readiness."""

    active: bool
    version: str | None = None
    n_obs_used: int | None = None
    fit_window: FitWindow | None = None
    variance_explained: list[float] | None = None
    available_hourly_snapshots: int | None = None
    min_obs_for_refit: int | None = None
    ready_to_refit: bool | None = None


class BookState(_Open):
    """The book as the pre-trade gate sees it."""

    symbol: str
    total_vega_usd: float | None = None
    total_gamma_usd: float | None = None
    total_theta_usd: float | None = None
    total_delta: float | None = None
    n_open_structures: int | None = None
    n_open_legs: int | None = None
    notional_engaged_usd: float | None = None
    capital_total_usd: float | None = None
    margin_used_usd: float | None = None
    is_current: bool | None = None
    is_bootstrap: bool | None = None


class MacroEvent(_Open):
    id: int
    event_type: str
    impact: str | None = None
    region: str | None = None
    scheduled_at: datetime
    description: str | None = None
    source: str | None = None


class EngineHeartbeat(_Open):
    name: str
    status: str
    hb_age_s: float | None = None
    hb_ttl_s: int | None = None
    stale_threshold_s: int | None = None
    out_key: str | None = None
    out_age_s: float | None = None


class EnginesStatus(_Open):
    """Per-engine heartbeat and output freshness, plus the IB gateway's state."""

    engines: list[EngineHeartbeat]
    ib_gateway: dict[str, Any] | None = None
    timestamp: str | None = None
