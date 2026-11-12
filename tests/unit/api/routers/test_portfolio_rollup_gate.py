"""Grep gate for the materialised portfolio endpoints (#368, hot/cold T4).

The six endpoints (and the greek caps the pre-trade gate runs on every
submit) must read the rollup tables, never ``account_history`` /
``open_position_history``, and never carry the per-day ``DISTINCT ON`` /
``date_trunc`` scan shape that hit the 20 s statement timeout on prod
(#286). The header keeps ONE documented exception: ``latest_account_row``,
a single indexed read used only when the Redis account cache is cold.
"""
from __future__ import annotations

import inspect
import re
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from api.orchestration import greek_caps
from api.routers import portfolio_panel as pp

FORBIDDEN = ("account_history", "open_position_history", "AccountHistory", "OpenPositionHistory",
             "DISTINCT ON", "date_trunc")
GATED = {
    "portfolio_stats": pp.portfolio_stats,
    "daily_pnl": pp.daily_pnl,
    "header_summary": pp.header_summary,
    "value_at_risk": pp.value_at_risk,
    "greek_limits": pp.greek_limits,
    "pnl_attribution": pp.pnl_attribution,
    "compute_live_caps": greek_caps.compute_live_caps,
    "_daily_rows_or_seed": pp._daily_rows_or_seed,
}


@pytest.mark.parametrize("name", sorted(GATED))
def test_endpoint_source_has_no_history_scan(name: str) -> None:
    src = inspect.getsource(GATED[name])
    # Strip the docstring: it may *mention* the tables it no longer reads.
    src = re.sub(r'"""[\s\S]*?"""', "", src, count=1)
    hits = [tok for tok in FORBIDDEN if tok in src]
    assert not hits, f"{name} still touches {hits}"


def test_header_fallback_is_a_single_indexed_read() -> None:
    src = inspect.getsource(pp.header_summary)
    assert "latest_account_row" in src, "the header's only history access is the documented helper"
    from persistence import rollups
    helper = inspect.getsource(rollups.latest_account_row)
    assert "limit(1)" in helper and "desc(AccountHistory.timestamp)" in helper


# ── behaviour on the rollup rows (fakes) ───────────────────────────────────────

class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows

    def one(self) -> Any:
        return self._rows[0]

    def scalars(self) -> _Result:
        return self

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _Db:
    def __init__(self, *results: list[Any]) -> None:
        self._q = [_Result(r) for r in results]
        self.calls = 0

    async def execute(self, *_a: Any, **_k: Any) -> _Result:
        self.calls += 1
        return self._q.pop(0) if self._q else _Result([])


def _daily(day: int, close: float, mtm: float | None, realized: float = 0.0, n_closed: int = 0) -> SimpleNamespace:
    return SimpleNamespace(day=datetime(2026, 11, day, tzinfo=UTC).date(), net_liq_close=Decimal(str(close)),
                           mtm_usd=None if mtm is None else Decimal(str(mtm)), realized_usd=Decimal(str(realized)),
                           n_closed=n_closed, computed_at=datetime(2026, 11, 6, 11, 55, tzinfo=UTC))


@pytest.mark.asyncio
async def test_daily_pnl_reads_the_rollup_rows_and_echoes_computed_at() -> None:
    rows = [_daily(4, 100_500, None), _daily(5, 101_000, 500.0, realized=250.0, n_closed=1), _daily(6, 101_300, 300.0)]
    out = await pp.daily_pnl(_Db(rows), days=10)  # type: ignore[arg-type]
    assert [s["mtm_usd"] for s in out["series"]] == [0.0, 500.0, 300.0]
    assert out["series"][1]["realized_usd"] == 250.0 and out["series"][2]["cumulative_usd"] == 800.0
    assert out["total_mtm_usd"] == 800.0 and out["computed_at"] == "2026-11-06T11:55:00+00:00"
    assert out["source"] == "portfolio_daily"


