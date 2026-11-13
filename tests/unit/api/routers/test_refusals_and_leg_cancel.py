"""Refusals survive a reload (#478); Cancel can act on one leg (#487)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from api.routers import trades as trades_router
from api.routers.trade import list_refusals
from persistence.models import StructureOrder, TradeEvent, TradeStructure

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 9, 27, tzinfo=UTC)


async def test_refusals_are_read_back_with_their_codes_and_numbers(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        db.add(TradeEvent(
            ts=NOW, structure_id=None, event_type="submission_blocked", severity="warning",
            description="pretrade_gate: cap_delta, cap_vega",
            payload={"preview_id": "tp_1", "failures": ["cap_delta", "cap_vega"],
                     "details": {"caps": {"delta_usd": 90645.12}, "post_trade": {"delta_usd": -2575628.94}}},
        ))
        db.add(TradeEvent(
            ts=NOW.replace(minute=26), structure_id=None, event_type="submission_blocked", severity="warning",
            description="revalidation_failed: surface_stale",
            payload={"surface_age_seconds": 131.8, "max_iv_age_seconds": 120.0},
        ))
        db.add(TradeEvent(ts=NOW, structure_id=None, event_type="order_filled", severity="info", description="x"))
        await db.commit()
        out = await list_refusals(db, limit=20)
    assert [r["kind"] for r in out] == ["pretrade_gate", "revalidation_failed"], "newest first, refusals only"
    assert out[0]["failures"] == ["cap_delta", "cap_vega"] and out[0]["preview_id"] == "tp_1"
    assert out[0]["details"]["caps"]["delta_usd"] == 90645.12
    assert out[1]["failures"] == ["surface_stale"] and out[1]["details"]["surface_age_seconds"] == 131.8


class _Client:
    calls: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def delete(self, url: str, headers: Any = None) -> Any:
        _Client.calls.append(url)
        return SimpleNamespace(status_code=200, text="")


async def test_cancel_on_one_leg_leaves_the_others_working(
    sqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Client.calls = []
    monkeypatch.setattr(trades_router.httpx, "AsyncClient", _Client)
    async with sqlite_sessionmaker() as db:
        s = TradeStructure(structure_type="butterfly", reference_tenor="3M", base_qty=10, state="submitted",
                           execution_mode="live")
        db.add(s)
        await db.flush()
        legs = []
        for i in range(3):
            o = StructureOrder(structure_id=s.id, leg_idx=i, order_role="entry", contract_type="call", side="BUY",
                               qty=10, order_type="LMT", state="submitted", ib_order_id=str(100 + i))
            db.add(o)
            legs.append(o)
        await db.commit()
        out = await trades_router.cancel_trade(int(s.id), db, order_id=int(legs[1].id))
        assert out["cancel_requested"] == 1 and len(out["results"]) == 1
    assert _Client.calls == ["http://execution-engine:8001/internal/orders/101"] or _Client.calls[0].endswith("/internal/orders/101")
    assert len(_Client.calls) == 1, "only the leg that was clicked"
