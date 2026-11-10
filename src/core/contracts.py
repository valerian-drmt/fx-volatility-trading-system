"""Per-symbol contract metadata (multiplier, exchange…) + IB localSymbol parsing.

Single source of truth used by execution-engine, risk-engine and the API
serialisation layer. Adding a new tradable symbol = one entry here, no
migration needed.

For a fully-pro setup one would persist this metadata via
``ib.qualifyContractsAsync`` at engine startup ; this static map is
sufficient for the current EURUSD-focused product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

# IB ``contract.multiplier`` for each symbol the system can hold.
# - "EUR" : standard EUR FX future + FOP options (CME, multiplier 125 000)
# - "M6E" : mini EUR FX future (CME, multiplier 12 500)
_MULTIPLIERS: dict[str, float] = {
    "EUR": 125_000.0,
    "M6E":  12_500.0,
}

DEFAULT_MULTIPLIER = 125_000.0


def multiplier_for(symbol: str | None) -> float:
    """Return the contract multiplier for ``symbol``.

    Falls back to ``DEFAULT_MULTIPLIER`` if the symbol is unknown — better
    to expose a 10x error visible in the dashboard than to silently drop
    the row.
    """
    if not symbol:
        return DEFAULT_MULTIPLIER
    return _MULTIPLIERS.get(symbol, DEFAULT_MULTIPLIER)


_FUT_MONTH_LETTERS = "FGHJKMNQUVXZ"  # IB convention Jan→Dec
_OPT_RE = re.compile(r"^EUU([FGHJKMNQUVXZ])(\d) ([CP])(\d{4})$")
_FUT_RE = re.compile(r"^(M6E|6E)([FGHJKMNQUVXZ])(\d)$")

# Canonical ``instrument_type`` values — NOT the IB ``secType`` strings
# ("FUT"/"FOP"/...). Compare against these constants so the literal can
# never drift again (cf. the risk-engine "FUT" vs "FUTURE" bug).
InstrumentType = Literal["FUTURE", "OPTION"]
INSTRUMENT_FUTURE: Final = "FUTURE"
INSTRUMENT_OPTION: Final = "OPTION"


@dataclass(frozen=True)
class ContractSpec:
    """Decoded view of an IB ``localSymbol``. ``maturity`` is intentionally
    NOT here — the DB ``positions`` table keeps it as a typed ``date`` column
    (cleaner for SQL queries than reparsing month-letter every time)."""
    symbol: str            # "EUR" or "M6E"
    instrument_type: str   # "FUTURE" or "OPTION"
    multiplier: float
    strike: float | None
    option_type: str | None  # "CALL" / "PUT" / None


def parse_local_symbol(ls: str | None) -> ContractSpec | None:
    """Decode the IB ``localSymbol`` into a :class:`ContractSpec`.

    Recognises the contract codes the system actually trades :
      - ``6E<M><Y>``    : standard EUR FX future on CME (multiplier 125 000).
      - ``M6E<M><Y>``   : Micro EUR FX future (multiplier 12 500).
      - ``EUU<M><Y> <C|P><strike×1000>`` : FOP option on EUR (multiplier 125 000).

    Returns None for unrecognised codes — caller handles fallback.
    """
    if not ls:
        return None
    m = _OPT_RE.match(ls)
    if m:
        right, strike_int = m.group(3), m.group(4)
        return ContractSpec(
            symbol="EUR",
            instrument_type=INSTRUMENT_OPTION,
            multiplier=multiplier_for("EUR"),
            strike=int(strike_int) / 1000.0,
            option_type="CALL" if right == "C" else "PUT",
        )
    m = _FUT_RE.match(ls)
    if m:
        cls = m.group(1)
        symbol = "EUR" if cls == "6E" else "M6E"
        return ContractSpec(
            symbol=symbol,
            instrument_type=INSTRUMENT_FUTURE,
            multiplier=multiplier_for(symbol),
            strike=None,
            option_type=None,
        )
    return None


def build_ib_local_symbol(
    contract_type: str | None,
    expiry: date | None,
    strike: float | None,
    symbol: str | None = "EUR",
) -> str | None:
    """Construct the IB ``localSymbol`` a leg WILL trade, from its contract fields —
    the inverse of :func:`parse_local_symbol`. Lets the UI show a not-yet-filled
    leg's contract (e.g. ``EUUV6 C1160``) BEFORE IB stamps ``ib_local_symbol`` on
    the first fill. Returns None when the fields are insufficient.

    Note: the calibrated strike may round to a slightly different IB tick than what
    IB ultimately picks, so this is a best-effort DISPLAY symbol; the authoritative
    value is still the one stamped on fill.
    """
    if expiry is None:
        return None
    try:
        month_letter = _FUT_MONTH_LETTERS[expiry.month - 1]
        year_digit = str(expiry.year)[-1]
    except (AttributeError, IndexError):
        return None
    ct = (contract_type or "").lower()
    if ct == "future":
        cls = "M6E" if symbol == "M6E" else "6E"
        return f"{cls}{month_letter}{year_digit}"
    if ct in ("call", "put") and strike:
        right = "C" if ct == "call" else "P"
        return f"EUU{month_letter}{year_digit} {right}{int(float(strike) * 1000):04d}"
    return None
