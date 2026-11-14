"""Response models derived from payloads recorded from prod on 2026-11-11 (#416,
slice 3): the portfolio, positions, regime, trade, health, admin and dev reads.

Generated from tests/fixtures/api_reads/*.json by inferring each field's
type over every recorded sample, then reviewed. Every field is optional and
extra keys pass through (`_Open`), so a model documents the shape for the
OpenAPI drift gate and the generated frontend types without ever dropping a
field or turning a 200 into a 500; tests/unit/api/test_read_models.py checks
that each recorded payload round-trips unchanged.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from api.schemas.reads import Lenient, _Open


class Health(_Open):
    commit: Lenient[str | None] = None
    git_sha: Lenient[str | None] = None
    status: Lenient[str | None] = None
    version: Lenient[str | None] = None


class HealthReady(_Open):
    commit: Lenient[str | None] = None
    components: Lenient[HealthReadyComponents | None] = None
    git_sha: Lenient[str | None] = None
    status: Lenient[str | None] = None
    version: Lenient[str | None] = None


class HealthReadyComponents(_Open):
    database: Lenient[str | None] = None
    redis: Lenient[str | None] = None


class HealthExtended(_Open):
    commit: Lenient[str | None] = None
    components: Lenient[HealthExtendedComponents | None] = None
    git_sha: Lenient[str | None] = None
    status: Lenient[str | None] = None
    timestamp: Lenient[str | None] = None
    version: Lenient[str | None] = None


class HealthExtendedComponents(_Open):
    backup: Lenient[str | None] = None
    database: Lenient[str | None] = None
    engines: Lenient[HealthExtendedComponentsEngines | None] = None
    redis: Lenient[str | None] = None


class HealthExtendedComponentsEngines(_Open):
    analytics_engine: Lenient[str | None] = None
    db_writer: Lenient[str | None] = None
    execution: Lenient[str | None] = None
    market_data: Lenient[str | None] = None
    risk_engine: Lenient[str | None] = None
    vol_engine: Lenient[str | None] = None


class PositionsBookRow(_Open):
    available: Lenient[float | None] = None
    avg_price: Lenient[float | None] = None
    contract_expiry: Lenient[str | None] = None
    contract_strike: Lenient[float | None] = None
    contract_type: Lenient[str | None] = None
    ib_local_symbol: Lenient[str | None] = None
    leg_idx: Lenient[int | float | None] = None
    open_qty: Lenient[float | None] = None
    order_id: Lenient[int | float | None] = None
    order_role: Lenient[str | None] = None
    rebuilt_at: Lenient[str | None] = None
    reserved_qty: Lenient[float | None] = None
    side: Lenient[str | None] = None
    structure_id: Lenient[int | float | None] = None
    structure_type: Lenient[str | None] = None


class PositionsBreaksRow(_Open):
    book_qty: Lenient[float | None] = None
    break_type: Lenient[str | None] = None
    broker_qty: Lenient[float | None] = None
    contract: Lenient[str | None] = None
    detected_at: Lenient[str | None] = None
    diff: Lenient[float | None] = None
    id: Lenient[int | float | None] = None
    last_seen_at: Lenient[str | None] = None
    resolved_at: Any = None


class PositionsStructured(_Open):
    structures: Lenient[list[PositionsStructuredStructuresItem] | None] = None
    unlinked: Lenient[list[Any] | None] = None


class PositionsStructuredStructuresItem(_Open):
    base_qty: Lenient[int | float | None] = None
    created_at: Lenient[str | None] = None
    legs: Lenient[list[dict[str, Any]] | None] = None
    net: Lenient[dict[str, Any] | None] = None
    product_label: Any = None
    state: Lenient[str | None] = None
    structure_id: Lenient[int | float | None] = None
    structure_type: Lenient[str | None] = None
    tenor: Lenient[str | None] = None


class PositionsReconciliation(_Open):
    as_of: Lenient[str | None] = None
    breaks: Lenient[list[PositionsReconciliationBreaksItem] | None] = None
    n_breaks: Lenient[int | float | None] = None
    n_contracts: Lenient[int | float | None] = None


class PositionsReconciliationBreaksItem(_Open):
    actual_net: Lenient[float | None] = None
    f_break: Lenient[float | None] = Field(default=None, alias="break")
    contract: Lenient[str | None] = None
    expected_net: Lenient[float | None] = None
    kind: Lenient[str | None] = None
    structure_id: Lenient[int | float | None] = None


class PositionsLedger(_Open):
    as_of: Lenient[str | None] = None
    positions: Lenient[list[PositionsLedgerPositionsItem] | None] = None
    totals: Lenient[PositionsLedgerTotals | None] = None


class PositionsLedgerPositionsItem(_Open):
    avg_cost: Lenient[float | None] = None
    commission: Lenient[float | None] = None
    contract: Lenient[str | None] = None
    multiplier: Lenient[float | None] = None
    net_qty: Lenient[float | None] = None
    realized_pnl: Lenient[float | None] = None
    unrealized_pnl: Lenient[float | None] = None


class PositionsLedgerTotals(_Open):
    commission: Lenient[float | None] = None
    realized_pnl: Lenient[float | None] = None
    unrealized_pnl: Lenient[float | None] = None


class PositionsActiveRow(_Open):
    armed_signal_label: Any = None
    armed_z_score: Any = None
    contract_price_entry: Lenient[float | None] = None
    contract_price_market: Any = None
    current_delta_unhedged: Lenient[float | None] = None
    current_gamma_usd_per_pip2: Lenient[float | None] = None
    current_pnl_gross_usd: Lenient[float | None] = None
    current_pnl_net_usd: Lenient[float | None] = None
    current_theta_usd_per_day: Lenient[float | None] = None
    current_vega_usd_per_volpt: Lenient[float | None] = None
    entry_gamma_usd_per_pip2: Any = None
    entry_iv_avg: Any = None
    entry_premium_usd: Lenient[float | None] = None
    entry_spot: Any = None
    entry_theta_usd_per_day: Any = None
    entry_total_cost_usd: Lenient[float | None] = None
    entry_vega_usd_per_volpt: Any = None
    expiry_date: Lenient[str | None] = None
    gamma_pnl_usd: Lenient[float | None] = None
    ib_qty_diff: Lenient[int | float | None] = None
    ib_qty_total: Lenient[int | float | None] = None
    ib_reconciled_at: Lenient[str | None] = None
    ib_sync_status: Lenient[str | None] = None
    id: Lenient[int | float | None] = None
    last_mtm_at: Lenient[str | None] = None
    nominal_eur: Lenient[float | None] = None
    opened_at: Lenient[str | None] = None
    product_label: Lenient[str | None] = None
    reference_tenor: Lenient[str | None] = None
    source: Lenient[str | None] = None
    state: Lenient[str | None] = None
    structure: Lenient[str | None] = None
    structure_id: Lenient[int | float | None] = None
    structure_type: Lenient[str | None] = None
    tenor: Lenient[str | None] = None
    theta_pnl_usd: Lenient[float | None] = None
    triggering_pc: Any = None
    vega_pnl_usd: Lenient[float | None] = None
    instrument_type: Lenient[str | None] = None
    iv: Lenient[float | None] = None
    option_type: Lenient[str | None] = None
    quantity: Lenient[float | None] = None
    side: Lenient[str | None] = None
    strike: Lenient[float | None] = None
    symbol: Lenient[str | None] = None
    vanna_usd: Lenient[float | None] = None
    volga_usd: Lenient[float | None] = None


class PositionsAggregate(_Open):
    n_delta_unknown: Lenient[int | float | None] = None
    n_open_positions: Lenient[int | float | None] = None
    total_delta_unhedged: Lenient[float | None] = None
    total_gamma_usd_per_pip2: Lenient[float | None] = None
    total_theta_usd_per_day: Lenient[float | None] = None
    total_vega_usd_per_volpt: Lenient[float | None] = None


class PortfolioAccount(_Open):
    available_funds_usd: Lenient[float | None] = None
    buying_power_usd: Lenient[float | None] = None
    freshness: Lenient[str | None] = None
    latest: Lenient[PortfolioAccountLatest | None] = None
    prev_24h: Lenient[PortfolioAccountPrev24h | None] = None


class PortfolioAccountLatest(_Open):
    cash_usd: Lenient[float | None] = None
    currencies: Lenient[dict[str, dict[str, Any]] | None] = None
    cushion: Lenient[float | None] = None
    excess_liquidity: Lenient[float | None] = None
    gross_position_value: Lenient[float | None] = None
    init_margin_req: Lenient[float | None] = None
    maint_margin_req: Lenient[float | None] = None
    net_liq_usd: Lenient[float | None] = None
    open_positions_count: Lenient[int | float | None] = None
    timestamp: Lenient[str | None] = None
    unrealized_pnl_usd: Lenient[float | None] = None


class PortfolioAccountPrev24h(_Open):
    cash_usd: Lenient[float | None] = None
    currencies: Lenient[dict[str, dict[str, Any]] | None] = None
    cushion: Lenient[float | None] = None
    excess_liquidity: Lenient[float | None] = None
    gross_position_value: Lenient[float | None] = None
    init_margin_req: Lenient[float | None] = None
    maint_margin_req: Lenient[float | None] = None
    net_liq_usd: Lenient[float | None] = None
    open_positions_count: Lenient[int | float | None] = None
    timestamp: Lenient[str | None] = None
    unrealized_pnl_usd: Lenient[float | None] = None


class PortfolioHeader(_Open):
    account: Lenient[PortfolioHeaderAccount | None] = None
    computed_at: Lenient[str | None] = None
    greeks: Lenient[PortfolioHeaderGreeks | None] = None
    pnl: Lenient[PortfolioHeaderPnl | None] = None
    rollup_computed_at: Lenient[str | None] = None
    var_1d_99: Lenient[PortfolioHeaderVar1d99 | None] = None


class PortfolioHeaderAccount(_Open):
    as_of: Lenient[str | None] = None
    cash_usd: Lenient[float | None] = None
    cushion: Lenient[float | None] = None
    excess_liquidity: Lenient[float | None] = None
    init_margin_req: Lenient[float | None] = None
    n_open_positions: Lenient[int | float | None] = None
    net_liq_usd: Lenient[float | None] = None
    util_pct: Lenient[float | None] = None


class PortfolioHeaderGreeks(_Open):
    delta_usd: Lenient[float | None] = None
    gamma_usd: Lenient[float | None] = None
    theta_usd: Lenient[float | None] = None
    vega_usd: Lenient[float | None] = None


class PortfolioHeaderPnl(_Open):
    open_unrealized_usd: Lenient[float | None] = None
    prev_close_day: Lenient[str | None] = None
    total_24h_usd: Lenient[float | None] = None


class PortfolioHeaderVar1d99(_Open):
    method: Lenient[str | None] = None
    n_days: Lenient[int | float | None] = None
    usd: Lenient[float | None] = None


class PortfolioEquityCurveRow(_Open):
    is_eod: Lenient[bool | None] = None
    net_liq_usd: Lenient[float | None] = None
    timestamp: Lenient[str | None] = None


class PortfolioValuationHistoryRow(_Open):
    contracts_usd: Lenient[float | None] = None
    eur_cash_usd: Lenient[float | None] = None
    net_liq_usd: Lenient[float | None] = None
    timestamp: Lenient[str | None] = None
    usd_cash_usd: Lenient[float | None] = None


class PortfolioTradeMarkersRow(_Open):
    closed_at: Lenient[str | None] = None
    # null in every recorded row (no closed trade, no entry spot); numbers when set
    entry_spot: Lenient[float | None] = None
    id: Lenient[int | float | None] = None
    net_pnl_usd: Lenient[float | None] = None
    opened_at: Lenient[str | None] = None
    state: Lenient[str | None] = None
    type: Lenient[str | None] = None


class PortfolioGreeksHistoryRow(_Open):
    delta_usd: Lenient[float | None] = None
    gamma_usd: Lenient[float | None] = None
    theta_usd: Lenient[float | None] = None
    timestamp: Lenient[str | None] = None
    vega_usd: Lenient[float | None] = None


class PortfolioGreekPnlHistoryRow(_Open):
    delta_pnl_usd: Lenient[float | None] = None
    gamma_pnl_usd: Lenient[float | None] = None
    theta_pnl_usd: Lenient[float | None] = None
    timestamp: Lenient[str | None] = None
    vega_pnl_usd: Lenient[float | None] = None


class PortfolioAggregateGreeks(_Open):
    computed_at: Lenient[str | None] = None
    n_open_positions: Lenient[int | float | None] = None
    n_with_snapshot: Lenient[int | float | None] = None
    total_delta_usd: Lenient[float | None] = None
    total_gamma_usd: Lenient[float | None] = None
    total_theta_usd: Lenient[float | None] = None
    total_vega_usd: Lenient[float | None] = None


class PortfolioVegaPerTenorRow(_Open):
    bucket: Lenient[str | None] = None
    dte_hi: Lenient[int | float | None] = None
    dte_lo: Lenient[int | float | None] = None
    n_positions: Lenient[int | float | None] = None
    vega_usd: Lenient[float | None] = None


class PortfolioRiskPerTenorRow(_Open):
    bucket: Lenient[str | None] = None
    dte_hi: Lenient[int | float | None] = None
    dte_lo: Lenient[int | float | None] = None
    n_positions: Lenient[int | float | None] = None
    vanna_usd: Lenient[float | None] = None
    vega_usd: Lenient[float | None] = None
    volga_usd: Lenient[float | None] = None


class PortfolioHedgeSummary(_Open):
    computed_at: Lenient[str | None] = None
    mtd: Lenient[PortfolioHedgeSummaryMtd | None] = None
    rolling_30d: Lenient[PortfolioHedgeSummaryRolling30d | None] = None
    rolling_7d: Lenient[PortfolioHedgeSummaryRolling7d | None] = None
    today: Lenient[PortfolioHedgeSummaryToday | None] = None
    wtd: Lenient[PortfolioHedgeSummaryWtd | None] = None
    ytd: Lenient[PortfolioHedgeSummaryYtd | None] = None


class PortfolioHedgeSummaryMtd(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioHedgeSummaryRolling30d(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioHedgeSummaryRolling7d(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioHedgeSummaryToday(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioHedgeSummaryWtd(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioHedgeSummaryYtd(_Open):
    cum_cost_usd: Lenient[float | None] = None
    n_hedges: Lenient[int | float | None] = None


class PortfolioStressGrid(_Open):
    axis: Lenient[str | None] = None
    current_spot: Lenient[float | None] = None
    dropped: Lenient[list[Any] | None] = None
    fallback: Lenient[PortfolioStressGridFallback | None] = None
    grid: Lenient[list[list[float]] | None] = None
    n_positions: Lenient[int | float | None] = None
    output: Lenient[str | None] = None
    row_bins: Lenient[list[int | float] | None] = None
    row_unit: Lenient[str | None] = None
    spot_bins_bps: Lenient[list[int | float] | None] = None
    spot_source: Lenient[str | None] = None
    vol_bins_vps: Lenient[list[int | float] | None] = None


class PortfolioStressGridFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioGreeksLadder(_Open):
    axis: Lenient[str | None] = None
    bins: Lenient[list[int | float] | None] = None
    current_spot: Lenient[float | None] = None
    dropped: Lenient[list[Any] | None] = None
    fallback: Lenient[PortfolioGreeksLadderFallback | None] = None
    n_positions: Lenient[int | float | None] = None
    rows: Lenient[list[PortfolioGreeksLadderRowsItem] | None] = None
    spot_bins_bps: Lenient[list[int | float] | None] = None
    spot_source: Lenient[str | None] = None
    unit: Lenient[str | None] = None


class PortfolioGreeksLadderFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioGreeksLadderRowsItem(_Open):
    axis_value: Lenient[int | float | None] = None
    delta_usd: Lenient[float | None] = None
    dspot_bps: Lenient[int | float | None] = None
    gamma_usd_per_pip: Lenient[float | None] = None
    hedge_delta_usd: Lenient[float | None] = None
    pnl_usd: Lenient[float | None] = None
    spot: Lenient[float | None] = None
    vega_usd_per_volpt: Lenient[float | None] = None


class PortfolioVegaPca(_Open):
    current_spot: Lenient[float | None] = None
    dropped: Lenient[list[Any] | None] = None
    fallback: Lenient[PortfolioVegaPcaFallback | None] = None
    f_model_version: Lenient[str | None] = Field(default=None, alias="model_version")
    n_positions: Lenient[int | float | None] = None
    pcs: Lenient[list[PortfolioVegaPcaPcsItem] | None] = None
    spot_source: Lenient[str | None] = None


class PortfolioVegaPcaFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioVegaPcaPcsItem(_Open):
    name: Lenient[str | None] = None
    pc: Lenient[int | float | None] = None
    variance_pct: Lenient[float | None] = None
    vega_usd: Lenient[float | None] = None


class PortfolioMarginalVar(_Open):
    convention: Lenient[str | None] = None
    dropped: Lenient[list[Any] | None] = None
    fallback: Lenient[PortfolioMarginalVarFallback | None] = None
    horizon_days: Lenient[int | float | None] = None
    method: Lenient[str | None] = None
    n_days: Lenient[int | float | None] = None
    positions: Lenient[list[PortfolioMarginalVarPositionsItem] | None] = None
    total: Lenient[PortfolioMarginalVarTotal | None] = None


class PortfolioMarginalVarFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioMarginalVarPositionsItem(_Open):
    component_usd: Lenient[float | None] = None
    factor: Lenient[str | None] = None
    id: Lenient[str | None] = None
    label: Lenient[str | None] = None
    pct: Lenient[float | None] = None
    standalone_usd: Lenient[float | None] = None
    trade: Lenient[str | None] = None


class PortfolioMarginalVarTotal(_Open):
    diversification_pct: Lenient[float | None] = None
    portfolio_var_usd: Lenient[float | None] = None


class PortfolioVarFactors(_Open):
    current_spot: Lenient[float | None] = None
    dropped: Lenient[list[Any] | None] = None
    factors: Lenient[list[PortfolioVarFactorsFactorsItem] | None] = None
    fallback: Lenient[PortfolioVarFactorsFallback | None] = None
    n_positions: Lenient[int | float | None] = None
    spot_source: Lenient[str | None] = None


class PortfolioVarFactorsFactorsItem(_Open):
    key: Lenient[str | None] = None
    label: Lenient[str | None] = None
    var_usd: Lenient[float | None] = None


class PortfolioVarFactorsFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioPnlAttribution(_Open):
    computed_at: Lenient[str | None] = None
    forward_source: Lenient[str | None] = None
    lookback_hours: Lenient[int | float | None] = None
    per_position: Lenient[list[PortfolioPnlAttributionPerPositionItem] | None] = None
    rollup: Lenient[str | None] = None
    source: Lenient[str | None] = None
    totals: Lenient[PortfolioPnlAttributionTotals | None] = None
    window_start: Lenient[str | None] = None


class PortfolioPnlAttributionPerPositionItem(_Open):
    actual_pnl_usd: Lenient[float | None] = None
    attrib_tenor: Lenient[str | None] = None
    contract_id: Lenient[int | float | None] = None
    delta_pnl_usd: Lenient[float | None] = None
    gamma_pnl_usd: Lenient[float | None] = None
    id: Lenient[int | float | None] = None
    iv: Lenient[float | None] = None
    nominal_eur: Lenient[float | None] = None
    product_label: Lenient[str | None] = None
    residual_usd: Lenient[float | None] = None
    side: Lenient[str | None] = None
    source: Lenient[str | None] = None
    structure: Lenient[str | None] = None
    structure_type: Lenient[str | None] = None
    tenor: Lenient[str | None] = None
    theta_pnl_usd: Lenient[float | None] = None
    trade_id: Lenient[int | float | None] = None
    vega_pnl_usd: Lenient[float | None] = None
    wing: Lenient[str | None] = None


class PortfolioPnlAttributionTotals(_Open):
    actual_pnl_usd: Lenient[float | None] = None
    delta_pnl_usd: Lenient[float | None] = None
    gamma_pnl_usd: Lenient[float | None] = None
    residual_usd: Lenient[float | None] = None
    theta_pnl_usd: Lenient[float | None] = None
    vega_pnl_usd: Lenient[float | None] = None


class PortfolioPinRisk(_Open):
    breach_bps: Lenient[int | float | None] = None
    current_spot: Lenient[float | None] = None
    n_options: Lenient[int | float | None] = None
    rows: Lenient[list[PortfolioPinRiskRowsItem] | None] = None


class PortfolioPinRiskRowsItem(_Open):
    delta_usd: Lenient[float | None] = None
    distance_pips: Lenient[float | None] = None
    dte_days: Lenient[int | float | None] = None
    expiry: Lenient[str | None] = None
    id: Lenient[int | float | None] = None
    option_type: Lenient[str | None] = None
    pnl_at_breach_dn_usd: Lenient[float | None] = None
    pnl_at_breach_up_usd: Lenient[float | None] = None
    pnl_at_pin_usd: Lenient[float | None] = None
    pnl_now_usd: Lenient[float | None] = None
    product_label: Lenient[str | None] = None
    qty: Lenient[float | None] = None
    side: Lenient[str | None] = None
    strike: Lenient[float | None] = None
    structure: Lenient[str | None] = None
    trade_id: Lenient[int | float | None] = None


class PortfolioScenarios(_Open):
    by_iv: Lenient[list[Any] | None] = None
    by_spot: Lenient[list[Any] | None] = None
    current_iv_avg_pct: Any = None
    current_spot: Any = None
    iv_steps_volpt: Lenient[list[float] | None] = None
    n_positions: Lenient[int | float | None] = None
    spot_steps_pct: Lenient[list[float] | None] = None


class PortfolioCash(_Open):
    currencies: Lenient[list[PortfolioCashCurrenciesItem] | None] = None
    eurusd_spot: Lenient[float | None] = None
    freshness: Lenient[str | None] = None
    timestamp: Lenient[str | None] = None
    total_usd: Lenient[float | None] = None


class PortfolioCashCurrenciesItem(_Open):
    ccy: Lenient[str | None] = None
    rate: Lenient[float | None] = None
    settled: Lenient[float | None] = None
    unsettled: Any = None
    usd_value: Lenient[float | None] = None


class PortfolioDailyPnl(_Open):
    computed_at: Lenient[str | None] = None
    days: Lenient[int | float | None] = None
    series: Lenient[list[PortfolioDailyPnlSeriesItem] | None] = None
    source: Lenient[str | None] = None
    total_mtm_usd: Lenient[float | None] = None


class PortfolioDailyPnlSeriesItem(_Open):
    cumulative_usd: Lenient[float | None] = None
    day: Lenient[str | None] = None
    mtm_usd: Lenient[float | None] = None
    n_closed: Lenient[int | float | None] = None
    net_liq_close: Lenient[float | None] = None
    realized_usd: Lenient[float | None] = None


class PortfolioPnlAttributionPivot(_Open):
    by: Lenient[str | None] = None
    groups: Lenient[list[PortfolioPnlAttributionPivotGroupsItem] | None] = None
    total_nominal_eur: Lenient[float | None] = None
    total_usd: Lenient[float | None] = None


class PortfolioPnlAttributionPivotGroupsItem(_Open):
    label: Lenient[str | None] = None
    n: Lenient[int | float | None] = None
    nominal_eur: Lenient[float | None] = None
    pnl_usd: Lenient[float | None] = None
    vanna_usd: Lenient[float | None] = None
    volga_usd: Lenient[float | None] = None


class PortfolioVar(_Open):
    computed_at: Lenient[str | None] = None
    convention: Lenient[str | None] = None
    current_spot: Lenient[float | None] = None
    dropped: Lenient[list[Any] | None] = None
    es_99_usd: Lenient[float | None] = None
    fallback: Lenient[PortfolioVarFallback | None] = None
    hist: Lenient[list[PortfolioVarHistItem] | None] = None
    horizon_days: Lenient[int | float | None] = None
    mean_daily_usd: Lenient[float | None] = None
    method: Lenient[str | None] = None
    n_days: Lenient[int | float | None] = None
    n_positions: Lenient[int | float | None] = None
    n_scenarios: Lenient[int | float | None] = None
    spot_source: Lenient[str | None] = None
    var_95_usd: Lenient[float | None] = None
    var_99_usd: Lenient[float | None] = None


class PortfolioVarFallback(_Open):
    spot: Lenient[str | None] = None


class PortfolioVarHistItem(_Open):
    count: Lenient[int | float | None] = None
    hi: Lenient[float | None] = None
    lo: Lenient[float | None] = None


class PortfolioGreekLimits(_Open):
    alpha: Lenient[float | None] = None
    computed_at: Lenient[str | None] = None
    cross_budget_usd: Lenient[float | None] = None
    delta_cap_usd: Lenient[float | None] = None
    gamma_cap_pip: Lenient[float | None] = None
    loss_budget_usd: Lenient[float | None] = None
    nav_base_usd: Lenient[float | None] = None
    nav_live_usd: Lenient[float | None] = None
    regime_mult: Lenient[float | None] = None
    spot: Lenient[float | None] = None
    vega_cap_usd: Lenient[float | None] = None


class PortfolioStats(_Open):
    computed_at: Lenient[str | None] = None
    cum_realized_usd: Lenient[float | None] = None
    cum_unrealized_usd: Lenient[float | None] = None
    current_drawdown_pct: Lenient[float | None] = None
    hit_rate: Any = None
    max_drawdown_pct: Lenient[float | None] = None
    n_closed: Lenient[int | float | None] = None
    n_days: Lenient[int | float | None] = None
    n_open: Lenient[int | float | None] = None
    n_reconciled_flat: Lenient[int | float | None] = None
    net_liq_change_usd: Lenient[float | None] = None
    sharpe: Lenient[float | None] = None


class AdminSettingsDomain(_Open):
    domain: Lenient[str | None] = None
    params: Lenient[list[AdminSettingsDomainParamsItem] | None] = None
    title: Lenient[str | None] = None


class AdminSettingsDomainParamsItem(_Open):
    default: Any = None
    description: Lenient[str | None] = None
    is_default: Lenient[bool | None] = None
    name: Lenient[str | None] = None
    namespace: Lenient[str | None] = None
    unit: Lenient[str | None] = None
    updated_by: Any = None
    value: Any = None


class DevStack(_Open):
    containers: Lenient[list[DevStackContainersItem] | None] = None
    edges: Lenient[list[DevStackEdgesItem] | None] = None
    timestamp: Lenient[str | None] = None


class DevStackContainersItem(_Open):
    desc: Lenient[str | None] = None
    image: Lenient[str | None] = None
    layer: Lenient[str | None] = None
    name: Lenient[str | None] = None
    status: Lenient[str | None] = None


class DevStackEdgesItem(_Open):
    f_from: Lenient[str | None] = Field(default=None, alias="from")
    to: Lenient[str | None] = None


class DevMigrations(_Open):
    chain: Lenient[list[DevMigrationsChainItem] | None] = None
    current: Lenient[str | None] = None
    head: Lenient[str | None] = None
    in_sync: Lenient[bool | None] = None
    n_applied: Lenient[int | float | None] = None
    n_pending: Lenient[int | float | None] = None
    n_total: Lenient[int | float | None] = None


class DevMigrationsChainItem(_Open):
    created: Lenient[str | None] = None
    down_revision: Lenient[str | None] = None
    filename: Lenient[str | None] = None
    id: Lenient[str | None] = None
    parent_title: Lenient[str | None] = None
    status: Lenient[str | None] = None
    title: Lenient[str | None] = None


class DevDbSchema(_Open):
    n_relationships: Lenient[int | float | None] = None
    n_tables: Lenient[int | float | None] = None
    relationships: Lenient[list[DevDbSchemaRelationshipsItem] | None] = None
    source: Lenient[str | None] = None
    tables: Lenient[list[DevDbSchemaTablesItem] | None] = None


class DevDbSchemaRelationshipsItem(_Open):
    cardinality: Lenient[str | None] = None
    composite: Lenient[bool | None] = None
    from_columns: Lenient[list[str] | None] = None
    from_table: Lenient[str | None] = None
    label: Lenient[str | None] = None
    on_delete: Lenient[str | None] = None
    on_update: Lenient[str | None] = None
    optional: Lenient[bool | None] = None
    self_loop: Lenient[bool | None] = None
    to_columns: Lenient[list[str] | None] = None
    to_table: Lenient[str | None] = None


class DevDbSchemaTablesItem(_Open):
    check_constraints: Lenient[list[dict[str, Any]] | None] = None
    columns: Lenient[list[dict[str, Any]] | None] = None
    comment: Lenient[str | None] = None
    composite_unique: Lenient[list[dict[str, Any]] | None] = None
    n_columns: Lenient[int | float | None] = None
    name: Lenient[str | None] = None


class DevHardware(_Open):
    cpu: Lenient[DevHardwareCpu | None] = None
    disk: Lenient[DevHardwareDisk | None] = None
    gpu: Lenient[list[Any] | None] = None
    memory: Lenient[DevHardwareMemory | None] = None
    timestamp: Lenient[str | None] = None


class DevHardwareCpu(_Open):
    cores: Lenient[int | float | None] = None
    load_avg: Lenient[list[float] | None] = None
    per_core: Lenient[list[float] | None] = None
    percent: Lenient[float | None] = None


class DevHardwareDisk(_Open):
    percent: Lenient[float | None] = None
    total_gb: Lenient[float | None] = None
    used_gb: Lenient[float | None] = None


class DevHardwareMemory(_Open):
    percent: Lenient[float | None] = None
    total_gb: Lenient[float | None] = None
    used_gb: Lenient[float | None] = None


class DevContainersMetrics(_Open):
    cpu: Lenient[list[DevContainersMetricsCpuItem] | None] = None
    end: Lenient[str | None] = None
    mem: Lenient[list[DevContainersMetricsMemItem] | None] = None
    reachable: Lenient[bool | None] = None
    source: Lenient[str | None] = None
    start: Lenient[str | None] = None
    step: Lenient[int | float | None] = None


class DevContainersMetricsCpuItem(_Open):
    name: Lenient[str | None] = None
    points: Lenient[list[list[float]] | None] = None


class DevContainersMetricsMemItem(_Open):
    name: Lenient[str | None] = None
    points: Lenient[list[list[float]] | None] = None


class RegimeFeatures(_Open):
    features: Lenient[list[RegimeFeaturesFeaturesItem] | None] = None
    symbol: Lenient[str | None] = None
    synthesis: Lenient[RegimeFeaturesSynthesis | None] = None
    timestamp: Lenient[str | None] = None


class RegimeFeaturesFeaturesItem(_Open):
    bucket: Lenient[str | None] = None
    delta_z_1h: Lenient[float | None] = None
    expected_z: Lenient[dict[str, Any] | None] = None
    name: Lenient[str | None] = None
    pct: Lenient[int | float | None] = None
    signal: Lenient[str | None] = None
    value: Lenient[float | None] = None
    vs_expected: Lenient[str | None] = None
    z: Lenient[float | None] = None


class RegimeFeaturesSynthesis(_Open):
    action: Lenient[str | None] = None
    dominant: Lenient[str | None] = None
    joint_pattern: Lenient[str | None] = None
    regime: Lenient[RegimeFeaturesSynthesisRegime | None] = None
    vs_expected: Lenient[RegimeFeaturesSynthesisVsExpected | None] = None


class RegimeFeaturesSynthesisRegime(_Open):
    action_default: Lenient[str | None] = None
    asymmetry_note: Lenient[str | None] = None
    family: Lenient[str | None] = None
    id: Lenient[int | float | None] = None
    intensity_count: Lenient[int | float | None] = None
    name: Lenient[str | None] = None


class RegimeFeaturesSynthesisVsExpected(_Open):
    delta_sigma: Lenient[float | None] = None
    feature: Lenient[str | None] = None
    label: Lenient[str | None] = None


class RegimeTransitions(_Open):
    by_day: Lenient[dict[str, Any] | None] = None
    calm_pre_event_count: Lenient[int | float | None] = None
    calm_pre_event_per_day: Lenient[float | None] = None
    hint: Lenient[str | None] = None
    n_snapshots: Lenient[int | float | None] = None
    symbol: Lenient[str | None] = None
    threshold_warning: Lenient[bool | None] = None
    total_transitions: Lenient[int | float | None] = None
    window_days: Lenient[int | float | None] = None


class RegimeGmmShadow(_Open):
    agreement_ratio: Lenient[float | None] = None
    by_label: Lenient[RegimeGmmShadowByLabel | None] = None
    n_total: Lenient[int | float | None] = None
    n_with_gmm: Lenient[int | float | None] = None
    promotion_gates: Lenient[RegimeGmmShadowPromotionGates | None] = None
    ready_to_promote: Lenient[bool | None] = None


class RegimeGmmShadowByLabel(_Open):
    calm: Lenient[RegimeGmmShadowByLabelCalm | None] = None


class RegimeGmmShadowByLabelCalm(_Open):
    pre_event: Lenient[int | float | None] = None


class RegimeGmmShadowPromotionGates(_Open):
    agreement_ratio_ok: Lenient[bool | None] = None
    agreement_ratio_required: Lenient[float | None] = None
    manual_check_needed: Lenient[str | None] = None
    n_required: Lenient[int | float | None] = None
    n_with_gmm_ok: Lenient[bool | None] = None


class SignalsPcaState(_Open):
    coherence: Lenient[SignalsPcaStateCoherence | None] = None
    fit_timestamp: Lenient[str | None] = None
    fit_window_end: Lenient[str | None] = None
    fit_window_start: Lenient[str | None] = None
    loadings_grid: Lenient[list[list[list[float]]] | None] = None
    loadings_stable: Lenient[SignalsPcaStateLoadingsStable | None] = None
    f_model_version: Lenient[str | None] = Field(default=None, alias="model_version")
    n_obs_in_fit: Lenient[int | float | None] = None
    signals: Lenient[SignalsPcaStateSignals | None] = None
    state: Lenient[str | None] = None
    timestamp: Lenient[str | None] = None
    variance_explained: Lenient[SignalsPcaStateVarianceExplained | None] = None


class SignalsPcaStateCoherence(_Open):
    all_coherent: Lenient[bool | None] = None
    contradictions: Lenient[list[Any] | None] = None


class SignalsPcaStateLoadingsStable(_Open):
    pc1: Lenient[bool | None] = None
    pc2: Lenient[bool | None] = None
    pc3: Lenient[bool | None] = None


class SignalsPcaStateSignals(_Open):
    pc1: Lenient[SignalsPcaStateSignalsPc1 | None] = None
    pc2: Lenient[SignalsPcaStateSignalsPc2 | None] = None
    pc3: Lenient[SignalsPcaStateSignalsPc3 | None] = None


class SignalsPcaStateSignalsPc1(_Open):
    actionable: Lenient[bool | None] = None
    actionable_reason: Lenient[str | None] = None
    label: Lenient[str | None] = None
    raw_score: Lenient[float | None] = None
    reason_category: Lenient[str | None] = None
    sub_signals: Any = None
    z_score: Lenient[float | None] = None


class SignalsPcaStateSignalsPc2(_Open):
    actionable: Lenient[bool | None] = None
    actionable_reason: Lenient[str | None] = None
    label: Lenient[str | None] = None
    raw_score: Lenient[float | None] = None
    reason_category: Lenient[str | None] = None
    sub_signals: Any = None
    z_score: Lenient[float | None] = None


class SignalsPcaStateSignalsPc3(_Open):
    actionable: Lenient[bool | None] = None
    actionable_reason: Lenient[str | None] = None
    label: Lenient[str | None] = None
    raw_score: Lenient[float | None] = None
    reason_category: Lenient[str | None] = None
    sub_signals: Lenient[dict[str, Any] | None] = None
    z_score: Lenient[float | None] = None


class SignalsPcaStateVarianceExplained(_Open):
    cumulative: Lenient[float | None] = None
    pc1: Lenient[float | None] = None
    pc2: Lenient[float | None] = None
    pc3: Lenient[float | None] = None


class TradeStructuresRow(_Open):
    display_name: Lenient[str | None] = None
    leg_template: Lenient[list[TradeStructuresRowLegTemplateItem] | None] = None
    max_legs: Lenient[int | float | None] = None
    min_legs: Lenient[int | float | None] = None
    rationale_for_pc: Lenient[str | None] = None
    requires_delta_hedge: Lenient[bool | None] = None
    structure_type: Lenient[str | None] = None
    typical_gamma_sign: Lenient[str | None] = None
    typical_theta_sign: Lenient[str | None] = None
    typical_vega_sign: Lenient[str | None] = None


class TradeStructuresRowLegTemplateItem(_Open):
    contract_type: Lenient[str | None] = None
    delta_pillar: Lenient[str | None] = None
    qty_factor: Lenient[int | float | None] = None
    side: Lenient[str | None] = None
    tenor_role: Lenient[str | None] = None
    overridable: Lenient[bool | None] = None


class TradeSubmittedRow(_Open):
    base_qty: Lenient[int | float | None] = None
    closes_trade_id: Lenient[int | float | None] = None
    contract: Lenient[str | None] = None
    created_at: Lenient[str | None] = None
    execution_mode: Lenient[str | None] = None
    id: Lenient[int | float | None] = None
    legs: Lenient[list[TradeSubmittedRowLegsItem] | None] = None
    order_role: Lenient[str | None] = None
    position_id: Lenient[int | float | None] = None
    position_state: Lenient[str | None] = None
    preview_id: Lenient[str | None] = None
    product_label: Lenient[str | None] = None
    qty_filled: Lenient[int | float | None] = None
    qty_total: Lenient[int | float | None] = None
    reference_tenor: Lenient[str | None] = None
    state: Lenient[str | None] = None
    structure_type: Lenient[str | None] = None
    total_commission_usd: Lenient[float | None] = None
    total_entry_cost_usd: Lenient[float | None] = None
    total_premium_paid_usd: Lenient[float | None] = None


class TradeSubmittedRowLegsItem(_Open):
    contract: Lenient[str | None] = None
    contract_type: Lenient[str | None] = None
    leg_idx: Lenient[int | float | None] = None
    qty: Lenient[int | float | None] = None
    qty_filled: Lenient[int | float | None] = None
    side: Lenient[str | None] = None
    state: Lenient[str | None] = None
    strike: Lenient[float | None] = None


class TradeSpotOrdersRow(_Open):
    avg_fill_price: Lenient[float | None] = None
    commission_usd: Lenient[float | None] = None
    created_at: Lenient[str | None] = None
    currency: Lenient[str | None] = None
    exchange: Lenient[str | None] = None
    filled_at: Lenient[str | None] = None
    ib_order_id: Lenient[int | float | None] = None
    ib_perm_id: Lenient[str | None] = None
    ib_status: Lenient[str | None] = None
    id: Lenient[int | float | None] = None
    limit_price: Lenient[float | None] = None
    local_symbol: Lenient[str | None] = None
    order_type: Lenient[str | None] = None
    qty: Lenient[int | float | None] = None
    qty_filled: Lenient[int | float | None] = None
    rejection_text: Lenient[str | None] = None
    order_role: Lenient[str | None] = None
    side: Lenient[str | None] = None
    state: Lenient[str | None] = None
    state_updated_at: Lenient[str | None] = None
    symbol: Lenient[str | None] = None
    why_held: Any = None


# ---- read off the router code (no recording: writes, auth-gated dev reads, empty lists) ----


class PositionDetail(_Open):
    """One booked position with its structure and latest mark (`GET /positions/{id}`)."""

    id: Any = None
    source: Any = None
    structure_id: Any = None
    structure: Any = None
    structure_type: Any = None
    product_label: Any = None
    reference_tenor: Any = None
    expiry_date: Any = None
    tenor: Any = None
    triggering_pc: Any = None
    armed_z_score: Any = None
    armed_signal_label: Any = None
    opened_at: Any = None
    state: Any = None
    entry_premium_usd: Any = None
    entry_total_cost_usd: Any = None
    entry_vega_usd_per_volpt: Any = None
    entry_gamma_usd_per_pip2: Any = None
    entry_theta_usd_per_day: Any = None
    entry_spot: Any = None
    entry_iv_avg: Any = None
    current_pnl_gross_usd: Any = None
    current_pnl_net_usd: Any = None
    vega_pnl_usd: Any = None
    gamma_pnl_usd: Any = None
    theta_pnl_usd: Any = None
    current_vega_usd_per_volpt: Any = None
    current_gamma_usd_per_pip2: Any = None
    current_theta_usd_per_day: Any = None
    current_delta_unhedged: Any = None
    last_mtm_at: Any = None
    ib_reconciled_at: Any = None
    ib_qty_total: Any = None
    ib_qty_diff: Any = None
    ib_sync_status: Any = None
    nominal_eur: Any = None
    contract_price_entry: Any = None
    contract_price_market: Any = None


class PositionMtmPoint(_Open):
    """One mark-to-market snapshot of a position."""

    timestamp: Any = None
    spot: Any = None
    iv_avg_legs_pct: Any = None
    pnl_gross_usd: Any = None
    pnl_net_usd: Any = None
    vega_pnl_usd: Any = None
    gamma_pnl_usd: Any = None
    theta_pnl_usd: Any = None
    other_pnl_usd: Any = None
    vega_usd_per_volpt: Any = None


class PositionAlertRow(_Open):
    """One exit-rule alert raised on a position."""

    id: Any = None
    timestamp: Any = None
    rule_triggered: Any = None
    action_recommended: Any = None
    priority: Any = None
    rule_detail: Any = None
    auto_executed: Any = None
    execution_status: Any = None
    closing_structure_id: Any = None


class PositionHedgeRow(_Open):
    """One delta hedge placed for a position."""

    id: Any = None
    triggered_at: Any = None
    delta_imbalance_at_trigger: Any = None
    hedge_qty: Any = None
    side: Any = None
    state: Any = None
    fill_price: Any = None
    total_cost_usd: Any = None
    ib_order_id: Any = None


class SignalTrackingPoint(_Open):
    """The arming signal's evolution since entry."""

    timestamp: Any = None
    triggering_pc: Any = None
    current_z_score: Any = None
    current_label: Any = None
    entry_z_score: Any = None
    entry_label: Any = None
    weakening_ratio: Any = None
    sign_flipped: Any = None
    status: Any = None


