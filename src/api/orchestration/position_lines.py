"""Loader of the position line set (#461): the one place the api decides
which lines the Trade, Risk and Portfolio panels show.

``core.positions.line_set`` is the rule; this module reads the book
(``leg_position``) and the broker mirror (``open_position``) for it and
offers the row builders the three endpoints share. No endpoint queries "what
is open" on its own any more.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.contracts import is_cash_symbol
from core.positions.line_set import BOOK_ONLY, BookLot, BrokerHolding, Line, build_lines
from core.products import product_label_from_symbol
from core.vol.tenors import tenor_bucket_from_expiry
from persistence.models import LegPosition, OpenPosition, StructureOrder, TradeStructure
from persistence.rows import open_position_row

# Columns of a mirror row that are additive in quantity: a line carrying a
# share of the holding carries the same share of them.
_SCALED = (
    "quantity", "current_pnl_usd", "nominal_eur", "delta_usd", "gamma_usd", "vega_usd",
    "theta_usd", "vanna_usd", "volga_usd",
)


@dataclass
class LineSet:
    lines: list[Line]
    positions: dict[int, OpenPosition]                 # mirror rows by id
    orders: dict[int, StructureOrder]                  # every order behind a book lot
    legs: dict[int, LegPosition]                       # leg_position by order id
    structures: dict[int, TradeStructure] = field(default_factory=dict)
    entry_legs: dict[int, int] = field(default_factory=dict)   # entry orders per trade
    closing: dict[str, dict[str, Any]] = field(default_factory=dict)   # line_id -> close in flight

    def by_id(self) -> dict[str, Line]:
        return {ln.line_id: ln for ln in self.lines}

    def for_trade(self, trade_id: int) -> list[Line]:
        return [ln for ln in self.lines if ln.trade_id == trade_id]

    def for_order(self, order_id: int) -> Line | None:
        """The line a leg belongs to. A line that nets several legs of one
        trade on one contract is carried by its first (oldest) leg."""
        for ln in self.lines:
            if ln.order_ids and ln.order_ids[0] == order_id:
                return ln
        return None


async def load_line_set(db: AsyncSession) -> LineSet:
    positions = (await db.execute(select(OpenPosition))).scalars().all()
    rows = (await db.execute(
        select(LegPosition, StructureOrder)
        .join(StructureOrder, StructureOrder.id == LegPosition.order_id)
        .where(LegPosition.open_qty != 0)
        .where(StructureOrder.ib_local_symbol.is_not(None))
    )).all()
    lots = [
        BookLot(contract=str(o.ib_local_symbol), trade_id=int(o.structure_id), order_id=int(o.id),
                qty=float(lp.open_qty or 0))
        for lp, o in rows
    ]
    holdings = [
        BrokerHolding(
            contract=str(p.structure),
            qty=float(p.quantity or 0) * (1.0 if (p.side or "").upper() == "BUY" else -1.0),
            position_id=int(p.id), is_cash=is_cash_symbol(p.structure),
        )
        for p in positions if p.structure
    ]
    lines = build_lines(lots, holdings)
    trade_ids = sorted({ln.trade_id for ln in lines if ln.trade_id is not None})
    structures: dict[int, TradeStructure] = {}
    if trade_ids:
        structures = {
            int(s.id): s for s in (await db.execute(
                select(TradeStructure).where(TradeStructure.id.in_(trade_ids))
            )).scalars().all()
        }
    entry_legs: dict[int, int] = {}
    if trade_ids:
        entry_legs = {
            int(sid): int(n) for sid, n in (await db.execute(
                select(StructureOrder.structure_id, func.count(StructureOrder.id))
                .where(StructureOrder.structure_id.in_(trade_ids))
                .where(StructureOrder.order_role.in_(("entry", "hedge")))
                .where(StructureOrder.state.notin_(("rejected", "cancelled")))
                .group_by(StructureOrder.structure_id)
            )).all()
        }
    return LineSet(
        entry_legs=entry_legs,
        closing=await _closes_in_flight(db, lines),
        lines=lines,
        positions={int(p.id): p for p in positions},
        orders={int(o.id): o for _, o in rows},
        legs={int(o.id): lp for lp, o in rows},
        structures=structures,
    )


# A closing order that is not terminal: it is on its way to IB or working there.
_WORKING = ("pending", "submitted", "acknowledged", "partially_filled")


def close_phase(order: StructureOrder) -> str:
    """Where a closing order stands, in the desk's words:

    ``sending``  written, not yet placed at IB
    ``sent``     placed, IB has not confirmed it yet
    ``waiting``  IB confirmed it holds the order; it waits for a counterparty
    ``partial``  IB filled part of it, the rest is waiting

    A close is never "rejected" here: a rejection is IB refusing the order,
    it is terminal and the position is simply open again."""
    if order.state == "partially_filled":
        return "partial"
    if order.state == "pending":
        return "sending"
    if order.state == "acknowledged" or order.acknowledged_at is not None:
        return "waiting"
    return "sent"


async def _closes_in_flight(db: AsyncSession, lines: list[Line]) -> dict[str, dict[str, Any]]:
    """The close in flight on each line: quantity still to close, limit, and
    whether IB has confirmed it holds the order. A close names the leg it
    closes (``closes_order_id``); one that does not is matched on its contract."""
    orders = (await db.execute(
        select(StructureOrder)
        .where(StructureOrder.order_role == "closing")
        .where(StructureOrder.state.in_(_WORKING))
        .order_by(StructureOrder.id)
    )).scalars().all()
    if not orders:
        return {}
    by_order: dict[int, Line] = {oid: ln for ln in lines for oid in ln.order_ids}
    by_contract: dict[str, Line] = {}
    for ln in lines:
        by_contract.setdefault(ln.contract, ln)
    out: dict[str, dict[str, Any]] = {}
    rank = {"sending": 0, "sent": 1, "waiting": 2, "partial": 3}
    for o in orders:
        ln = by_order.get(int(o.closes_order_id)) if o.closes_order_id is not None else None
        if ln is None and o.ib_local_symbol:
            ln = by_contract.get(str(o.ib_local_symbol))
        if ln is None:
            continue
        phase = close_phase(o)
        remaining = max(int(o.qty) - int(o.qty_filled or 0), 0)
        cur = out.get(ln.line_id)
        if cur is None:
            out[ln.line_id] = {
                "phase": phase, "qty": remaining, "n_orders": 1,
                "limit_price": float(o.limit_price) if o.limit_price is not None else None,
                "order_type": o.order_type,
                "structure_ids": [int(o.structure_id)], "order_ids": [int(o.id)],
                "ib_order_ids": [o.ib_order_id] if o.ib_order_id else [],
                "since": o.submitted_at.isoformat() if o.submitted_at else None,
            }
            continue
        cur["qty"] += remaining
        cur["n_orders"] += 1
        cur["structure_ids"].append(int(o.structure_id))
        cur["order_ids"].append(int(o.id))
        if o.ib_order_id:
            cur["ib_order_ids"].append(o.ib_order_id)
        if rank[phase] < rank[cur["phase"]]:
            cur["phase"] = phase   # the least advanced order tells the state of the close
    return out


def _scale(row: dict[str, Any], share: float) -> dict[str, Any]:
    if abs(share - 1.0) < 1e-9:
        return row
    out = dict(row)
    for k in _SCALED:
        if out.get(k) is not None:
            out[k] = float(out[k]) * abs(share)
    return out


def line_fields(ln: Line, ls: LineSet | None = None) -> dict[str, Any]:
    """The identity every panel row carries, whatever its other columns."""
    out: dict[str, Any] = {
        "line_id": ln.line_id, "line_status": ln.status,
        "book_qty": ln.book_qty, "broker_qty": ln.broker_qty,
        "position_id": ln.position_id,
    }
    if ls is not None:
        out["identity"] = identity(ls, ln)
    return out


def identity(ls: LineSet, ln: Line, today: date | None = None) -> dict[str, Any]:
    """The first columns of EVERY position panel, built in one place so the
    Trade, Risk and Portfolio tables cannot describe one line differently:
    trade number, IB contract number, product, contract symbol, side,
    quantity, tenor, and how many of the trade's legs are still open.

    The panels differ only in the columns after these."""
    today = today or datetime.now(UTC).date()
    struct = ls.structures.get(ln.trade_id) if ln.trade_id is not None else None
    pos = ls.positions.get(ln.position_id) if ln.position_id is not None else None
    order = ls.orders.get(ln.order_ids[0]) if ln.order_ids else None
    con_id = pos.contract_id if pos is not None and pos.contract_id is not None else (
        order.con_id if order is not None else None
    )
    return {
        "trade_id": ln.trade_id,
        "contract_id": int(con_id) if con_id is not None else None,
        "symbol": ln.contract,
        "structure_type": struct.structure_type if struct is not None else None,
        "structure_label": struct.product_label if struct is not None else None,
        "leg_label": product_label_from_symbol(ln.contract, None),
        "side": "BUY" if ln.qty > 0 else "SELL",
        "qty": abs(ln.qty),
        "tenor": tenor_bucket_from_expiry(pos.expiry, today) if pos is not None and pos.expiry else None,
        "legs_open": len(ls.for_trade(ln.trade_id)) if ln.trade_id is not None else 1,
        "legs_total": ls.entry_legs.get(int(ln.trade_id), 1) if ln.trade_id is not None else 1,
        # The close in flight on this line, None when there is none: the
        # three panels show the position as closing instead of plain open.
        "closing": ls.closing.get(ln.line_id),
    }


def mirror_rows(ls: LineSet, today: date | None = None) -> list[dict[str, Any]]:
    """One row per line in the shape of ``GET /positions/open``.

    A line the broker holds is the mirror row, scaled to the line's share
    when several trades hold the contract. A line only the book holds has no
    market data: its numbers are None (unknown), never 0.

    ``id`` stays the mirror row id for the first line of a holding, so the
    WebSocket patches keyed on it still land; other lines get a negative id
    (minus the leg's order id), unique and never a mirror id.
    """
    today = today or datetime.now(UTC).date()
    out: list[dict[str, Any]] = []
    seen_positions: set[int] = set()
    for ln in ls.lines:
        struct = ls.structures.get(ln.trade_id) if ln.trade_id is not None else None
        pos = ls.positions.get(ln.position_id) if ln.position_id is not None else None
        if pos is not None:
            row = _scale(open_position_row(pos, today), ln.share)
            if int(pos.id) in seen_positions:
                row["id"] = -int(ln.order_ids[0]) if ln.order_ids else -int(pos.id)
            seen_positions.add(int(pos.id))
            row["trade_id"] = ln.trade_id
            if struct is not None:
                row["package_id"] = struct.package_id
                row["product_label"] = product_label_from_symbol(ln.contract, struct.structure_type) or row["product_label"]
        else:
            order = ls.orders.get(ln.order_ids[0]) if ln.order_ids else None
            leg = ls.legs.get(ln.order_ids[0]) if ln.order_ids else None
            row = {
                "id": -int(ln.order_ids[0]) if ln.order_ids else 0,
                "package_id": struct.package_id if struct is not None else None,
                "trade_id": ln.trade_id,
                "contract_id": int(order.con_id) if order is not None and order.con_id is not None else None,
                "product_label": product_label_from_symbol(
                    ln.contract, struct.structure_type if struct is not None else None,
                ),
                "structure": ln.contract,
                "side": "BUY" if ln.book_qty > 0 else "SELL",
                "quantity": abs(ln.book_qty),
                "tenor": None, "tenor_now": None, "expiry": None,
                "current_pnl_usd": None, "market_price": None,
                "contract_price_entry": float(leg.avg_price) if leg is not None and leg.avg_price is not None else None,
                "nominal_eur": None,
                "delta_usd": None, "gamma_usd": None, "vega_usd": None, "theta_usd": None,
                "iv": None, "vanna_usd": None, "volga_usd": None,
                "timestamp": None, "entry_timestamp": None,
            }
        row.update(line_fields(ln, ls))
        out.append(row)
    return out


def summary(ls: LineSet) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for ln in ls.lines:
        by_status[ln.status] = by_status.get(ln.status, 0) + 1
    return {
        "as_of": datetime.now(UTC).isoformat(),
        "n_lines": len(ls.lines),
        "by_status": by_status,
        "line_ids": [ln.line_id for ln in ls.lines],
        "book_only": [ln.line_id for ln in ls.lines if ln.status == BOOK_ONLY],
    }


_ATTRIB_TERMS = (
    "actual_pnl_usd", "delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd", "residual_usd",
)


def attribution_rows(per_position: list[dict[str, Any]], ls: LineSet) -> list[dict[str, Any]]:
    """The stored per-position decomposition, re-keyed on the line set: one
    row per line, in line order (``GET /portfolio/pnl-attribution``).

    The rollup decomposes each mirror row; a line carries its share of it. A
    line with nothing to decompose is still listed, with empty terms and the
    reason in ``attrib_status``: ``not_held`` (the book holds it, the broker
    does not) or ``pending`` (held, the next rollup will price it).
    """
    stored = {int(r["id"]): r for r in per_position if r.get("source") == "ib_live" and r.get("id") is not None}
    out: list[dict[str, Any]] = []
    for ln in ls.lines:
        struct = ls.structures.get(ln.trade_id) if ln.trade_id is not None else None
        pos = ls.positions.get(ln.position_id) if ln.position_id is not None else None
        base = stored.get(int(ln.position_id)) if ln.position_id is not None else None
        if base is not None:
            row = dict(base)
            share = abs(ln.share)
            if abs(share - 1.0) > 1e-9:
                for k in (*_ATTRIB_TERMS, "nominal_eur"):
                    if row.get(k) is not None:
                        row[k] = round(float(row[k]) * share, 2)
        else:
            row = {
                "id": int(pos.id) if pos is not None else (-int(ln.order_ids[0]) if ln.order_ids else 0),
                "source": "ib_live",
                "structure": ln.contract,
                "product_label": pos.product_label if pos is not None else product_label_from_symbol(ln.contract, None),
                "side": "BUY" if ln.qty > 0 else "SELL",
                "tenor": pos.tenor if pos is not None else None,
                "attrib_tenor": None,
                "contract_id": int(pos.contract_id) if pos is not None and pos.contract_id is not None else None,
                "nominal_eur": None, "iv": None, "wing": "other", "wing_detail": None,
                "attrib_status": "pending" if pos is not None else "not_held",
                "pnl_basis": None, "t_then": None,
                **dict.fromkeys(_ATTRIB_TERMS, None),
            }
        row["trade_id"] = ln.trade_id
        row["structure_type"] = struct.structure_type if struct is not None else row.get("structure_type")
        row.update(line_fields(ln, ls))
        out.append(row)
    return out
