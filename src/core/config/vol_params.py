"""Typed Pydantic schema for every runtime-tunable parameter of the
vol trading pipeline.

Authoritative source of truth for the admin UI and the DB
``vol_config`` table. Field-level constraints (``ge``/``le``/``Literal``)
are enforced at write time by Pydantic, so invalid combinations never
reach the DB or the services.

Design rules :
 - ONE Pydantic model per cockpit panel / refactor phase.
 - Defaults match the values from ``docs/VOL_TRADING_USER_GUIDE.md`` and
   ``docs/VOL_MODEL_REFACTOR_PLAN.md`` at the time of writing. The defaults
   are also used as a resilience fallback when the DB is unreachable.
 - Tenor grids expressed in days (30, 60, 90, ...) rather than month
   strings (``1M``, ``2M``, ...) so the engine can do arithmetic directly.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Panel 1 — Regime Detector
# ---------------------------------------------------------------------------

RegimeLabel = Literal["calm", "stressed", "pre_event"]


class RegimeConfig(BaseModel):
    """Features + thresholds for the GMM-based regime detector."""

    model_config = ConfigDict(extra="forbid")

    gmm_components: int = Field(default=3, ge=2, le=6)
    regime_labels: tuple[RegimeLabel, RegimeLabel, RegimeLabel] = (
        "calm",
        "stressed",
        "pre_event",
    )
    event_dampener_horizon_days: int = Field(default=5, ge=1, le=30)
    vol_of_vol_window_days: int = Field(default=20, ge=5, le=90)
    stressed_sizing_multiplier: float = Field(default=0.7, ge=0.1, le=1.0)


# ---------------------------------------------------------------------------
# Panel 2 / P3 — PCA signal
# ---------------------------------------------------------------------------


class SignalConfig(BaseModel):
    """z-score thresholds + model selection for the PCA signal panel."""

    model_config = ConfigDict(extra="forbid")

    # Scalar signal (Panel 2 arm-trade decisions).
    threshold_vol_pts: float = Field(default=1.0, ge=0.1, le=10.0)
    model_p: Literal["har", "garch", "ewma"] = "har"
    vrp_regime_override: float | None = Field(default=None, ge=-5.0, le=5.0)

    # z-score cutoffs mapped to UX badges (WAIT / ARM / STRONG / EXTREME).
    z_threshold_arm: float = Field(default=1.5, ge=0.5, le=3.0)
    z_threshold_strong: float = Field(default=2.0, ge=1.5, le=4.0)
    z_threshold_extreme: float = Field(default=3.0, ge=2.5, le=5.0)

    # Rolling window for the PCA z-score distribution.
    pca_rolling_months: int = Field(default=3, ge=1, le=12)
    variance_explained_min: float = Field(default=0.85, ge=0.6, le=0.99)

    @field_validator("z_threshold_strong")
    @classmethod
    def _strong_above_arm(cls, v: float, info) -> float:
        arm = info.data.get("z_threshold_arm")
        if arm is not None and v <= arm:
            raise ValueError("z_threshold_strong must be > z_threshold_arm")
        return v

    @field_validator("z_threshold_extreme")
    @classmethod
    def _extreme_above_strong(cls, v: float, info) -> float:
        strong = info.data.get("z_threshold_strong")
        if strong is not None and v <= strong:
            raise ValueError("z_threshold_extreme must be > z_threshold_strong")
        return v


# ---------------------------------------------------------------------------
# Panel 3 Section E — Sizing
# ---------------------------------------------------------------------------


class SizingConfig(BaseModel):
    """Position sizing formula : base × conviction × book_penalty × event."""

    model_config = ConfigDict(extra="forbid")

    base_size: int = Field(default=10, ge=1, le=1000)
    alpha_book: float = Field(default=0.2, ge=0.0, le=1.0)
    book_rejection_threshold: float = Field(default=0.8, ge=0.3, le=1.0)
    event_dampener_multiplier: float = Field(default=0.5, ge=0.1, le=1.0)
    max_loss_pct_capital: float = Field(default=0.02, ge=0.001, le=0.1)


# ---------------------------------------------------------------------------
# Panel 4 — Exit rules
# ---------------------------------------------------------------------------


class ExitRulesConfig(BaseModel):
    """Thresholds that trigger systematic exits on open structures."""

    model_config = ConfigDict(extra="forbid")

    z_flip_exit_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    time_remaining_min_ratio: float = Field(default=0.3, ge=0.1, le=0.5)
    stop_loss_vega_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    time_to_expiry_force_exit_days: int = Field(default=7, ge=1, le=30)


# ---------------------------------------------------------------------------
# Panel 5 / P2 — Surface fit
# ---------------------------------------------------------------------------


class SurfaceConfig(BaseModel):
    """Tenor grid, fit diagnostics, no-arb tolerances."""

    model_config = ConfigDict(extra="forbid")

    tenors_days: list[int] = Field(default_factory=lambda: [30, 60, 90, 120, 150, 180])
    delta_pillars: list[int] = Field(default_factory=lambda: [10, 25, 50, 75, 90])
    svi_rmse_max_warn: float = Field(default=0.003, ge=0.0001, le=0.01)
    butterfly_check_grid: int = Field(default=100, ge=20, le=500)
    ssvi_vs_svi_tolerance: float = Field(default=0.20, ge=0.05, le=0.5)

    @field_validator("tenors_days")
    @classmethod
    def _tenors_sorted_positive(cls, v: list[int]) -> list[int]:
        if not v or any(t <= 0 for t in v) or v != sorted(v):
            raise ValueError("tenors_days must be a sorted list of positive ints")
        return v

    @field_validator("delta_pillars")
    @classmethod
    def _pillars_in_range(cls, v: list[int]) -> list[int]:
        if not v or any(not 0 < p < 100 for p in v) or v != sorted(v):
            raise ValueError("delta_pillars must be sorted deltas in (0, 100)")
        return v


# ---------------------------------------------------------------------------
# P1 / P4 — Calibration (VRP, HAR, W1, fair smile)
# ---------------------------------------------------------------------------


class CalibrationConfig(BaseModel):
    """Walk-forward + backtest calibration hyperparameters."""

    model_config = ConfigDict(extra="forbid")

    w1_walk_forward_months: int = Field(default=12, ge=3, le=36)
    w1_clip_min: float = Field(default=0.0, ge=0.0, le=0.5)
    w1_clip_max: float = Field(default=1.0, ge=0.5, le=1.0)

    vrp_train_split: float = Field(default=0.7, ge=0.5, le=0.9)
    vrp_mae_improvement_threshold: float = Field(default=0.20, ge=0.0, le=1.0)

    har_components: tuple[int, int, int] = (1, 5, 22)
    ewma_lambda_fair_smile: float = Field(default=0.94, ge=0.5, le=0.999)

    @field_validator("w1_clip_max")
    @classmethod
    def _clip_max_above_min(cls, v: float, info) -> float:
        mn = info.data.get("w1_clip_min")
        if mn is not None and v <= mn:
            raise ValueError("w1_clip_max must be > w1_clip_min")
        return v


# ---------------------------------------------------------------------------
# P5 — Delta hedge
# ---------------------------------------------------------------------------


class DeltaHedgeConfig(BaseModel):
    """Delta hedging behavior : static / threshold / scheduled."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["static", "threshold", "scheduled"] = "threshold"
    threshold_delta: float = Field(default=0.05, ge=0.01, le=0.5)
    scheduled_interval_minutes: int = Field(default=60, ge=5, le=1440)


