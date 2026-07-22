"""Unit tests for GET /api/v1/portfolio/{var,marginal-var} — method selection.

Both endpoints prefer historical simulation (replay ~1y of daily EURUSD moves
through the current book) and fall back to the account's own P&L history only
when there is nothing to simulate. These tests pin that switch, because the
fallback is exactly the path that returns nulls on a freshly seeded deployment.
"""
from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from api.routers import portfolio_panel as pp
from api.routers.portfolio_panel import marginal_var, value_at_risk
from core.pricing.bs import bs_price

pytestmark = pytest.mark.asyncio


def _closes(n: int = 300) -> list[float]:
    """Deterministic zig-zag EURUSD closes with a moving amplitude."""
    out = [1.10]
    for i in range(n):
        amp = 0.004 * (1.0 + 0.5 * math.sin(i / 3.0))
        out.append(out[-1] * (1.0 + (amp if i % 2 == 0 else -amp)))
    return out


def _day(n: int) -> datetime:
    return datetime(2026, 7, 1, tzinfo=UTC) + timedelta(days=n)


def _bars(n: int = 300) -> str:
    return json.dumps([{"t": i, "o": c, "h": c, "l": c, "c": c} for i, c in enumerate(_closes(n))])


def _straddle() -> list[dict]:
    T = 30 / 365.0
    return [
        {"id": "11", "type": "OPTION", "qty_signed": 5, "mult": 125_000,
         "K": 1.10, "T": T, "iv": 0.08, "right": "C",
         "price_base": bs_price(1.10, 1.10, T, 0.08, "C")},
        {"id": "12", "type": "OPTION", "qty_signed": 5, "mult": 125_000,
         "K": 1.10, "T": T, "iv": 0.08, "right": "P",
         "price_base": bs_price(1.10, 1.10, T, 0.08, "P")},
    ]


class _FakeRedis:
    def __init__(self, value: str | None) -> None:
        self._value = value
        self.asked: str | None = None

    async def get(self, key: str) -> str | None:
        self.asked = key
        return self._value


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows

    def scalars(self) -> _Result:
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    """Serves a queued result per ``execute`` call, in order."""

    def __init__(self, *results: list) -> None:
        self._queue = [_Result(r) for r in results]

    async def execute(self, *_args, **_kwargs) -> _Result:
        return self._queue.pop(0) if self._queue else _Result([])


class _Pos:
    def __init__(self, pid: int) -> None:
        self.id = pid
        self.product_label = f"EUR {pid}"
        self.structure = f"struct-{pid}"
        self.trade_id = 7
        self.package_id = None
        self.delta_usd = 1_000
        self.vega_usd = 9_000        # vol-dominant → factor "level"
        self.vanna_usd = 10
        self.volga_usd = 5


@pytest.fixture
def book(monkeypatch: pytest.MonkeyPatch):
    """Patch the book resolution so the tests never need a real DB row set."""
    def _set(spot, baselines):
        async def _fake(_db):
            return spot, baselines
        monkeypatch.setattr(pp, "_resolve_book", _fake)
    return _set


# ── /var ──────────────────────────────────────────────────────────────────────

async def test_var_prefers_historical_simulation(book):
    book(1.10, _straddle())
    out = await value_at_risk(_FakeDb(), _FakeRedis(_bars()))

    assert out["method"] == "historical-simulation"
    assert out["n_positions"] == 2 and out["current_spot"] == 1.10
    # One scenario per replayed session, and that is what n_days reports.
    assert out["n_days"] == out["n_scenarios"] > 200
    # Losses, ordered: the 99% tail is worse than the 95% one, ES worse still.
    assert out["es_99_usd"] <= out["var_99_usd"] <= out["var_95_usd"] < 0
    # The chart plots the same population the quantiles came from.
    assert sum(b["count"] for b in out["hist"]) == out["n_days"]


async def test_var_falls_back_when_the_bar_cache_is_empty(book):
    book(1.10, _straddle())
    # No bars → nothing to replay. Account history holds 2 daily net-liq points.
    out = await value_at_risk(
        _FakeDb([], [(_day(1), 400_000.0), (_day(2), 395_000.0)]),
        _FakeRedis(None),
    )
    assert out["method"] == "account-history"
    assert out["var_95_usd"] is None and out["var_99_usd"] is None


async def test_var_falls_back_when_the_book_is_empty(book):
    book(None, [])
    out = await value_at_risk(_FakeDb([], []), _FakeRedis(_bars()))
    assert out["method"] == "account-history"
    assert out["n_days"] == 0


async def test_var_refuses_to_simulate_off_a_part_filled_cache(book):
    book(1.10, _straddle())
    # ~40 bars ⇒ under 20 scenarios once the RV window is consumed: a 99%
    # quantile there would be a worse number than no number at all.
    out = await value_at_risk(_FakeDb([], []), _FakeRedis(_bars(40)))
    assert out["method"] == "account-history"


async def test_var_reads_the_daily_bar_cache_key(book):
    book(1.10, _straddle())
    redis = _FakeRedis(_bars())
    await value_at_risk(_FakeDb(), redis)
    assert redis.asked == "bars:EURUSD:1Y"


async def test_var_survives_a_corrupt_bar_cache(book):
    book(1.10, _straddle())
    out = await value_at_risk(_FakeDb([], []), _FakeRedis("not json"))
    assert out["method"] == "account-history"        # fell back instead of raising


# ── /marginal-var ─────────────────────────────────────────────────────────────

async def test_marginal_var_decomposes_the_simulated_book(book):
    book(1.10, _straddle())
    out = await marginal_var(_FakeDb([_Pos(11), _Pos(12)]), _FakeRedis(_bars()))

    assert out["method"] == "historical-simulation"
    assert out["n_days"] > 200                    # scenarios, not calendar days
    assert {r["id"] for r in out["positions"]} == {"11", "12"}
    assert all(r["factor"] == "level" for r in out["positions"])   # vega-dominant
    assert all(r["label"].startswith("EUR ") and r["trade"] == "T-7" for r in out["positions"])
    # Euler allocation: the components add back up to the portfolio VaR.
    assert sum(r["component_usd"] for r in out["positions"]) == pytest.approx(
        out["total"]["portfolio_var_usd"], rel=1e-6
    )


async def test_marginal_var_empty_book_short_circuits():
    out = await marginal_var(_FakeDb([]), _FakeRedis(_bars()))
    assert out["positions"] == [] and out["total"] is None and out["n_days"] == 0


async def test_marginal_var_falls_back_without_bars(book):
    book(1.10, _straddle())
    # positions, then the open_position_history rows (2 snaps → 1 delta each,
    # below core.risk.marginal_var.MIN_DAYS → no decomposition yet).
    db = _FakeDb([_Pos(11)], [(11, 100.0), (11, 140.0)])
    out = await marginal_var(db, _FakeRedis(None))
    assert out["method"] == "account-history"
    assert out["positions"] == []
