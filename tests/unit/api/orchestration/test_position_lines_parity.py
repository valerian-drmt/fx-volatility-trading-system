"""The three position panels list the SAME lines (#461).

One book, one broker mirror, three endpoints: ``/positions/structured``
(Trade), ``/positions/open`` (Risk) and ``/portfolio/pnl-attribution``
(Portfolio) must return the same set of ``line_id``. The fixture is the prod
book of 2026-11-12 in miniature: a matched trade, a leg the broker no longer
holds, a contract shared by two trades, a holding no trade explains, and a
spot balance.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from api.orchestration.position_lines import attribution_rows, load_line_set, mirror_rows
from api.routers.positions import list_lines, list_open, list_structured
from persistence.models import LegPosition, OpenPosition, StructureOrder, TradeStructure

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 12, 11, 0, tzinfo=UTC)


async def _trade(db: Any, stype: str, legs: list[tuple[str, str, int, int]]) -> TradeStructure:
    """legs: (ib symbol, side, qty filled, book open qty signed)."""
    s = TradeStructure(structure_type=stype, reference_tenor="3M", base_qty=1, state="fully_filled", execution_mode="live")
    db.add(s)
    await db.flush()
    for i, (sym, side, qty, open_qty) in enumerate(legs):
        o = StructureOrder(structure_id=s.id, leg_idx=i, order_role="entry",
                           contract_type="call" if " C" in sym else ("put" if " P" in sym else "future"),
                           side=side, qty=qty, state="filled", qty_filled=qty, ib_local_symbol=sym)
        db.add(o)
        await db.flush()
        db.add(LegPosition(order_id=o.id, open_qty=Decimal(open_qty), avg_price=Decimal("0.0100")))
    await db.flush()
    return s


def _held(sym: str, side: str, qty: int, **kw: Any) -> OpenPosition:
    return OpenPosition(structure=sym, side=side, quantity=Decimal(qty), entry_timestamp=NOW, **kw)


async def _seed(db: Any) -> dict[str, int]:
    t60 = await _trade(db, "long strangle 10d", [("EUUZ6 P1075", "BUY", 100, 100), ("EUUZ6 C1210", "BUY", 100, 100)])
    t79 = await _trade(db, "butterfly", [("EUUG7 C1110", "BUY", 10, 10)])
    t90 = await _trade(db, "vanilla_call", [("EUUG7 C1110", "BUY", 30, 30)])
    t88 = await _trade(db, "future_buy", [("6EH7", "BUY", 7, 7)])
    db.add_all([
        _held("EUUZ6 C1210", "BUY", 100, delta_usd=Decimal("1000.00"), current_pnl_usd=Decimal("50.00")),
        _held("EUUG7 C1110", "BUY", 40, delta_usd=Decimal("4000.00"), current_pnl_usd=Decimal("-80.00")),
        _held("6EH7", "BUY", 5, delta_usd=Decimal("700000.00")),                 # broker holds less
        _held("EUUG7 P1120", "BUY", 10, delta_usd=Decimal("-500.00")),            # no trade explains it
        _held("EUR.USD", "SELL", 22_533, delta_usd=Decimal("-25000.00")),
    ])
    await db.commit()
    return {"t60": int(t60.id), "t79": int(t79.id), "t90": int(t90.id), "t88": int(t88.id)}


def _structured_ids(out: dict[str, Any]) -> set[str]:
    ids = {leg["line_id"] for s in out["structures"] for leg in s["legs"] if leg["open"]}
    return ids | {u["line_id"] for u in out["unlinked"]}


async def test_three_panels_one_line_set(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        expected = {
            f"EUUZ6 P1075|T{t['t60']}", f"EUUZ6 C1210|T{t['t60']}",
            f"EUUG7 C1110|T{t['t79']}", f"EUUG7 C1110|T{t['t90']}",
            "6EH7|-", "EUUG7 P1120|-", "EUR.USD|-",
        }
        trade = await list_structured(db, limit=50)
        risk = await list_open(db)
        ls = await load_line_set(db)
        portfolio = attribution_rows([], ls)
        summary = await list_lines(db)

        assert _structured_ids(trade) == expected
        assert {r["line_id"] for r in risk} == expected
        assert {r["line_id"] for r in portfolio} == expected
        assert set(summary["line_ids"]) == expected and summary["n_lines"] == 7

        status = {r["line_id"]: r["line_status"] for r in risk}
        assert status[f"EUUZ6 P1075|T{t['t60']}"] == "book_only"
        assert status[f"EUUZ6 C1210|T{t['t60']}"] == "matched"
        assert status["6EH7|-"] == "qty_mismatch", "a future is one line per contract"
        assert status["EUUG7 P1120|-"] == "broker_only"
        assert status["EUR.USD|-"] == "cash"
        # same status on the other two panels
        assert {u["line_id"]: u["line_status"] for u in trade["unlinked"]} == {
            "EUUG7 P1120|-": "broker_only", "EUR.USD|-": "cash", "6EH7|-": "qty_mismatch",
        }
        assert {r["line_id"]: r["line_status"] for r in portfolio} == status


async def test_a_line_only_the_book_holds_has_unknown_numbers_not_zeros(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        rows = {r["line_id"]: r for r in mirror_rows(await load_line_set(db))}
        put = rows[f"EUUZ6 P1075|T{t['t60']}"]
        assert put["quantity"] == 100 and put["side"] == "BUY" and put["trade_id"] == t["t60"]
        assert put["delta_usd"] is None and put["current_pnl_usd"] is None and put["position_id"] is None
        assert put["contract_price_entry"] == pytest.approx(0.01)
        assert put["id"] < 0, "never collides with a mirror row id"
        attrib = {r["line_id"]: r for r in attribution_rows([], await load_line_set(db))}
        assert attrib[f"EUUZ6 P1075|T{t['t60']}"]["attrib_status"] == "not_held"
        assert attrib["EUR.USD|-"]["attrib_status"] == "pending"


async def test_a_holding_shared_by_two_trades_is_split_and_sums_back(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        rows = {r["line_id"]: r for r in await list_open(db)}
        a, b = rows[f"EUUG7 C1110|T{t['t79']}"], rows[f"EUUG7 C1110|T{t['t90']}"]
        assert (a["quantity"], b["quantity"]) == (10, 30)
        assert a["delta_usd"] == pytest.approx(1000.0) and b["delta_usd"] == pytest.approx(3000.0)
        assert a["current_pnl_usd"] + b["current_pnl_usd"] == pytest.approx(-80.0)
        assert a["id"] != b["id"] and a["position_id"] == b["position_id"]
        # the stored decomposition of that one mirror row is shared the same way
        stored = [{"id": a["position_id"], "source": "ib_live", "actual_pnl_usd": 400.0, "delta_pnl_usd": 200.0,
                   "gamma_pnl_usd": 0.0, "vega_pnl_usd": 100.0, "theta_pnl_usd": 100.0, "residual_usd": 0.0,
                   "nominal_eur": 5_000_000.0}]
        attrib = {r["line_id"]: r for r in attribution_rows(stored, await load_line_set(db))}
        assert attrib[f"EUUG7 C1110|T{t['t79']}"]["actual_pnl_usd"] == pytest.approx(100.0)
        assert attrib[f"EUUG7 C1110|T{t['t90']}"]["actual_pnl_usd"] == pytest.approx(300.0)


async def test_trade_panel_shows_the_flagged_leg_inside_its_trade(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        out = await list_structured(db, limit=1)     # the limit no longer hides a held trade
        by_id = {s["structure_id"]: s for s in out["structures"]}
        # a trade is its options: the futures-only trade is not a trade row
        assert set(by_id) == set(t.values()) - {t["t88"]}
        legs = {leg["ib_local_symbol"]: leg for leg in by_id[t["t60"]]["legs"]}
        assert legs["EUUZ6 P1075"]["open"] is True and legs["EUUZ6 P1075"]["linked"] is False
        assert legs["EUUZ6 P1075"]["line_status"] == "book_only" and legs["EUUZ6 P1075"]["delta_usd"] is None
        assert legs["EUUZ6 C1210"]["linked"] is True and legs["EUUZ6 C1210"]["line_status"] == "matched"
        assert by_id[t["t60"]]["net"]["n_linked"] == 1 and by_id[t["t60"]]["net"]["n_lines"] == 2
        fut = next(u for u in out["unlinked"] if u["symbol"] == "6EH7")
        assert fut["line_status"] == "qty_mismatch" and fut["qty"] == 5 and fut["book_qty"] == 7


async def test_the_three_panels_describe_a_line_with_the_same_identity(sqlite_sessionmaker: Any) -> None:
    """Same lines is not enough: the first columns (trade, contract, product,
    symbol, side, quantity, tenor, legs) must be THE SAME on the three
    panels; only the columns after them differ."""
    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        trade = await list_structured(db, limit=50)
        risk = {r["line_id"]: r["identity"] for r in await list_open(db)}
        portfolio = {r["line_id"]: r["identity"] for r in attribution_rows([], await load_line_set(db))}
        desk = {leg["line_id"]: leg["identity"] for s in trade["structures"] for leg in s["legs"] if leg["open"]}
        desk.update({u["line_id"]: u["identity"] for u in trade["unlinked"]})
        assert desk == risk == portfolio

        strangle_call = risk[f"EUUZ6 C1210|T{t['t60']}"]
        assert strangle_call["structure_type"] == "long strangle 10d" and strangle_call["leg_label"] == "Vanilla Call"
        assert (strangle_call["side"], strangle_call["qty"]) == ("BUY", 100)
        assert (strangle_call["legs_open"], strangle_call["legs_total"]) == (2, 2)
        shared = risk[f"EUUG7 C1110|T{t['t79']}"]
        assert shared["qty"] == 10 and shared["legs_total"] == 1
        assert risk["EUUG7 P1120|-"]["trade_id"] is None and risk["EUUG7 P1120|-"]["legs_total"] == 1


async def test_a_close_in_flight_is_shown_on_the_line_with_what_ib_confirmed(sqlite_sessionmaker: Any) -> None:
    """Position -> Close -> the line says "closing" on the three panels, with
    whether IB has confirmed the order. Never "rejected" for an order that is
    waiting at the broker (prod 2026-11-12, EUUZ6 C1210)."""
    from sqlalchemy import select

    async with sqlite_sessionmaker() as db:
        t = await _seed(db)
        entry = (await db.execute(
            select(StructureOrder).where(StructureOrder.ib_local_symbol == "EUUZ6 C1210")
        )).scalar_one()
        ticket = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=100,
                                state="submitted", execution_mode="live")
        db.add(ticket)
        await db.flush()
        close = StructureOrder(structure_id=ticket.id, leg_idx=0, order_role="closing", contract_type="call",
                               side="SELL", qty=100, order_type="LMT", limit_price=0.0001, state="submitted",
                               ib_local_symbol="EUUZ6 C1210", closes_order_id=entry.id, ib_order_id="31")
        db.add(close)
        await db.commit()
        line = f"EUUZ6 C1210|T{t['t60']}"

        ident = {r["line_id"]: r["identity"] for r in await list_open(db)}
        assert ident[line]["closing"] == {
            "phase": "sent", "qty": 100, "n_orders": 1, "limit_price": 0.0001, "order_type": "LMT",
            "structure_ids": [ticket.id], "order_ids": [close.id], "ib_order_ids": ["31"], "since": None,
        }
        assert ident[f"EUUG7 C1110|T{t['t79']}"]["closing"] is None

        close.acknowledged_at = NOW          # IB confirmed it holds the order
        await db.commit()
        trade = await list_structured(db, limit=50)
        legs = {leg["line_id"]: leg["identity"] for s in trade["structures"] for leg in s["legs"] if leg["open"]}
        portfolio = {r["line_id"]: r["identity"] for r in attribution_rows([], await load_line_set(db))}
        assert legs[line]["closing"]["phase"] == portfolio[line]["closing"]["phase"] == "waiting"

        close.state = "rejected"             # IB refused it: terminal, the position is plain open again
        await db.commit()
        assert {r["line_id"]: r["identity"] for r in await list_open(db)}[line]["closing"] is None

