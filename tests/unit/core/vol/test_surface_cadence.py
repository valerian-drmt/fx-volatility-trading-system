"""The surface freshness limit follows the publishing cadence (#480).

The vol engine publishes every 180 s; the limit was 120 s. For a third of
every cycle the latest surface was "stale", and a preview built then was
refused at submit for its whole life (prod, 2026-11-13: surface 124 s and
132 s old against 120).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.risk.pretrade import BookGreeks, Caps, GateInputs, TradeGreeks, evaluate_pretrade
from core.vol.cadence import (
    SURFACE_PUBLISH_INTERVAL_S,
    is_surface_fresh,
    surface_age_floor_s,
    surface_age_limit_s,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]


def test_the_limit_is_never_tighter_than_the_cadence_allows() -> None:
    assert surface_age_floor_s() > SURFACE_PUBLISH_INTERVAL_S
    assert surface_age_limit_s(120) == surface_age_floor_s() == 270.0, "the configured 120 s cannot be met"
    assert surface_age_limit_s(600) == 600.0, "a looser configured limit is kept"
    assert surface_age_limit_s(None) == 270.0 and surface_age_limit_s("bad") == 270.0


def test_the_ages_refused_on_prod_are_fresh_and_a_missed_cycle_is_not() -> None:
    assert is_surface_fresh(124.0, 120) and is_surface_fresh(132.0, 120)
    assert is_surface_fresh(185.0, 120), "a full cycle plus its computing time"
    assert not is_surface_fresh(365.0, 120), "one publication missed"
    assert not is_surface_fresh(999.0, 120), "the synthetic surface"
    assert not is_surface_fresh(None, 120), "unknown is not fresh"


def test_the_vol_engine_and_the_limit_share_one_cadence() -> None:
    engine = (ROOT / "src/engines/vol/engine.py").read_text(encoding="utf-8")
    assert "CYCLE_S = SURFACE_PUBLISH_INTERVAL_S" in engine
    for path in ("src/api/routers/trade.py", "src/api/orchestration/pretrade_gate.py"):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert "surface_age_limit_s(" in src
        assert 'get("max_iv_data_age_seconds", 120' not in src, f"{path} still enforces the raw config"


def test_the_gate_uses_the_limit_it_is_given() -> None:
    ok = GateInputs(
        halted=False, halt_reason=None, live_mode=True, risk_heartbeat_age_s=1.0, max_risk_heartbeat_age_s=30.0,
        ib_account_type="paper", required_account_type="paper",
        book=BookGreeks(0.0, 0.0, 0.0, 0, 0),
        trade=TradeGreeks(delta_usd=1.0, vega_usd_per_volpt=1.0, gamma_usd_per_pip=1.0, notional_eur=1.0),
        caps=Caps(delta_usd=100.0, vega_usd_per_volpt=100.0, gamma_usd_per_pip=100.0, max_notional_eur=100.0),
        symbols=("EUR",), allowed_symbols=("EUR",),
        surface_age_s=132.0, max_surface_age_s=surface_age_limit_s(120), arb_ok=None,
    )
    assert evaluate_pretrade(ok).passed
    from dataclasses import replace

    assert "surface_stale" in evaluate_pretrade(replace(ok, surface_age_s=400.0)).failures
