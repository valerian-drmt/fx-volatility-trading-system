"""The position line set: ONE list of lines for every position panel (#461).

The Trade, Risk and Portfolio panels are three perspectives on the same
portfolio: the same lines, different columns. They used to answer "what is
open?" from three different tables with three different rules (8, 7 and 7
rows for 7 holdings on 2026-11-12). This module is the single answer.

A FUTURE is one line per contract, whatever trades hold it (``is_pooled``).

An option line is one **(contract, trade) lot**:

* the book says how much each trade holds of each contract (netted legs);
* the broker says how much of each contract the account holds;
* when the lots of a contract add up to what the broker holds, each lot is a
  line as its trade holds it (long or short);
* when they do not, opposite lots cancel and the broker quantity is allocated
  to what is left, oldest trade first; what cannot be allocated is a line of
  its own.

Every line carries a status, so a disagreement between the book and the
broker is VISIBLE on every panel instead of being a row on one of them:

``matched``       the book and the broker agree on this lot
``qty_mismatch``  both hold it, in different quantities
``book_only``     the book holds it, the broker does not
``broker_only``   the broker holds it, no trade in the book explains it
``cash``          a spot FX balance: cash management, not a booked position

Pure: no I/O. ``api.orchestration.position_lines`` loads the inputs.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from core.contracts import INSTRUMENT_FUTURE, parse_local_symbol

EPS = 1e-6

MATCHED = "matched"
QTY_MISMATCH = "qty_mismatch"
BOOK_ONLY = "book_only"
BROKER_ONLY = "broker_only"
CASH = "cash"
STATUSES = (MATCHED, QTY_MISMATCH, BOOK_ONLY, BROKER_ONLY, CASH)


@dataclass(frozen=True)
class BookLot:
    """What one leg holds, from ``leg_position`` (signed)."""

    contract: str        # IB local symbol the leg FILLED
    trade_id: int
    order_id: int
    qty: float           # signed: + long / − short


@dataclass(frozen=True)
class BrokerHolding:
    """One row of the broker mirror (``open_position``), signed."""

    contract: str
    qty: float
    position_id: int
    is_cash: bool = False


@dataclass(frozen=True)
class Line:
    line_id: str               # stable across panels and refreshes
    contract: str
    trade_id: int | None       # None: no trade explains this holding
    book_qty: float            # signed, what the book says this lot holds
    broker_qty: float          # signed, the part of the broker holding given to this lot
    status: str
    position_id: int | None    # the mirror row behind ``broker_qty``
    order_ids: tuple[int, ...] = ()
    broker_total: float = 0.0  # the whole broker holding on this contract

    @property
    def share(self) -> float:
        """Fraction of the mirror row's numbers (greeks, P&L) this line carries."""
        if abs(self.broker_total) < EPS:
            return 0.0
        return self.broker_qty / self.broker_total

    @property
    def qty(self) -> float:
        """The quantity to display: what is really held when the broker holds
        some of it, else what the book believes."""
        return self.broker_qty if abs(self.broker_qty) > EPS else self.book_qty


def line_id(contract: str, trade_id: int | None) -> str:
    return f"{contract}|T{trade_id}" if trade_id is not None else f"{contract}|-"


def is_pooled(contract: str) -> bool:
    """A future is ONE position per contract, whatever trades bought or sold
    it (owner's rule, 2026-11-13). A trade is one or several options; the
    futures, bought outright or sent as the hedge of a package, are the
    account's stock of a handful of contracts (one per quarter) and sit on
    one line each. The book still knows which trade traded what."""
    spec = parse_local_symbol(contract)
    return spec is not None and spec.instrument_type == INSTRUMENT_FUTURE


def _pooled_line(contract: str, book: float, total: float, pid: int | None) -> Line | None:
    if abs(book) <= EPS and abs(total) <= EPS:
        return None
    if abs(book - total) <= EPS:
        status = MATCHED
    elif abs(total) <= EPS:
        status = BOOK_ONLY
    elif abs(book) <= EPS:
        status = BROKER_ONLY
    else:
        status = QTY_MISMATCH
    return Line(
        line_id=line_id(contract, None), contract=contract, trade_id=None,
        book_qty=book, broker_qty=total, status=status,
        position_id=pid if abs(total) > EPS else None, broker_total=total,
    )


def _same_sign(a: float, b: float) -> bool:
    return (a > 0) == (b > 0)