# ---------------------------------------------------------------------------
# P5.1 — Trade structure mapping (signal -> structure factory)
# ---------------------------------------------------------------------------

StructureKind = Literal[
    "straddle_atm",
    "calendar_spread",
    "risk_reversal_25d",
    "butterfly_25d",
]


class TradeStructuresConfig(BaseModel):
    """Which structure is generated for each signal origin."""

    model_config = ConfigDict(extra="forbid")

    pc1_structure: StructureKind = "straddle_atm"
    pc2_structure: StructureKind = "calendar_spread"
    pc3_skew_structure: StructureKind = "risk_reversal_25d"
    pc3_convex_structure: StructureKind = "butterfly_25d"
    default_tenor_days: int = Field(default=90, ge=7, le=365)


# ---------------------------------------------------------------------------
# Root config — the single object written to / read from the DB
# ---------------------------------------------------------------------------


class VolTradingConfig(BaseModel):
    """Root config persisted as JSONB in the ``vol_config`` table.

    Adding a new tunable parameter is a 2-step operation :
      1. add the field to the relevant section below (or create a new
         section if no existing one fits).
      2. reference ``get_current_config().section.field`` from the
         consuming service code.

    The admin UI picks up the new field automatically because RJSF
    generates the form from this schema's JSON Schema export.
    """

    model_config = ConfigDict(extra="forbid")

    regime: RegimeConfig = Field(default_factory=RegimeConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    sizing: SizingConfig = Field(default_factory=SizingConfig)
    exit_rules: ExitRulesConfig = Field(default_factory=ExitRulesConfig)
    surface: SurfaceConfig = Field(default_factory=SurfaceConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    delta_hedge: DeltaHedgeConfig = Field(default_factory=DeltaHedgeConfig)
    structures: TradeStructuresConfig = Field(default_factory=TradeStructuresConfig)