class ClosePositionResult(_Open):
    """A live close sent for one open position."""

    position_id: Any = None
    local_symbol: Any = None
    closed_qty: Any = None
    open_qty_before: Any = None
    order_type: Any = None
    limit_price: Any = None
    structure_id: Any = None
    order_id: Any = None
    trace_id: Any = None
    ib: Any = None




class MonitorRunReport(_Open):
    """One pass of the position monitor."""

    open_positions: Any = None
    alerts: Any = None
    hedges: Any = None


class HaltSetResult(_Open):
    """The kill switch after a halt."""

    halted: Any = None
    reason: Any = None
    set_at: Any = None
    execution_cancel: Any = None


class HaltClearResult(_Open):
    """The kill switch after a resume."""

    halted: Any = None
    reason: Any = None


class PcaRefitResult(_Open):
    """A PCA refit: the new model version and how it compares."""

    version: Any = None
    n_obs_used: Any = None
    variance_explained_ratio: Any = None
    cosine_similarity: Any = None
    previous_version: Any = None


class EventInserted(_Open):
    """A macro event written to the calendar."""

    id: Any = None
    scheduled_at: Any = None


class TradePreview(_Open):
    """A trade preview: structure, pricing, costs, scenarios and pre-submit checks."""

    preview_id: Any = None
    created_at: Any = None
    expires_at: Any = None
    mode: Any = None
    signal_source: Any = None
    structure: Any = None
    greeks_net: Any = None
    pricing: Any = None
    costs: Any = None
    scenarios: Any = None
    legs_greeks: Any = None
    pnl_grid: Any = None
    sizing: Any = None
    pre_submit_checks: Any = None
    state: Any = None
    blocking_reasons: Any = None
    surface_age_seconds: Any = None
    surface_source: Any = None
    warnings: Any = None
    spot: Any = None
    user_action: Any = None


