"""Unit tests for `_valuation_series` — the pure decomposition behind
`/portfolio/valuation-history` (net liq = USD cash + EUR cash ($) + contracts).

The endpoint's SQL uses Postgres-only constructs (DISTINCT ON, to_timestamp)
→ covered by the db_integration job; here we exercise the split/foot logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from api.routers.portfolio_panel import _valuation_series

T0 = datetime(2026, 7, 1, 12, 0)
HOUR = timedelta(hours=1)


def _ccy(usd: float, eur: float) -> dict:
    return {"USD": {"CashBalance": usd}, "EUR": {"CashBalance": eur}}


def test_empty_history_yields_empty_series():
    assert _valuation_series([], []) == []


def test_parts_foot_exactly_to_net_liq():
    acct = [(T0, 456_000.0, _ccy(-895_000.0, 1_200_000.0))]
    spots = [(T0, 1.13)]
    (row,) = _valuation_series(acct, spots)
    assert row["usd_cash_usd"] == pytest.approx(-895_000.0)
    assert row["eur_cash_usd"] == pytest.approx(1_200_000.0 * 1.13)
    total = row["usd_cash_usd"] + row["eur_cash_usd"] + row["contracts_usd"]
    assert total == pytest.approx(row["net_liq_usd"], abs=0.02)


def test_spot_forward_fills_and_currencies_carry():
    acct = [
        (T0, 100.0, _ccy(40.0, 50.0)),
        (T0 + HOUR, 110.0, None),  # no breakdown → carries the last one
        (T0 + 2 * HOUR, 120.0, None),
    ]
    spots = [(T0, 1.0), (T0 + 2 * HOUR, 1.2)]  # nothing at T0+1h → forward-fill
    rows = _valuation_series(acct, spots)
    assert rows[1]["eur_cash_usd"] == pytest.approx(50.0)  # still spot 1.0
    assert rows[2]["eur_cash_usd"] == pytest.approx(60.0)  # spot 1.2 kicks in
    assert rows[1]["contracts_usd"] == pytest.approx(110.0 - 40.0 - 50.0)


def test_seed_spot_covers_buckets_before_first_surface():
    acct = [(T0, 100.0, _ccy(10.0, 50.0))]
    rows = _valuation_series(acct, [], seed_spot=1.1)
    assert rows[0]["eur_cash_usd"] == pytest.approx(55.0)


def test_no_spot_or_breakdown_leaves_gap_but_plots_net_liq():
    rows = _valuation_series([(T0, 100.0, _ccy(1.0, 1.0))], [])  # no spot at all
    assert rows[0]["net_liq_usd"] == 100.0
    assert rows[0]["usd_cash_usd"] is None
    assert rows[0]["contracts_usd"] is None
    rows = _valuation_series([(T0, 100.0, None)], [(T0, 1.1)])  # no breakdown
    assert rows[0]["eur_cash_usd"] is None
