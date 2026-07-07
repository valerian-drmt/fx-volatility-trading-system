"""Book vs broker reconciliation — pure classification (invariant I4).

The book (Σ ``leg_position.open_qty`` per contract — our forward truth) and the
broker mirror (``open_position`` net per contract — the checksum) should agree.
Any gap is a *break*, materialised as data (never a silent discrepancy): it is
detected, it lives, it resolves, it audits. This module is the pure diff/classify;
``engines.execution.reconciler`` folds the two dicts and persists the breaks, and
the ``/positions/reconciliation`` endpoint reuses the same classification.

``book`` / ``broker`` are **signed** nets keyed by IB ``localSymbol``
(BUY = +, SELL = −).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Below this, a book − broker difference is rounding noise, not a break.
BREAK_EPS = 1e-4


@dataclass(frozen=True)
class Break:
    contract: str      # IB localSymbol
    book_qty: float
    broker_qty: float
    diff: float        # book − broker
    break_type: str


def classify_break(book_qty: float, broker_qty: float) -> str | None:
    """Return the break type, or ``None`` if book and broker agree.

    - ``missing_at_ib``   book holds it, IB is flat (fill not reflected / lag)
    - ``unbooked_at_ib``  IB holds it, the book has no record (manual / orphan)
    - ``direction``       signs disagree (we think long, IB is short)
    - ``quantity``        both hold it, sizes differ
    """
    book = round(book_qty, 4)
    broker = round(broker_qty, 4)
    if abs(round(book - broker, 4)) <= BREAK_EPS:
        return None
    if broker == 0:
        return "missing_at_ib"
    if book == 0:
        return "unbooked_at_ib"
    if (book > 0) != (broker > 0):
        return "direction"
    return "quantity"


def compute_breaks(
    book: Mapping[str, float], broker: Mapping[str, float]
) -> list[Break]:
    """Diff two signed-net-by-contract maps into the list of open breaks."""
    out: list[Break] = []
    for sym in sorted(set(book) | set(broker)):
        b = round(book.get(sym, 0.0), 4)
        k = round(broker.get(sym, 0.0), 4)
        break_type = classify_break(b, k)
        if break_type is None:
            continue
        out.append(Break(
            contract=sym, book_qty=b, broker_qty=k,
            diff=round(b - k, 4), break_type=break_type,
        ))
    return out