def _offset_opposites(
    trades: list[tuple[int, float, tuple[int, ...]]],
) -> list[tuple[int, float, tuple[int, ...]]]:
    """Long and short lots of different trades on ONE contract cancel out,
    oldest first: the broker nets a contract, so two trades on opposite sides
    hold nothing between them. Only the residual stays a line.

    Prod, 2026-11-12: fourteen July trades on 6EZ6 (179 long, 192 short, none
    closed against another) showed as fourteen "book only" lines; what the
    book really disagrees with the broker about is the residual, 13 short."""
    longs = [[t, q, o] for t, q, o in trades if q > 0]
    shorts = [[t, q, o] for t, q, o in trades if q < 0]
    i = j = 0
    while i < len(longs) and j < len(shorts):
        cut = min(longs[i][1], -shorts[j][1])
        longs[i][1] -= cut
        shorts[j][1] += cut
        if longs[i][1] <= EPS:
            i += 1
        if shorts[j][1] >= -EPS:
            j += 1
    left = [(t, q, o) for t, q, o in (*longs, *shorts) if abs(q) > EPS]
    return sorted(left, key=lambda x: x[0])


def build_lines(lots: Iterable[BookLot], holdings: Iterable[BrokerHolding]) -> list[Line]:
    """The line set. Deterministic: contracts in name order, trades oldest first."""
    by_contract: dict[str, dict[int, list[BookLot]]] = defaultdict(lambda: defaultdict(list))
    for lot in lots:
        if lot.contract:
            by_contract[lot.contract][lot.trade_id].append(lot)
    held: dict[str, BrokerHolding] = {}
    for h in holdings:
        if not h.contract:
            continue
        prev = held.get(h.contract)
        held[h.contract] = h if prev is None else BrokerHolding(
            contract=h.contract, qty=prev.qty + h.qty, position_id=prev.position_id, is_cash=prev.is_cash,
        )

    out: list[Line] = []
    for contract in sorted(set(by_contract) | set(held)):
        holding = held.get(contract)
        total = holding.qty if holding is not None else 0.0
        pid = holding.position_id if holding is not None else None
        if is_pooled(contract):
            book = sum(lot.qty for legs in by_contract.get(contract, {}).values() for lot in legs)
            pooled = _pooled_line(contract, book, total, pid)
            if pooled is not None:
                out.append(pooled)
            continue
        remaining = total
        trades = []
        for trade_id in sorted(by_contract.get(contract, {})):
            legs = by_contract[contract][trade_id]
            qty = sum(lot.qty for lot in legs)
            if abs(qty) > EPS:
                trades.append((trade_id, qty, tuple(sorted(lot.order_id for lot in legs))))
        if trades and abs(total) > EPS and abs(sum(q for _, q, _ in trades) - total) <= EPS:
            # The book and the broker agree on this contract: every trade's lot
            # is a line, as the trade holds it, long or short. The broker nets
            # a contract, the desk does not: the futures that hedge one package
            # stay on that package even when another package holds the other
            # side. (Prod, 2026-11-13: +30, +1, -37, +22, +6 on 6EH7, 22 held;
            # netting the lots first showed 16 and 6, and three hedges on no
            # line at all.) A lot carries its signed share of the holding.
            # A contract the broker does not hold at all has no mark to show:
            # lots that cancel there stay off the panels, as before.
            for trade_id, book_qty, order_ids in trades:
                out.append(Line(
                    line_id=line_id(contract, trade_id), contract=contract, trade_id=trade_id,
                    book_qty=book_qty, broker_qty=book_qty, status=MATCHED,
                    position_id=pid, order_ids=order_ids, broker_total=total,
                ))
            continue
        # They disagree: only what is left once opposite lots cancel can be
        # compared with the broker, so the disagreement shows on its own.
        trades = _offset_opposites(trades)
        for trade_id, book_qty, order_ids in trades:
            given = 0.0
            if abs(remaining) > EPS and _same_sign(book_qty, remaining):
                given = min(abs(book_qty), abs(remaining)) * (1.0 if remaining > 0 else -1.0)
                remaining -= given
            if abs(given) <= EPS:
                status = BOOK_ONLY
            elif abs(given - book_qty) <= EPS:
                status = MATCHED
            else:
                status = QTY_MISMATCH
            out.append(Line(
                line_id=line_id(contract, trade_id), contract=contract, trade_id=trade_id,
                book_qty=book_qty, broker_qty=given, status=status,
                position_id=pid if abs(given) > EPS else None, order_ids=order_ids, broker_total=total,
            ))
        if abs(remaining) > EPS:
            is_cash = holding is not None and holding.is_cash
            out.append(Line(
                line_id=line_id(contract, None), contract=contract, trade_id=None,
                book_qty=0.0, broker_qty=remaining, status=CASH if is_cash else BROKER_ONLY,
                position_id=pid, broker_total=total,
            ))
    return out


def unmatched(lines: Iterable[Line]) -> list[Line]:
    """Lines an operator must look at: everything but matched lots and cash."""
    return [ln for ln in lines if ln.status not in (MATCHED, CASH)]
