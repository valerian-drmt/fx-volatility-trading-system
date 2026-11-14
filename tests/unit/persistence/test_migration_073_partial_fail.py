"""Migration 073 (#494): the three ``partial_fail`` trades of prod, replayed."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from persistence.models import Base, LegPosition, StructureFill, StructureOrder, TradeStructure
from tests.fixtures.db import coerce_bigint_to_integer

pytestmark = pytest.mark.unit

_PATH = Path(__file__).resolve().parents[3] / "src/persistence/migrations/versions/073_partial_fail_review.py"
_spec = importlib.util.spec_from_file_location("mig_073", _PATH)
assert _spec is not None and _spec.loader is not None
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)


def _trade(db: Session, state: str) -> TradeStructure:
    t = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=1, state=state,
                       execution_mode="live")
    db.add(t)
    db.flush()
    return t


def _order(db: Session, t: TradeStructure, *, role: str, side: str, qty: int, filled: int, state: str,
           closes: int | None = None, idx: int = 0, text: str | None = None) -> StructureOrder:
    o = StructureOrder(
        structure_id=t.id, leg_idx=idx, order_role=role, contract_type="call", side=side, qty=qty,
        qty_filled=filled, order_type="LMT", limit_price=0.01, state=state, closes_order_id=closes,
        rejection_text=text, rejected_at=NOW if state == "rejected" else None,
    )
    db.add(o)
    db.flush()
    if filled:
        db.add(StructureFill(order_id=o.id, ib_execution_id=f"x{o.id}", timestamp=NOW, qty_filled=filled,
                             fill_price=0.01, commission_usd=0.0, side=side))
    return o


def _run(conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mig, "op", SimpleNamespace(get_bind=lambda: conn))
    mig.upgrade()


def test_the_three_trades_of_prod_read_as_what_happened(monkeypatch: pytest.MonkeyPatch) -> None:
    coerce_bigint_to_integer(Base.metadata)   # SQLite autoincrements INTEGER keys only
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        held = _trade(db, "fully_filled")
        e67 = _order(db, held, role="entry", side="BUY", qty=10, filled=10, state="filled", idx=0)
        e84 = _order(db, held, role="entry", side="BUY", qty=89, filled=89, state="filled", idx=1)
        e85 = _order(db, held, role="entry", side="SELL", qty=100, filled=100, state="filled", idx=2)
        db.add_all([
            LegPosition(order_id=e67.id, open_qty=Decimal(0), reserved_qty=Decimal(10)),   # stale
            LegPosition(order_id=e84.id, open_qty=Decimal(0), reserved_qty=Decimal(0)),
            LegPosition(order_id=e85.id, open_qty=Decimal(0), reserved_qty=Decimal(0)),
        ])
        why = "execution-engine unreachable: ReadTimeout"
        t78, t81, t82 = _trade(db, "partial_fail"), _trade(db, "partial_fail"), _trade(db, "partial_fail")
        o97 = _order(db, t78, role="closing", side="SELL", qty=10, filled=0, state="cancelled", closes=e67.id, text=why)
        o102 = _order(db, t81, role="closing", side="SELL", qty=89, filled=89, state="rejected", closes=e84.id, text=why)
        o103 = _order(db, t82, role="closing", side="BUY", qty=100, filled=100, state="rejected", closes=e85.id, text=why)
        # a real partial failure and a close still working: both must be left alone
        real = _trade(db, "partial_fail")
        _order(db, real, role="entry", side="BUY", qty=5, filled=5, state="filled", idx=0)
        _order(db, real, role="entry", side="BUY", qty=5, filled=0, state="rejected", idx=1)
        open_leg = _order(db, held, role="entry", side="BUY", qty=20, filled=20, state="filled", idx=3)
        working = _trade(db, "submitted")
        _order(db, working, role="closing", side="SELL", qty=20, filled=5, state="partially_filled", closes=open_leg.id)
        db.add(LegPosition(order_id=open_leg.id, open_qty=Decimal(15), reserved_qty=Decimal(15)))
        db.commit()
        ids = {"t78": t78.id, "t81": t81.id, "t82": t82.id, "real": real.id, "working": working.id,
               "o97": o97.id, "o102": o102.id, "o103": o103.id, "e67": e67.id, "open": open_leg.id}

    with engine.begin() as conn:
        _run(conn, monkeypatch)
    with engine.begin() as conn:                      # idempotent: a second run changes nothing
        _run(conn, monkeypatch)

    with Session(engine) as db:
        o102, o103, o97 = (db.get(StructureOrder, ids[k]) for k in ("o102", "o103", "o97"))
        assert (o102.state, o102.rejection_text, o102.rejected_at) == ("filled", None, None)
        assert (o103.state, o103.rejection_text) == ("filled", None)
        assert o97.state == "cancelled", "nothing filled: it stays what it was"
        assert db.get(TradeStructure, ids["t81"]).state == "fully_filled"
        assert db.get(TradeStructure, ids["t82"]).state == "fully_filled"
        assert db.get(TradeStructure, ids["t78"]).state == "fully_failed"
        assert db.get(TradeStructure, ids["real"]).state == "partial_fail", "a real mix stays"
        assert db.get(TradeStructure, ids["working"]).state == "submitted"
        legs = {lp.order_id: lp.reserved_qty for lp in db.query(LegPosition).all()}
        assert legs[ids["e67"]] == Decimal(0), "the leg a dead close reserved is free"
        assert legs[ids["open"]] == Decimal(15), "a close still working keeps its reservation"


def test_trade_state_rule() -> None:
    assert mig.trade_state(["filled"]) == "fully_filled"
    assert mig.trade_state(["cancelled"]) == "fully_failed"
    assert mig.trade_state(["filled", "rejected"]) == "partial_fail"
    assert mig.trade_state(["filled", "submitted"]) is None and mig.trade_state([]) is None


def test_revision_chain() -> None:
    assert (mig.revision, mig.down_revision) == ("073_partial_fail_review", "072_idempotency_key")
    assert len(mig.revision) <= 32
