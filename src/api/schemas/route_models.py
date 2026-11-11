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

from api.schemas.reads import _Open


class Health(_Open):
    commit: str | None = None
    git_sha: str | None = None
    status: str | None = None
    version: str | None = None


class HealthReady(_Open):
    commit: str | None = None
    components: HealthReadyComponents | None = None
    git_sha: str | None = None
    status: str | None = None
    version: str | None = None


class HealthReadyComponents(_Open):
    database: str | None = None
    redis: str | None = None


class HealthExtended(_Open):
    commit: str | None = None
    components: HealthExtendedComponents | None = None
    git_sha: str | None = None
    status: str | None = None
    timestamp: str | None = None
    version: str | None = None


class HealthExtendedComponents(_Open):
    backup: str | None = None
    database: str | None = None
    engines: HealthExtendedComponentsEngines | None = None
    redis: str | None = None


class HealthExtendedComponentsEngines(_Open):
    analytics_engine: str | None = None
    db_writer: str | None = None
    execution: str | None = None
    market_data: str | None = None
    risk_engine: str | None = None
    vol_engine: str | None = None


class PositionsBookRow(_Open):
    available: float | None = None
    avg_price: float | None = None
    contract_expiry: str | None = None
    contract_strike: float | None = None
    contract_type: str | None = None
    ib_local_symbol: str | None = None
    leg_idx: int | float | None = None
    open_qty: float | None = None
    order_id: int | float | None = None
    order_role: str | None = None
    rebuilt_at: str | None = None
    reserved_qty: float | None = None
    side: str | None = None
    structure_id: int | float | None = None
    structure_type: str | None = None


class PositionsBreaksRow(_Open):
    book_qty: float | None = None
    break_type: str | None = None
    broker_qty: float | None = None
    contract: str | None = None
    detected_at: str | None = None
    diff: float | None = None
    id: int | float | None = None
    last_seen_at: str | None = None
    resolved_at: Any = None


class PositionsStructured(_Open):
    structures: list[PositionsStructuredStructuresItem] | None = None
    unlinked: list[Any] | None = None


class PositionsStructuredStructuresItem(_Open):
    base_qty: int | float | None = None
    created_at: str | None = None
    legs: list[dict[str, Any]] | None = None
    net: dict[str, Any] | None = None
    product_label: Any = None
    state: str | None = None
    structure_id: int | float | None = None
    structure_type: str | None = None
    tenor: str | None = None


class PositionsReconciliation(_Open):
    as_of: str | None = None
    breaks: list[PositionsReconciliationBreaksItem] | None = None
    n_breaks: int | float | None = None
    n_contracts: int | float | None = None


class PositionsReconciliationBreaksItem(_Open):
    actual_net: float | None = None
    f_break: float | None = Field(default=None, alias="break")
    contract: str | None = None
    expected_net: float | None = None
    kind: str | None = None
    structure_id: int | float | None = None


class PositionsLedger(_Open):
    as_of: str | None = None
    positions: list[PositionsLedgerPositionsItem] | None = None
    totals: PositionsLedgerTotals | None = None


class PositionsLedgerPositionsItem(_Open):
    avg_cost: float | None = None
    commission: float | None = None
    contract: str | None = None
    multiplier: float | None = None
    net_qty: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None


class PositionsLedgerTotals(_Open):
    commission: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None


class PositionsActiveRow(_Open):
    armed_signal_label: Any = None
    armed_z_score: Any = None
    contract_price_entry: float | None = None
    contract_price_market: Any = None
    current_delta_unhedged: float | None = None
    current_gamma_usd_per_pip2: float | None = None
    current_pnl_gross_usd: float | None = None
    current_pnl_net_usd: float | None = None
    current_theta_usd_per_day: float | None = None
    current_vega_usd_per_volpt: float | None = None
    entry_gamma_usd_per_pip2: Any = None
    entry_iv_avg: Any = None
    entry_premium_usd: float | None = None
    entry_spot: Any = None
    entry_theta_usd_per_day: Any = None
    entry_total_cost_usd: float | None = None
    entry_vega_usd_per_volpt: Any = None
    expiry_date: str | None = None
    gamma_pnl_usd: float | None = None
    ib_qty_diff: int | float | None = None
    ib_qty_total: int | float | None = None
    ib_reconciled_at: str | None = None
    ib_sync_status: str | None = None
    id: int | float | None = None
    last_mtm_at: str | None = None
    nominal_eur: float | None = None
    opened_at: str | None = None
    product_label: str | None = None
    reference_tenor: str | None = None
    source: str | None = None
    state: str | None = None
    structure: str | None = None
    structure_id: int | float | None = None
    structure_type: str | None = None
    tenor: str | None = None
    theta_pnl_usd: float | None = None
    triggering_pc: Any = None
    vega_pnl_usd: float | None = None
    instrument_type: str | None = None
    iv: float | None = None
    option_type: str | None = None
    quantity: float | None = None
    side: str | None = None
    strike: float | None = None
    symbol: str | None = None
    vanna_usd: float | None = None
    volga_usd: float | None = None