@pytest.mark.asyncio
async def test_stats_sharpe_from_rollup_closes_plus_small_tables() -> None:
    rows = [_daily(d, 100_000 + 100 * d, 100.0) for d in range(1, 7)]
    closed = SimpleNamespace(n=2, wins=1, cum_real=Decimal("300"), n_recon=1)
    openp = SimpleNamespace(u=Decimal("50"), n=3)
    out = await pp.portfolio_stats(_Db(rows, [closed], [openp]))  # type: ignore[arg-type]
    assert out["n_days"] == 6 and out["net_liq_change_usd"] == 500.0
    assert out["hit_rate"] == 0.5 and out["n_reconciled_flat"] == 1 and out["cum_unrealized_usd"] == 50.0
    assert out["computed_at"] == "2026-11-06T11:55:00+00:00"


async def _no_lines(_db: Any) -> Any:
    from api.orchestration.position_lines import LineSet

    return LineSet(lines=[], positions={}, orders={}, legs={})


async def _one_line(_db: Any) -> Any:
    """The line set behind the stored row: position 1, held, no trade."""
    from api.orchestration.position_lines import LineSet
    from core.positions.line_set import BROKER_ONLY, Line

    line = Line(line_id="X|-", contract="X", trade_id=None, book_qty=0.0, broker_qty=1.0,
                status=BROKER_ONLY, position_id=1, broker_total=1.0)
    return LineSet(lines=[line], positions={}, orders={}, legs={})


@pytest.mark.asyncio
async def test_pnl_attribution_serves_the_stored_row_and_pivots_it(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = {
        "per_position": [
            {"id": 1, "source": "ib_live", "attrib_tenor": "3M", "actual_pnl_usd": 100.0, "delta_pnl_usd": 60.0,
             "gamma_pnl_usd": 10.0, "vega_pnl_usd": 20.0, "theta_pnl_usd": 5.0, "residual_usd": 5.0},
        ],
        "meta": {"lookback_hours": 24, "computed_at": "2026-11-06T11:55:00+00:00",
                 "window_start": "2026-11-05T11:55:00+00:00", "forward_source": "future_mark"},
    }

    async def _read(_db: Any, *, lookback_hours: int, now: Any) -> dict[str, Any] | None:
        return dict(stored, meta=dict(stored["meta"])) if lookback_hours == 24 else None

    async def _compute(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("a fresh row must never be recomputed on the request path")

    monkeypatch.setattr(pp, "read_attribution_latest", _read)
    monkeypatch.setattr(pp, "compute_attribution", _compute)
    monkeypatch.setattr(pp, "load_line_set", _one_line)
    out = await pp.pnl_attribution(_Db(), None, lookback_hours=24, group_by="tenor", source="ib_live")  # type: ignore[arg-type]
    assert out["rollup"] == "materialised" and out["computed_at"] == "2026-11-06T11:55:00+00:00"
    assert out["groups"][0]["label"] == "3M" and out["totals"]["actual_pnl_usd"] == 100.0
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        await pp.pnl_attribution(_Db(), None, lookback_hours=24, group_by="bogus", source="ib_live")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pnl_attribution_computes_inline_only_without_a_fresh_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _read(*_a: Any, **_k: Any) -> None:
        return None

    async def _compute(_db: Any, *, now: Any, lookback_hours: int, spot_fallback: Any) -> dict[str, Any]:
        return {"per_position": [], "meta": {"lookback_hours": lookback_hours, "computed_at": now.isoformat(),
                                             "window_start": "w", "forward_source": "none"}}

    async def _fb(*_a: Any, **_k: Any) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(pp, "read_attribution_latest", _read)
    monkeypatch.setattr(pp, "compute_attribution", _compute)
    monkeypatch.setattr(pp, "load_line_set", _no_lines)
    monkeypatch.setattr(pp, "spot_fallbacks", _fb)
    out = await pp.pnl_attribution(_Db(), None, lookback_hours=4, group_by=None, source="ib_live")  # type: ignore[arg-type]
    assert out["rollup"] == "inline" and out["per_position"] == [] and out["totals"]["actual_pnl_usd"] is None