class SubmitResult(_Open):
    """A preview submitted: the structure and position it opened."""

    success: Any = None
    structure_id: Any = None
    position_id: Any = None
    n_orders_submitted: Any = None
    execution_mode: Any = None
    state: Any = None
    total_premium_paid_usd: Any = None
    total_commission_usd: Any = None
    total_entry_cost_usd: Any = None
    fully_filled_at: Any = None
    trace_id: Any = None
    execution_engine: Any = None


class PreviewCancelled(_Open):
    """A preview the trader dropped."""

    preview_id: Any = None
    state: Any = None


class TradeCloseResult(_Open):
    """A close sent for every leg of a trade."""

    trade_id: Any = None
    total_legs: Any = None
    closed_legs: Any = None
    failed_legs: Any = None
    results: Any = None


class TradeCancelResult(_Open):
    """The working orders of a trade cancelled."""

    trade_id: Any = None
    cancelled: Any = None
    failed: Any = None
    structure_state: Any = None
    results: Any = None


class IbOrder(_Open):
    """One IB order as the execution engine reports it."""

    order_id: Any = None
    perm_id: Any = None
    symbol: Any = None
    sec_type: Any = None
    expiry: Any = None
    strike: Any = None
    right: Any = None
    local_symbol: Any = None
    con_id: Any = None
    side: Any = None
    qty: Any = None
    limit_price: Any = None
    status: Any = None
    filled: Any = None
    remaining: Any = None
    spot_order_id: Any = None
    order_ref: Any = None


