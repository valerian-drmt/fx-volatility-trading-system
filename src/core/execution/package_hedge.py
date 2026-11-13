"""An option structure and its delta hedge are ONE package (#482).

A vol desk buys or sells volatility, not direction: the option legs and the
futures that neutralise their delta are decided together and executed
together. Three rules, all pure:

* ``plan_hedge``: from the structure's delta, the futures to trade and the
  delta left once they are (the residual the limits apply to);
* ``hedge_target``: how many of those futures should be on, given how much of
  the options has filled, so a partial fill is hedged in proportion and no
  future is ever sent before an option fills;
* ``underlying_future_month``: the future an EUR option on futures delivers
  into, which is the contract to hedge with.

Before this the hedge was a second order the browser sent after the first
was accepted and before it filled: the limits judged the structure unhedged,
and unfilled options left naked futures.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from core.units import EUR_FOP_MULTIPLIER

_MONTHS = "FGHJKMNQUVXZ"   # CME month codes, January to December
_QUARTERLY = (3, 6, 9, 12)   # the 6E futures listed on the quarterly cycle
_OPT_MONTH_RE = re.compile(r"^EUU([FGHJKMNQUVXZ])(\d)")


@dataclass(frozen=True)
class HedgePlan:
    side: str                    # 'BUY' / 'SELL' of the future
    qty: int                     # futures for the whole structure
    contract_eur: float          # EUR per future
    delta_per_future_usd: float  # USD delta of one future, at the preview's spot
    structure_delta_usd: float   # the option legs' delta, unhedged
    residual_delta_usd: float    # what is left once the futures are on

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_hedge(
    structure_delta_usd: float, spot: float, *, contract_eur: float = EUR_FOP_MULTIPLIER,
) -> HedgePlan | None:
    """The futures that bring the structure's delta closest to zero. None when
    there is nothing to hedge with (no spot) or nothing to hedge (delta below
    half a future, where zero futures is already the closest)."""
    if not spot or spot <= 0:
        return None
    per_future = contract_eur * spot
    qty = round(abs(structure_delta_usd) / per_future)
    if qty <= 0:
        return None
    side = "SELL" if structure_delta_usd > 0 else "BUY"
    signed = -qty if side == "SELL" else qty
    return HedgePlan(
        side=side, qty=qty, contract_eur=contract_eur, delta_per_future_usd=round(per_future, 2),
        structure_delta_usd=round(structure_delta_usd, 2),
        residual_delta_usd=round(structure_delta_usd + signed * per_future, 2),
    )


def hedge_target(hedge_qty: int, options_filled: float, options_ordered: float) -> int:
    """Futures that should be on for the share of the options that has
    filled. 0 before any fill, the whole hedge when everything has filled;
    rounded to the nearest future in between."""
    if hedge_qty <= 0 or options_ordered <= 0 or options_filled <= 0:
        return 0
    share = min(1.0, options_filled / options_ordered)
    return min(hedge_qty, round(hedge_qty * share))


def underlying_future_month(option_local_symbol: str | None, today: date) -> str | None:
    """``YYYYMM`` of the 6E future behind an EUU option: the first quarterly
    month at or after the option's month (``EUUG7`` February 2027 -> 202703,
    ``EUUZ6`` -> 202612). None for anything that is not an EUU option."""
    m = _OPT_MONTH_RE.match(option_local_symbol or "")
    if not m:
        return None
    month = _MONTHS.index(m.group(1)) + 1
    digit = int(m.group(2))
    year = min(
        (y for y in range(today.year - 5, today.year + 5) if y % 10 == digit),
        key=lambda y: abs(y - today.year),
    )
    quarter = next(q for q in _QUARTERLY if q >= month)
    return f"{year}{quarter:02d}"
