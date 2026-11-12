"""Writer of ``instrument``: one row per contract the broker has shown us (#460).

The execution engine calls this from the position sync (every held contract)
and from the fill handler (the contract of every first fill). The row is the
broker's own description of the contract, so nothing downstream needs to
rebuild a symbol, guess a multiplier or approximate an expiry.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.contracts import multiplier_for, parse_local_symbol
from persistence.models import Instrument


def _expiry(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip()
    if len(text) < 8:
        return None   # a bare contract month ("202612") is not an expiry date
    try:
        return datetime.strptime(text[:8], "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None


def _multiplier(raw: Any, local_symbol: str, sec_type: str, symbol: str | None) -> Decimal:
    """IB's multiplier when it sends one; else what the symbol implies; a spot
    pair has none and counts 1."""
    try:
        value = Decimal(str(raw)) if raw not in (None, "") else None
    except InvalidOperation:
        value = None
    if value is not None and value > 0:
        return value
    spec = parse_local_symbol(local_symbol)
    if spec is not None:
        return Decimal(str(spec.multiplier))
    if sec_type == "CASH":
        return Decimal("1")
    return Decimal(str(multiplier_for(symbol)))


def _item(
    *, con_id: Any, local_symbol: Any, sec_type: Any, symbol: Any, currency: Any, exchange: Any,
    multiplier: Any, expiry: Any, strike: Any, right: Any,
) -> dict[str, Any] | None:
    try:
        cid = int(con_id or 0)
    except (TypeError, ValueError):
        return None
    ls = str(local_symbol or "").strip()
    if cid <= 0 or not ls:
        return None
    st = str(sec_type or "").strip().upper()[:8] or "UNKNOWN"
    sym = str(symbol or "").strip()[:10] or None
    try:
        k = Decimal(str(strike)) if strike not in (None, "", 0, 0.0) else None
    except InvalidOperation:
        k = None
    r = str(right or "").strip().upper()[:1]
    return {
        "con_id": cid, "local_symbol": ls[:20], "sec_type": st, "symbol": sym,
        "currency": str(currency or "").strip().upper()[:5] or None,
        "exchange": str(exchange or "").strip()[:12] or None,
        "multiplier": _multiplier(multiplier, ls, st, sym),
        "expiry": _expiry(expiry), "strike": k, "option_right": r if r in ("C", "P") else None,
    }


def instrument_from_ib_position(p: dict[str, Any]) -> dict[str, Any] | None:
    """From one ``OrderExecutor.list_positions()`` dict."""
    return _item(
        con_id=p.get("con_id"), local_symbol=p.get("local_symbol"), sec_type=p.get("sec_type"),
        symbol=p.get("symbol"), currency=p.get("currency"), exchange=p.get("exchange"),
        multiplier=p.get("multiplier"), expiry=p.get("expiry"), strike=p.get("strike"), right=p.get("right"),
    )


def instrument_from_ib_contract(c: Any) -> dict[str, Any] | None:
    """From an ib_async ``Contract`` (``fill.contract``)."""
    if c is None:
        return None
    return _item(
        con_id=getattr(c, "conId", None), local_symbol=getattr(c, "localSymbol", None),
        sec_type=getattr(c, "secType", None), symbol=getattr(c, "symbol", None),
        currency=getattr(c, "currency", None), exchange=getattr(c, "exchange", None),
        multiplier=getattr(c, "multiplier", None),
        expiry=getattr(c, "lastTradeDateOrContractMonth", None),
        strike=getattr(c, "strike", None), right=getattr(c, "right", None),
    )


_FIELDS = ("local_symbol", "sec_type", "symbol", "currency", "exchange", "multiplier", "expiry", "strike", "option_right")


async def upsert_instruments(db: AsyncSession, items: list[dict[str, Any] | None]) -> int:
    """Insert unknown contracts, refresh changed ones. One SELECT for the
    batch; a contract that did not change costs nothing. Caller commits."""
    wanted = {int(i["con_id"]): i for i in items if i}
    if not wanted:
        return 0
    existing = {
        int(r.con_id): r for r in (await db.execute(
            select(Instrument).where(Instrument.con_id.in_(list(wanted)))
        )).scalars().all()
    }
    now = datetime.now(UTC)
    written = 0
    for cid, item in wanted.items():
        row = existing.get(cid)
        if row is None:
            db.add(Instrument(first_seen_at=now, updated_at=now, **item))
            written += 1
            continue
        changed = False
        for f in _FIELDS:
            new = item[f]
            if new is None:
                continue   # never erase what an earlier, richer sighting gave us
            old = getattr(row, f)
            if isinstance(new, Decimal) and old is not None:
                if Decimal(str(old)) == new:
                    continue
            elif old == new:
                continue
            setattr(row, f, new)
            changed = True
        if changed:
            row.updated_at = now
            written += 1
    if written:
        await db.flush()
    return written


async def expiries_by_symbol(db: AsyncSession, symbols: list[str]) -> dict[str, date]:
    """Listed expiry per IB local symbol, for the contracts we know."""
    if not symbols:
        return {}
    rows = (await db.execute(
        select(Instrument.local_symbol, Instrument.expiry)
        .where(Instrument.local_symbol.in_(symbols))
        .where(Instrument.expiry.is_not(None))
    )).all()
    return {str(s): e for s, e in rows}
