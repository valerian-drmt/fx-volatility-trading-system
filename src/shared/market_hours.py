"""Market-hours gate for the engines: the clock and the operator override
around the pure rule in ``core.market_hours`` (#332).

``is_fx_market_open()`` with no argument reads the wall clock here, in the
infrastructure layer, so ``core`` stays deterministic;
``market_gate_active()`` honours ``FORCE_RUN_MARKET_HOURS`` so an operator
can re-run engines off-hours for replay / debug.
"""
from __future__ import annotations

from datetime import UTC, datetime

from core.market_hours import is_fx_market_open as _is_open_at


def is_fx_market_open(now: datetime | None = None) -> bool:
    """True if the FX cash market is open at ``now`` (default: the wall clock)."""
    return _is_open_at(now if now is not None else datetime.now(UTC))


def market_gate_active() -> bool:
    """Return True when the engine should respect market hours."""
    from shared.config import get_settings

    return not get_settings().force_run_market_hours
