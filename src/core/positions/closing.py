"""Pure helpers for closing-structure construction (cf. STEP5 §9.3).

Given the *entry* StructureOrder rows of an open position, build the leg
spec list for a closing structure : opposite side per leg, qty equal to the
qty actually filled, same contract metadata.

The orchestrator (api.orchestration.position_close) uses this to seed a
new ``trade_structures`` row + per-leg ``structure_orders`` rows with
``order_role='closing'``, then dispatches via execution-engine.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class EntryLegSnapshot:
    leg_idx: int
    contract_type: str
    contract_strike: float
    contract_expiry: date
    contract_symbol: str
    contract_exchange: str
    contract_currency: str
    side: str               # 'BUY' | 'SELL'
    qty_filled: int
    preview_iv_pct: float | None
    preview_price: float | None


@dataclass(frozen=True)
class ClosingLegSpec:
    leg_idx: int
    contract_type: str
    contract_strike: float
    contract_expiry: date
    contract_symbol: str
    contract_exchange: str
    contract_currency: str
    side: str               # opposite of entry
    qty: int                # equals entry qty_filled
    preview_iv_pct: float | None
    preview_price: float | None


_OPPOSITE = {"BUY": "SELL", "SELL": "BUY"}


def build_closing_legs(
    entries: Sequence[EntryLegSnapshot],
) -> list[ClosingLegSpec]:
    """Build closing-leg specs from filled entry orders.

    Skips entries with ``qty_filled == 0`` (nothing to close on that leg).
    Raises ``ValueError`` when no leg has any filled qty.
    """
    out: list[ClosingLegSpec] = []
    for e in entries:
        if e.qty_filled <= 0:
            continue
        side_u = e.side.upper()
        if side_u not in _OPPOSITE:
            raise ValueError(f"unexpected entry side: {e.side!r}")
        out.append(ClosingLegSpec(
            leg_idx=e.leg_idx,
            contract_type=e.contract_type,
            contract_strike=e.contract_strike,
            contract_expiry=e.contract_expiry,
            contract_symbol=e.contract_symbol,
            contract_exchange=e.contract_exchange,
            contract_currency=e.contract_currency,
            side=_OPPOSITE[side_u],
            qty=int(e.qty_filled),
            preview_iv_pct=e.preview_iv_pct,
            preview_price=e.preview_price,
        ))
    if not out:
        raise ValueError(
            "cannot build closing structure : no entry has qty_filled > 0"
        )
    return out
