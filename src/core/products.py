"""Product label mapping — single source of truth for the user-friendly
structure name that decorates every position / trade row.

The system stores two structure identifiers per row :
- ``structure`` (IB localSymbol, e.g. ``"EUUQ6 C1130"``, ``"6EM6"``)
- ``structure_type`` (snake_case product code, e.g. ``"vanilla_call"``,
  ``"future_buy"``)

This module collapses both into one of 8 canonical labels :

    Vanilla Call · Vanilla Put · Straddle · Strangle · Butterfly ·
    Calendar · Future - 6E · Future - M6E

The helper is imported by the Alembic backfill migration (``032``) **and**
the engine writers, so the column never drifts from the writer logic.
"""
from __future__ import annotations

PRODUCT_LABELS = (
    "Vanilla Call", "Vanilla Put", "Straddle", "Strangle",
    "Butterfly", "Calendar", "Future - 6E", "Future - M6E",
)

_STRUCTURE_TYPE_TO_LABEL: dict[str, str] = {
    "vanilla_call":         "Vanilla Call",
    "short_vanilla_call":   "Vanilla Call",
    "vanilla_put":          "Vanilla Put",
    "short_vanilla_put":    "Vanilla Put",
    "straddle_atm":         "Straddle",
    "short_straddle_atm":   "Straddle",
    "long_strangle_25d":    "Strangle",
    "short_strangle":       "Strangle",
    "long_butterfly_25d":   "Butterfly",
    "short_butterfly_25d":  "Butterfly",
    "calendar_long":        "Calendar",
    "calendar_short":       "Calendar",
}


def product_label_from_symbol(
    ib_symbol: str | None,
    structure_type: str | None,
) -> str | None:
    """Return the user-friendly product label.

    Resolution order :
        1. ``structure_type`` (highest signal — exec pipeline writes it).
        2. ``ib_symbol`` parse (IB-live positions that bypass trade_structure).

    Returns ``None`` when neither input is recognised. Never raises.
    """
    if structure_type:
        if structure_type.startswith("future_"):
            return _future_label(ib_symbol)
        label = _STRUCTURE_TYPE_TO_LABEL.get(structure_type)
        if label is not None:
            return label
    if not ib_symbol:
        return None
    sym = ib_symbol.strip()
    if not sym:
        return None
    # IB option localSymbols look like "EUUQ6 C1130" / "EUUN6 P1170" :
    # 5 chars + space + C/P + strike. The space-delimited C/P token is the
    # most reliable signal across all CME FX-option series.
    if " C" in sym:
        return "Vanilla Call"
    if " P" in sym:
        return "Vanilla Put"
    # Otherwise treat as a future symbol (6E* full / M6E* micro).
    return _future_label(sym)


def _future_label(ib_symbol: str | None) -> str:
    return "Future - M6E" if ib_symbol and ib_symbol.startswith("M6E") else "Future - 6E"