class PositionsAggregate(_Open):
    n_delta_unknown: int | float | None = None
    n_open_positions: int | float | None = None
    total_delta_unhedged: float | None = None
    total_gamma_usd_per_pip2: float | None = None
    total_theta_usd_per_day: float | None = None
    total_vega_usd_per_volpt: float | None = None


class PortfolioAccount(_Open):
    available_funds_usd: float | None = None
    buying_power_usd: float | None = None
    freshness: str | None = None
    latest: PortfolioAccountLatest | None = None
    prev_24h: PortfolioAccountPrev24h | None = None


class PortfolioAccountLatest(_Open):
    cash_usd: float | None = None
    currencies: dict[str, dict[str, Any]] | None = None
    cushion: float | None = None
    excess_liquidity: float | None = None
    gross_position_value: float | None = None
    init_margin_req: float | None = None
    maint_margin_req: float | None = None
    net_liq_usd: float | None = None
    open_positions_count: int | float | None = None
    timestamp: str | None = None
    unrealized_pnl_usd: float | None = None


class PortfolioAccountPrev24h(_Open):
    cash_usd: float | None = None
    currencies: dict[str, dict[str, Any]] | None = None
    cushion: float | None = None
    excess_liquidity: float | None = None
    gross_position_value: float | None = None
    init_margin_req: float | None = None
    maint_margin_req: float | None = None
    net_liq_usd: float | None = None
    open_positions_count: int | float | None = None
    timestamp: str | None = None
    unrealized_pnl_usd: float | None = None


class PortfolioHeader(_Open):
    account: PortfolioHeaderAccount | None = None
    computed_at: str | None = None
    greeks: PortfolioHeaderGreeks | None = None
    pnl: PortfolioHeaderPnl | None = None
    rollup_computed_at: str | None = None
    var_1d_99: PortfolioHeaderVar1d99 | None = None


class PortfolioHeaderAccount(_Open):
    as_of: str | None = None
    cash_usd: float | None = None
    cushion: float | None = None
    excess_liquidity: float | None = None
    init_margin_req: float | None = None
    n_open_positions: int | float | None = None
    net_liq_usd: float | None = None
    util_pct: float | None = None


class PortfolioHeaderGreeks(_Open):
    delta_usd: float | None = None
    gamma_usd: float | None = None
    theta_usd: float | None = None
    vega_usd: float | None = None


class PortfolioHeaderPnl(_Open):
    open_unrealized_usd: float | None = None
    prev_close_day: str | None = None
    total_24h_usd: float | None = None


class PortfolioHeaderVar1d99(_Open):
    method: str | None = None
    n_days: int | float | None = None
    usd: float | None = None


class PortfolioEquityCurveRow(_Open):
    is_eod: bool | None = None
    net_liq_usd: float | None = None
    timestamp: str | None = None


class PortfolioValuationHistoryRow(_Open):
    contracts_usd: float | None = None
    eur_cash_usd: float | None = None
    net_liq_usd: float | None = None
    timestamp: str | None = None
    usd_cash_usd: float | None = None


class PortfolioTradeMarkersRow(_Open):
    closed_at: str | None = None
    # null in every recorded row (no closed trade, no entry spot); numbers when set
    entry_spot: float | None = None
    id: int | float | None = None
    net_pnl_usd: float | None = None
    opened_at: str | None = None
    state: str | None = None
    type: str | None = None


class PortfolioGreeksHistoryRow(_Open):
    delta_usd: float | None = None
    gamma_usd: float | None = None
    theta_usd: float | None = None
    timestamp: str | None = None
    vega_usd: float | None = None


