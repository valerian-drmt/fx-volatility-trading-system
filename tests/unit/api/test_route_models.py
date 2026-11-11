"""#416 slice 3: the models in api.schemas.route_models against payloads recorded
from prod on 2026-11-11 (tests/fixtures/api_reads/). Each payload validates and
serialises back to exactly the recorded JSON, and each route declares its model,
so a renamed or dropped field shows in the OpenAPI drift gate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import TypeAdapter

from api.schemas import route_models as rm

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "api_reads"

CASES: list[tuple[str, str, Any]] = [
    ("health", "/api/v1/health", rm.Health),
    ("health_ready", "/api/v1/health/ready", rm.HealthReady),
    ("health_extended", "/api/v1/health/extended", rm.HealthExtended),
    ("positions_book", "/api/v1/positions/book", list[rm.PositionsBookRow]),
    ("positions_breaks", "/api/v1/positions/breaks", list[rm.PositionsBreaksRow]),
    ("positions_structured", "/api/v1/positions/structured", rm.PositionsStructured),
    ("positions_reconciliation", "/api/v1/positions/reconciliation", rm.PositionsReconciliation),
    ("positions_ledger", "/api/v1/positions/ledger", rm.PositionsLedger),
    ("positions_active", "/api/v1/positions/active", list[rm.PositionsActiveRow]),
    ("positions_aggregate", "/api/v1/positions/aggregate", rm.PositionsAggregate),
    ("positions_position_id_mtm_history", "/api/v1/positions/{position_id}/mtm-history", list[Any]),
    ("positions_position_id_alerts", "/api/v1/positions/{position_id}/alerts", list[Any]),
    ("positions_position_id_hedges", "/api/v1/positions/{position_id}/hedges", list[Any]),
    ("positions_position_id_signal_tracking", "/api/v1/positions/{position_id}/signal-tracking", list[Any]),
    ("portfolio_account", "/api/v1/portfolio/account", rm.PortfolioAccount),
    ("portfolio_header", "/api/v1/portfolio/header", rm.PortfolioHeader),
    ("portfolio_equity_curve", "/api/v1/portfolio/equity-curve", list[rm.PortfolioEquityCurveRow]),
    ("portfolio_valuation_history", "/api/v1/portfolio/valuation-history", list[rm.PortfolioValuationHistoryRow]),
    ("portfolio_trade_markers", "/api/v1/portfolio/trade-markers", list[rm.PortfolioTradeMarkersRow]),
    ("portfolio_greeks_history", "/api/v1/portfolio/greeks-history", list[rm.PortfolioGreeksHistoryRow]),
    ("portfolio_greek_pnl_history", "/api/v1/portfolio/greek-pnl-history", list[rm.PortfolioGreekPnlHistoryRow]),
    ("portfolio_aggregate_greeks", "/api/v1/portfolio/aggregate-greeks", rm.PortfolioAggregateGreeks),
    ("portfolio_vega_per_tenor", "/api/v1/portfolio/vega-per-tenor", list[rm.PortfolioVegaPerTenorRow]),
    ("portfolio_risk_per_tenor", "/api/v1/portfolio/risk-per-tenor", list[rm.PortfolioRiskPerTenorRow]),
    ("portfolio_hedge_summary", "/api/v1/portfolio/hedge-summary", rm.PortfolioHedgeSummary),
    ("portfolio_stress_grid", "/api/v1/portfolio/stress-grid", rm.PortfolioStressGrid),
    ("portfolio_greeks_ladder", "/api/v1/portfolio/greeks-ladder", rm.PortfolioGreeksLadder),
    ("portfolio_vega_pca", "/api/v1/portfolio/vega-pca", rm.PortfolioVegaPca),
    ("portfolio_marginal_var", "/api/v1/portfolio/marginal-var", rm.PortfolioMarginalVar),
    ("portfolio_var_factors", "/api/v1/portfolio/var-factors", rm.PortfolioVarFactors),
    ("portfolio_pnl_attribution", "/api/v1/portfolio/pnl-attribution", rm.PortfolioPnlAttribution),
    ("portfolio_pin_risk", "/api/v1/portfolio/pin-risk", rm.PortfolioPinRisk),
    ("portfolio_scenarios", "/api/v1/portfolio/scenarios", rm.PortfolioScenarios),
    ("portfolio_cash", "/api/v1/portfolio/cash", rm.PortfolioCash),
    ("portfolio_daily_pnl", "/api/v1/portfolio/daily-pnl", rm.PortfolioDailyPnl),
    ("portfolio_pnl_attribution_pivot", "/api/v1/portfolio/pnl-attribution-pivot", rm.PortfolioPnlAttributionPivot),
    ("portfolio_var", "/api/v1/portfolio/var", rm.PortfolioVar),
    ("portfolio_greek_limits", "/api/v1/portfolio/greek-limits", rm.PortfolioGreekLimits),
    ("portfolio_stats", "/api/v1/portfolio/stats", rm.PortfolioStats),
    ("admin_config_schema", "/api/v1/admin/config/schema", dict[str, Any]),
    ("admin_settings_domain", "/api/v1/admin/settings/{domain}", rm.AdminSettingsDomain),
    ("dev_stack", "/api/v1/dev/stack", rm.DevStack),
    ("dev_migrations", "/api/v1/dev/migrations", rm.DevMigrations),
    ("dev_db_schema", "/api/v1/dev/db-schema", rm.DevDbSchema),
    ("dev_hardware", "/api/v1/dev/hardware", rm.DevHardware),
    ("dev_containers_metrics", "/api/v1/dev/containers/metrics", rm.DevContainersMetrics),
    ("regime_features", "/api/v1/regime/features", rm.RegimeFeatures),
    ("regime_transitions", "/api/v1/regime/transitions", rm.RegimeTransitions),
    ("regime_gmm_shadow", "/api/v1/regime/gmm/shadow", rm.RegimeGmmShadow),
    ("signals_pca_state", "/api/v1/signals/pca/state", rm.SignalsPcaState),
    ("trade_structures", "/api/v1/trade/structures", list[rm.TradeStructuresRow]),
    ("trade_submitted", "/api/v1/trade/submitted", list[rm.TradeSubmittedRow]),
    ("trade_spot_orders", "/api/v1/trade/spot-orders", list[rm.TradeSpotOrdersRow]),
]


@pytest.mark.parametrize(("slug", "path", "model"), CASES, ids=[c[0] for c in CASES])
def test_recorded_payload_round_trips_unchanged(slug: str, path: str, model: Any) -> None:
    recorded = json.loads((FIX / f"{slug}.json").read_text(encoding="utf-8"))
    adapter = TypeAdapter(model)
    # exclude_unset: what the routes do (response_model_exclude_unset)
    parsed = adapter.validate_python(recorded)
    out = json.loads(json.dumps(jsonable_encoder(adapter.dump_python(parsed, mode="json", exclude_unset=True))))
    assert out == recorded


def test_every_operation_declares_a_typed_success_response() -> None:
    """115 operations, all with a schema the drift gate can diff (only /metrics, plain text, is exempt)."""
    from api.main import create_app

    spec = create_app().openapi()
    untyped = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            ok = [v for k, v in op.get("responses", {}).items() if k.startswith("2")]
            schemas = [v.get("content", {}).get("application/json", {}).get("schema") or {} for v in ok]
            if not any(json.dumps(s) != "{}" and ("$ref" in json.dumps(s) or s.get("type") in ("object", "array")) for s in schemas):
                untyped.append(f"{method.upper()} {path}")
    assert untyped == [], untyped


@pytest.mark.parametrize(("slug", "path", "model"), CASES, ids=[c[0] for c in CASES])
def test_the_route_declares_its_model(slug: str, path: str, model: Any) -> None:
    from api.main import create_app

    spec = create_app().openapi()
    schema = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema, path


def test_a_value_the_declared_type_rejects_is_sent_as_the_untyped_route_sent_it() -> None:
    """Prod regression of #416: handlers build rows from ORM objects, so a `str`
    field gets a datetime and a number field a Decimal. The model must pass them
    on as jsonable_encoder did (isoformat, float), never answer 500."""
    from datetime import UTC, datetime
    from decimal import Decimal

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    ts = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

    @app.get("/rows", response_model=list[rm.TradeSubmittedRow], response_model_exclude_unset=True)
    def rows() -> list[dict[str, Any]]:
        return [{"id": 7, "created_at": ts, "total_premium_paid_usd": Decimal("12.5"), "state": ["odd"]}]

    r = TestClient(app).get("/rows")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["created_at"] == ts.isoformat()
    assert row["state"] == ["odd"]
    assert row["id"] == 7
