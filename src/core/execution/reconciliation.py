"""Book↔broker break classification — pure (no I/O).

Invariant I4 (OMS_ARCHITECTURE_CIBLE.md §7.2) : the book (Σ leg positions per
contract) and the broker mirror (net per contract) should agree ; any gap is
a *break* — a datum, never a silent divergence. This module owns the diff
math ; the API endpoint (`/positions/reconciliation`) and the engine
materialiser (`engines.execution.reconciler`) both consume it, so there is
exactly one definition of "what counts as a break".
"""
from __future__ import annotations

from dataclasses import dataclass

#: Nets closer than this are equal (float noise from Numeric round-trips).
BREAK_EPS = 1e-4


@dataclass(frozen=True)
class Break:
    """One book↔broker divergence on one contract."""

    contract: str        # IB localSymbol
    book_qty: float      # signed net the book expects (+ long / − short)
    broker_qty: float    # signed net the mirror reports
    diff: float          # book − broker
    break_type: str      # missing_at_ib | unbooked_at_ib | direction | quantity


def classify_break(book: float, broker: float) -> str | None:
    """Break type for a (book, broker) signed-net pair — None when they agree.

      - ``missing_at_ib``   the book holds it, IB is flat (lost fill / lag)
      - ``unbooked_at_ib``  IB holds it, the book has no record (orphan)
      - ``direction``       both hold it, signs disagree
      - ``quantity``        both hold it, sizes differ
    """
    book = round(book, 4)
    broker = round(broker, 4)
    if abs(book - broker) <= BREAK_EPS:
        return None
    if broker == 0:
        return "missing_at_ib"
    if book == 0:
        return "unbooked_at_ib"
    if (book > 0) != (broker > 0):
        return "direction"
    return "quantity"


def compute_breaks(
    book_by_contract: dict[str, float],
    broker_by_contract: dict[str, float],
) -> list[Break]:
    """Diff two signed-net-per-contract views. Sorted by contract."""
    out: list[Break] = []
    for sym in sorted(set(book_by_contract) | set(broker_by_contract)):
        book = round(book_by_contract.get(sym, 0.0), 4)
        broker = round(broker_by_contract.get(sym, 0.0), 4)
        kind = classify_break(book, broker)
        if kind is None:
            continue
        out.append(Break(
            contract=sym, book_qty=book, broker_qty=broker,
            diff=round(book - broker, 4), break_type=kind,
        ))
    return out