class PortfolioGreekPnlHistoryRow(_Open):
    delta_pnl_usd: float | None = None
    gamma_pnl_usd: float | None = None
    theta_pnl_usd: float | None = None
    timestamp: str | None = None
    vega_pnl_usd: float | None = None


class PortfolioAggregateGreeks(_Open):
    computed_at: str | None = None
    n_open_positions: int | float | None = None
    n_with_snapshot: int | float | None = None
    total_delta_usd: float | None = None
    total_gamma_usd: float | None = None
    total_theta_usd: float | None = None
    total_vega_usd: float | None = None


class PortfolioVegaPerTenorRow(_Open):
    bucket: str | None = None
    dte_hi: int | float | None = None
    dte_lo: int | float | None = None
    n_positions: int | float | None = None
    vega_usd: float | None = None


class PortfolioRiskPerTenorRow(_Open):
    bucket: str | None = None
    dte_hi: int | float | None = None
    dte_lo: int | float | None = None
    n_positions: int | float | None = None
    vanna_usd: float | None = None
    vega_usd: float | None = None
    volga_usd: float | None = None


class PortfolioHedgeSummary(_Open):
    computed_at: str | None = None
    mtd: PortfolioHedgeSummaryMtd | None = None
    rolling_30d: PortfolioHedgeSummaryRolling30d | None = None
    rolling_7d: PortfolioHedgeSummaryRolling7d | None = None
    today: PortfolioHedgeSummaryToday | None = None
    wtd: PortfolioHedgeSummaryWtd | None = None
    ytd: PortfolioHedgeSummaryYtd | None = None