class OrdersList(_Open):
    """The execution engine's open orders."""

    orders: Lenient[list[IbOrder] | None] = None
    count: Any = None


class IbPositionsList(_Open):
    """The execution engine's IB positions."""

    positions: Any = None
    count: Any = None


class RedisKeyRow(_Open):
    """One watched Redis key."""

    key: Any = None
    exists: Any = None
    ttl: Any = None
    age_s: Any = None


class RedisKeys(_Open):
    """Watched Redis keys."""

    keys: Lenient[list[RedisKeyRow] | None] = None


class RedisValue(_Open):
    """One Redis value, parsed when it is JSON."""

    key: Any = None
    value: Any = None
    raw: Any = None
    is_json: Any = None


class TableRow(_Open):
    """One ORM table."""

    name: Any = None
    n_columns: Any = None
    pk: Any = None


class TablesList(_Open):
    """Every ORM table."""

    tables: Lenient[list[TableRow] | None] = None


class TablePage(_Open):
    """One page of a table's rows."""

    table: Any = None
    total: Any = None
    limit: Any = None
    offset: Any = None
    order_by: Any = None
    order_dir: Any = None
    filters: Any = None
    columns: Any = None
    columns_meta: Any = None
    rows: Any = None


class LogContainers(_Open):
    """The containers Loki holds logs for."""

    containers: Any = None


class LogEntry(_Open):
    """One log line from Loki."""

    ts: Any = None
    container: Any = None
    message: Any = None
    labels: Any = None


class LogQueryResult(_Open):
    """Log lines matching a query, newest first."""

    entries: Lenient[list[LogEntry] | None] = None
    total: Any = None
    query: Any = None
    minutes: Any = None


class MigrationDetail(_Open):
    """One Alembic migration's source and its upgrade / downgrade bodies."""

    id: Any = None
    filename: Any = None
    content: Any = None
    upgrade: Any = None
    downgrade: Any = None


class BeaconAck(_Open):
    """How many timings a beacon recorded."""

    recorded: Any = None