class PortfolioHedgeSummaryMtd(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioHedgeSummaryRolling30d(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioHedgeSummaryRolling7d(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioHedgeSummaryToday(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioHedgeSummaryWtd(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioHedgeSummaryYtd(_Open):
    cum_cost_usd: float | None = None
    n_hedges: int | float | None = None


class PortfolioStressGrid(_Open):
    axis: str | None = None
    current_spot: float | None = None
    dropped: list[Any] | None = None
    fallback: PortfolioStressGridFallback | None = None
    grid: list[list[float]] | None = None
    n_positions: int | float | None = None
    output: str | None = None
    row_bins: list[int | float] | None = None
    row_unit: str | None = None
    spot_bins_bps: list[int | float] | None = None
    spot_source: str | None = None
    vol_bins_vps: list[int | float] | None = None


class PortfolioStressGridFallback(_Open):
    spot: str | None = None


class PortfolioGreeksLadder(_Open):
    axis: str | None = None
    bins: list[int | float] | None = None
    current_spot: float | None = None
    dropped: list[Any] | None = None
    fallback: PortfolioGreeksLadderFallback | None = None
    n_positions: int | float | None = None
    rows: list[PortfolioGreeksLadderRowsItem] | None = None
    spot_bins_bps: list[int | float] | None = None
    spot_source: str | None = None
    unit: str | None = None


class PortfolioGreeksLadderFallback(_Open):
    spot: str | None = None


class PortfolioGreeksLadderRowsItem(_Open):
    axis_value: int | float | None = None
    delta_usd: float | None = None
    dspot_bps: int | float | None = None
    gamma_usd_per_pip: float | None = None
    hedge_delta_usd: float | None = None
    pnl_usd: float | None = None
    spot: float | None = None
    vega_usd_per_volpt: float | None = None


class PortfolioVegaPca(_Open):
    current_spot: float | None = None
    dropped: list[Any] | None = None
    fallback: PortfolioVegaPcaFallback | None = None
    f_model_version: str | None = Field(default=None, alias="model_version")
    n_positions: int | float | None = None
    pcs: list[PortfolioVegaPcaPcsItem] | None = None
    spot_source: str | None = None


class PortfolioVegaPcaFallback(_Open):
    spot: str | None = None


class PortfolioVegaPcaPcsItem(_Open):
    name: str | None = None
    pc: int | float | None = None
    variance_pct: float | None = None
    vega_usd: float | None = None


class PortfolioMarginalVar(_Open):
    convention: str | None = None
    dropped: list[Any] | None = None
    fallback: PortfolioMarginalVarFallback | None = None
    horizon_days: int | float | None = None
    method: str | None = None
    n_days: int | float | None = None
    positions: list[PortfolioMarginalVarPositionsItem] | None = None
    total: PortfolioMarginalVarTotal | None = None


class PortfolioMarginalVarFallback(_Open):
    spot: str | None = None


class PortfolioMarginalVarPositionsItem(_Open):
    component_usd: float | None = None
    factor: str | None = None
    id: str | None = None
    label: str | None = None
    pct: float | None = None
    standalone_usd: float | None = None
    trade: str | None = None


class PortfolioMarginalVarTotal(_Open):
    diversification_pct: float | None = None
    portfolio_var_usd: float | None = None


class PortfolioVarFactors(_Open):
    current_spot: float | None = None
    dropped: list[Any] | None = None
    factors: list[PortfolioVarFactorsFactorsItem] | None = None
    fallback: PortfolioVarFactorsFallback | None = None
    n_positions: int | float | None = None
    spot_source: str | None = None


class PortfolioVarFactorsFactorsItem(_Open):
    key: str | None = None
    label: str | None = None
    var_usd: float | None = None


class PortfolioVarFactorsFallback(_Open):
    spot: str | None = None


class PortfolioPnlAttribution(_Open):
    computed_at: str | None = None
    forward_source: str | None = None
    lookback_hours: int | float | None = None
    per_position: list[PortfolioPnlAttributionPerPositionItem] | None = None
    rollup: str | None = None
    source: str | None = None
    totals: PortfolioPnlAttributionTotals | None = None
    window_start: str | None = None


class PortfolioPnlAttributionPerPositionItem(_Open):
    actual_pnl_usd: float | None = None
    attrib_tenor: str | None = None
    contract_id: int | float | None = None
    delta_pnl_usd: float | None = None
    gamma_pnl_usd: float | None = None
    id: int | float | None = None
    iv: float | None = None
    nominal_eur: float | None = None
    product_label: str | None = None
    residual_usd: float | None = None
    side: str | None = None
    source: str | None = None
    structure: str | None = None
    structure_type: str | None = None
    tenor: str | None = None
    theta_pnl_usd: float | None = None
    trade_id: int | float | None = None
    vega_pnl_usd: float | None = None
    wing: str | None = None


class PortfolioPnlAttributionTotals(_Open):
    actual_pnl_usd: float | None = None
    delta_pnl_usd: float | None = None
    gamma_pnl_usd: float | None = None
    residual_usd: float | None = None
    theta_pnl_usd: float | None = None
    vega_pnl_usd: float | None = None


class PortfolioPinRisk(_Open):
    breach_bps: int | float | None = None
    current_spot: float | None = None
    n_options: int | float | None = None
    rows: list[PortfolioPinRiskRowsItem] | None = None


class PortfolioPinRiskRowsItem(_Open):
    delta_usd: float | None = None
    distance_pips: float | None = None
    dte_days: int | float | None = None
    expiry: str | None = None
    id: int | float | None = None
    option_type: str | None = None
    pnl_at_breach_dn_usd: float | None = None
    pnl_at_breach_up_usd: float | None = None
    pnl_at_pin_usd: float | None = None
    pnl_now_usd: float | None = None
    product_label: str | None = None
    qty: float | None = None
    side: str | None = None
    strike: float | None = None
    structure: str | None = None
    trade_id: int | float | None = None


class PortfolioScenarios(_Open):
    by_iv: list[Any] | None = None
    by_spot: list[Any] | None = None
    current_iv_avg_pct: Any = None
    current_spot: Any = None
    iv_steps_volpt: list[float] | None = None
    n_positions: int | float | None = None
    spot_steps_pct: list[float] | None = None


class PortfolioCash(_Open):
    currencies: list[PortfolioCashCurrenciesItem] | None = None
    eurusd_spot: float | None = None
    freshness: str | None = None
    timestamp: str | None = None
    total_usd: float | None = None


class PortfolioCashCurrenciesItem(_Open):
    ccy: str | None = None
    rate: float | None = None
    settled: float | None = None
    unsettled: Any = None
    usd_value: float | None = None


class PortfolioDailyPnl(_Open):
    computed_at: str | None = None
    days: int | float | None = None
    series: list[PortfolioDailyPnlSeriesItem] | None = None
    source: str | None = None
    total_mtm_usd: float | None = None


class PortfolioDailyPnlSeriesItem(_Open):
    cumulative_usd: float | None = None
    day: str | None = None
    mtm_usd: float | None = None
    n_closed: int | float | None = None
    net_liq_close: float | None = None
    realized_usd: float | None = None


class PortfolioPnlAttributionPivot(_Open):
    by: str | None = None
    groups: list[PortfolioPnlAttributionPivotGroupsItem] | None = None
    total_nominal_eur: float | None = None
    total_usd: float | None = None


class PortfolioPnlAttributionPivotGroupsItem(_Open):
    label: str | None = None
    n: int | float | None = None
    nominal_eur: float | None = None
    pnl_usd: float | None = None
    vanna_usd: float | None = None
    volga_usd: float | None = None


class PortfolioVar(_Open):
    computed_at: str | None = None
    convention: str | None = None
    current_spot: float | None = None
    dropped: list[Any] | None = None
    es_99_usd: float | None = None
    fallback: PortfolioVarFallback | None = None
    hist: list[PortfolioVarHistItem] | None = None
    horizon_days: int | float | None = None
    mean_daily_usd: float | None = None
    method: str | None = None
    n_days: int | float | None = None
    n_positions: int | float | None = None
    n_scenarios: int | float | None = None
    spot_source: str | None = None
    var_95_usd: float | None = None
    var_99_usd: float | None = None


class PortfolioVarFallback(_Open):
    spot: str | None = None


class PortfolioVarHistItem(_Open):
    count: int | float | None = None
    hi: float | None = None
    lo: float | None = None


class PortfolioGreekLimits(_Open):
    alpha: float | None = None
    computed_at: str | None = None
    cross_budget_usd: float | None = None
    delta_cap_usd: float | None = None
    gamma_cap_pip: float | None = None
    loss_budget_usd: float | None = None
    nav_base_usd: float | None = None
    nav_live_usd: float | None = None
    regime_mult: float | None = None
    spot: float | None = None
    vega_cap_usd: float | None = None


class PortfolioStats(_Open):
    computed_at: str | None = None
    cum_realized_usd: float | None = None
    cum_unrealized_usd: float | None = None
    current_drawdown_pct: float | None = None
    hit_rate: Any = None
    max_drawdown_pct: float | None = None
    n_closed: int | float | None = None
    n_days: int | float | None = None
    n_open: int | float | None = None
    n_reconciled_flat: int | float | None = None
    net_liq_change_usd: float | None = None
    sharpe: float | None = None


class AdminSettingsDomain(_Open):
    domain: str | None = None
    params: list[AdminSettingsDomainParamsItem] | None = None
    title: str | None = None


class AdminSettingsDomainParamsItem(_Open):
    default: Any = None
    description: str | None = None
    is_default: bool | None = None
    name: str | None = None
    namespace: str | None = None
    unit: str | None = None
    updated_by: Any = None
    value: Any = None


class DevStack(_Open):
    containers: list[DevStackContainersItem] | None = None
    edges: list[DevStackEdgesItem] | None = None
    timestamp: str | None = None


class DevStackContainersItem(_Open):
    desc: str | None = None
    image: str | None = None
    layer: str | None = None
    name: str | None = None
    status: str | None = None


class DevStackEdgesItem(_Open):
    f_from: str | None = Field(default=None, alias="from")
    to: str | None = None


class DevMigrations(_Open):
    chain: list[DevMigrationsChainItem] | None = None
    current: str | None = None
    head: str | None = None
    in_sync: bool | None = None
    n_applied: int | float | None = None
    n_pending: int | float | None = None
    n_total: int | float | None = None


class DevMigrationsChainItem(_Open):
    created: str | None = None
    down_revision: str | None = None
    filename: str | None = None
    id: str | None = None
    parent_title: str | None = None
    status: str | None = None
    title: str | None = None


class DevDbSchema(_Open):
    n_relationships: int | float | None = None
    n_tables: int | float | None = None
    relationships: list[DevDbSchemaRelationshipsItem] | None = None
    source: str | None = None
    tables: list[DevDbSchemaTablesItem] | None = None


class DevDbSchemaRelationshipsItem(_Open):
    cardinality: str | None = None
    composite: bool | None = None
    from_columns: list[str] | None = None
    from_table: str | None = None
    label: str | None = None
    on_delete: str | None = None
    on_update: str | None = None
    optional: bool | None = None
    self_loop: bool | None = None
    to_columns: list[str] | None = None
    to_table: str | None = None


class DevDbSchemaTablesItem(_Open):
    check_constraints: list[dict[str, Any]] | None = None
    columns: list[dict[str, Any]] | None = None
    comment: str | None = None
    composite_unique: list[dict[str, Any]] | None = None
    n_columns: int | float | None = None
    name: str | None = None


class DevHardware(_Open):
    cpu: DevHardwareCpu | None = None
    disk: DevHardwareDisk | None = None
    gpu: list[Any] | None = None
    memory: DevHardwareMemory | None = None
    timestamp: str | None = None


class DevHardwareCpu(_Open):
    cores: int | float | None = None
    load_avg: list[float] | None = None
    per_core: list[float] | None = None
    percent: float | None = None


class DevHardwareDisk(_Open):
    percent: float | None = None
    total_gb: float | None = None
    used_gb: float | None = None


class DevHardwareMemory(_Open):
    percent: float | None = None
    total_gb: float | None = None
    used_gb: float | None = None


class DevContainersMetrics(_Open):
    cpu: list[DevContainersMetricsCpuItem] | None = None
    end: str | None = None
    mem: list[DevContainersMetricsMemItem] | None = None
    reachable: bool | None = None
    source: str | None = None
    start: str | None = None
    step: int | float | None = None


class DevContainersMetricsCpuItem(_Open):
    name: str | None = None
    points: list[list[float]] | None = None


class DevContainersMetricsMemItem(_Open):
    name: str | None = None
    points: list[list[float]] | None = None


class RegimeFeatures(_Open):
    features: list[RegimeFeaturesFeaturesItem] | None = None
    symbol: str | None = None
    synthesis: RegimeFeaturesSynthesis | None = None
    timestamp: str | None = None


class RegimeFeaturesFeaturesItem(_Open):
    bucket: str | None = None
    delta_z_1h: float | None = None
    expected_z: dict[str, Any] | None = None
    name: str | None = None
    pct: int | float | None = None
    signal: str | None = None
    value: float | None = None
    vs_expected: str | None = None
    z: float | None = None


class RegimeFeaturesSynthesis(_Open):
    action: str | None = None
    dominant: str | None = None
    joint_pattern: str | None = None
    regime: RegimeFeaturesSynthesisRegime | None = None
    vs_expected: RegimeFeaturesSynthesisVsExpected | None = None


class RegimeFeaturesSynthesisRegime(_Open):
    action_default: str | None = None
    asymmetry_note: str | None = None
    family: str | None = None
    id: int | float | None = None
    intensity_count: int | float | None = None
    name: str | None = None


class RegimeFeaturesSynthesisVsExpected(_Open):
    delta_sigma: float | None = None
    feature: str | None = None
    label: str | None = None


class RegimeTransitions(_Open):
    by_day: dict[str, Any] | None = None
    calm_pre_event_count: int | float | None = None
    calm_pre_event_per_day: float | None = None
    hint: str | None = None
    n_snapshots: int | float | None = None
    symbol: str | None = None
    threshold_warning: bool | None = None
    total_transitions: int | float | None = None
    window_days: int | float | None = None


class RegimeGmmShadow(_Open):
    agreement_ratio: float | None = None
    by_label: RegimeGmmShadowByLabel | None = None
    n_total: int | float | None = None
    n_with_gmm: int | float | None = None
    promotion_gates: RegimeGmmShadowPromotionGates | None = None
    ready_to_promote: bool | None = None


class RegimeGmmShadowByLabel(_Open):
    calm: RegimeGmmShadowByLabelCalm | None = None


class RegimeGmmShadowByLabelCalm(_Open):
    pre_event: int | float | None = None


class RegimeGmmShadowPromotionGates(_Open):
    agreement_ratio_ok: bool | None = None
    agreement_ratio_required: float | None = None
    manual_check_needed: str | None = None
    n_required: int | float | None = None
    n_with_gmm_ok: bool | None = None


class SignalsPcaState(_Open):
    coherence: SignalsPcaStateCoherence | None = None
    fit_timestamp: str | None = None
    fit_window_end: str | None = None
    fit_window_start: str | None = None
    loadings_grid: list[list[list[float]]] | None = None
    loadings_stable: SignalsPcaStateLoadingsStable | None = None
    f_model_version: str | None = Field(default=None, alias="model_version")
    n_obs_in_fit: int | float | None = None
    signals: SignalsPcaStateSignals | None = None
    state: str | None = None
    timestamp: str | None = None
    variance_explained: SignalsPcaStateVarianceExplained | None = None


class SignalsPcaStateCoherence(_Open):
    all_coherent: bool | None = None
    contradictions: list[Any] | None = None


class SignalsPcaStateLoadingsStable(_Open):
    pc1: bool | None = None
    pc2: bool | None = None
    pc3: bool | None = None


class SignalsPcaStateSignals(_Open):
    pc1: SignalsPcaStateSignalsPc1 | None = None
    pc2: SignalsPcaStateSignalsPc2 | None = None
    pc3: SignalsPcaStateSignalsPc3 | None = None


class SignalsPcaStateSignalsPc1(_Open):
    actionable: bool | None = None
    actionable_reason: str | None = None
    label: str | None = None
    raw_score: float | None = None
    reason_category: str | None = None
    sub_signals: Any = None
    z_score: float | None = None


class SignalsPcaStateSignalsPc2(_Open):
    actionable: bool | None = None
    actionable_reason: str | None = None
    label: str | None = None
    raw_score: float | None = None
    reason_category: str | None = None
    sub_signals: Any = None
    z_score: float | None = None


class SignalsPcaStateSignalsPc3(_Open):
    actionable: bool | None = None
    actionable_reason: str | None = None
    label: str | None = None
    raw_score: float | None = None
    reason_category: str | None = None
    sub_signals: dict[str, Any] | None = None
    z_score: float | None = None


class SignalsPcaStateVarianceExplained(_Open):
    cumulative: float | None = None
    pc1: float | None = None
    pc2: float | None = None
    pc3: float | None = None


class TradeStructuresRow(_Open):
    display_name: str | None = None
    leg_template: list[TradeStructuresRowLegTemplateItem] | None = None
    max_legs: int | float | None = None
    min_legs: int | float | None = None
    rationale_for_pc: str | None = None
    requires_delta_hedge: bool | None = None
    structure_type: str | None = None
    typical_gamma_sign: str | None = None
    typical_theta_sign: str | None = None
    typical_vega_sign: str | None = None


class TradeStructuresRowLegTemplateItem(_Open):
    contract_type: str | None = None
    delta_pillar: str | None = None
    qty_factor: int | float | None = None
    side: str | None = None
    tenor_role: str | None = None
    overridable: bool | None = None


class TradeSubmittedRow(_Open):
    base_qty: int | float | None = None
    closes_trade_id: int | float | None = None
    contract: str | None = None
    created_at: str | None = None
    execution_mode: str | None = None
    id: int | float | None = None
    legs: list[TradeSubmittedRowLegsItem] | None = None
    order_role: str | None = None
    position_id: int | float | None = None
    position_state: str | None = None
    preview_id: str | None = None
    product_label: str | None = None
    qty_filled: int | float | None = None
    qty_total: int | float | None = None
    reference_tenor: str | None = None
    state: str | None = None
    structure_type: str | None = None
    total_commission_usd: float | None = None
    total_entry_cost_usd: float | None = None
    total_premium_paid_usd: float | None = None


class TradeSubmittedRowLegsItem(_Open):
    contract: str | None = None
    contract_type: str | None = None
    leg_idx: int | float | None = None
    qty: int | float | None = None
    qty_filled: int | float | None = None
    side: str | None = None
    state: str | None = None
    strike: float | None = None


class TradeSpotOrdersRow(_Open):
    avg_fill_price: float | None = None
    commission_usd: float | None = None
    created_at: str | None = None
    currency: str | None = None
    exchange: str | None = None
    filled_at: str | None = None
    ib_order_id: int | float | None = None
    ib_perm_id: str | None = None
    ib_status: str | None = None
    id: int | float | None = None
    limit_price: float | None = None
    local_symbol: str | None = None
    order_type: str | None = None
    qty: int | float | None = None
    qty_filled: int | float | None = None
    rejection_text: str | None = None
    side: str | None = None
    state: str | None = None
    state_updated_at: str | None = None
    symbol: str | None = None
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


class ManualCloseResult(_Open):
    """A position marked closed by hand."""

    position_id: Any = None
    state: Any = None
    phase: Any = None


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

    orders: list[IbOrder] | None = None
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

    keys: list[RedisKeyRow] | None = None


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

    tables: list[TableRow] | None = None


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

    entries: list[LogEntry] | None = None
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
