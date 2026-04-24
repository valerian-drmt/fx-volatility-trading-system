"""Build DB row payloads from engine outputs.

Pure transformations: engine-native dict → DB table dict ready to enqueue.
No state, no IO — all testable in isolation. The Controller wires these
into its result-handling callbacks (``_on_market_data_payload``,
``_on_vol_result``) and passes the output to ``enqueue_db_event``.

Scope of R2 PR #4 :
    - account_snaps      (from MarketDataEngine portfolio_payload)
    - vol_surfaces       (from VolEngine result)
    - signals            (from VolEngine pillar_rows)
    - positions          (from RiskEngine open_positions, INSERT-only via ON CONFLICT)
    - position_snapshots (from RiskEngine open_positions, every cycle)

Not yet handled :
    - trades (OrderExecutor fills) — needs OrderExecutor.place_*
      to surface avgFillPrice and orderId in its return dict ; deferred
      to a follow-up that touches the order return contract.

Reference : releases/architecture_finale_project/08-postgresql.md
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

# Tags used to extract scalar USD fields from IB AccountSummary items.
# Each AccountValue has .tag, .currency, .value (all strings); we pick the
# first row where tag and currency match.
_ACCOUNT_USD_TAGS: dict[str, str] = {
    "net_liq_usd": "NetLiquidation",
    "buying_power_usd": "BuyingPower",
    "available_usd": "AvailableFunds",
    "unrealized_pnl_usd": "UnrealizedPnL",
    "realized_pnl_usd": "RealizedPnL",
    "gross_position_value_usd": "GrossPositionValue",
}


def _safe_decimal(value: Any) -> Decimal | None:
    """Coerce ``value`` to ``Decimal`` or return ``None`` on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _find_usd_value(summary: list, tag: str) -> Decimal | None:
    """Return the first summary item with ``tag`` and currency USD, as Decimal."""
    for item in summary or []:
        if getattr(item, "tag", None) == tag and getattr(item, "currency", None) == "USD":
            return _safe_decimal(getattr(item, "value", None))
    return None


def build_account_snap_row(
    summary: list | None,
    positions: list | None,
    cash_balances: dict[str, float] | None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Map an IB portfolio snapshot to an ``account_snaps`` row dict.

    ``cash_balances`` is pre-computed by ``Controller._extract_cash_balances``
    (currency → TotalCashBalance). We store it as-is in the ``currencies``
    JSONB column and also derive ``cash_usd`` from it for the scalar column.
    """
    cash_balances = cash_balances or {}
    ts = timestamp or datetime.now(UTC)
    row: dict[str, Any] = {
        "timestamp": ts,
        "currencies": dict(cash_balances),
        "open_positions_count": len(positions or []),
        "cash_usd": _safe_decimal(cash_balances.get("USD")),
    }
    for column, tag in _ACCOUNT_USD_TAGS.items():
        row[column] = _find_usd_value(summary or [], tag)
    return row


def build_vol_surface_row(
    vol_result: dict,
    underlying: str,
    spot: float | None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Map a VolEngine result to a ``vol_surfaces`` row dict.

    ``vol_result["spot"]`` is the forward price (F) in the current engine;
    the true spot mid price comes from the caller via ``spot`` (typically
    the latest bid/ask midpoint held by the Controller). If ``spot`` is
    not available we fall back to the forward, which is better than raising
    — the ``spot`` column is NOT NULL on the schema.
    """
    ts = timestamp or datetime.now(UTC)
    pillar_rows = vol_result.get("pillar_rows") or []
    forward = _safe_decimal(vol_result.get("spot"))

    spot_decimal = _safe_decimal(spot) if spot is not None else None
    if spot_decimal is None:
        spot_decimal = forward  # last-resort fallback, see docstring

    surface_data = {p.get("tenor_label", f"idx_{i}"): p for i, p in enumerate(pillar_rows)}
    fair_vol_data = {
        p.get("tenor_label"): p.get("sigma_fair_pct")
        for p in pillar_rows
        if p.get("tenor_label") and p.get("sigma_fair_pct") is not None
    }
    rv_data = {
        p.get("tenor_label"): p.get("RV_pct")
        for p in pillar_rows
        if p.get("tenor_label") and p.get("RV_pct") is not None
    }

    return {
        "timestamp": ts,
        "underlying": underlying,
        "spot": spot_decimal,
        "forward": forward,
        "surface_data": surface_data,
        "fair_vol_data": fair_vol_data or None,
        "rv_data": rv_data or None,
    }


def build_signal_rows(
    vol_result: dict,
    underlying: str,
    timestamp: datetime | None = None,
) -> list[dict[str, Any]]:
    """Map each enriched pillar to a ``signals`` row.

    Only pillars that have BOTH ``sigma_ATM_pct`` AND ``sigma_fair_pct``
    produce a row — the others are partial data (scan in progress, missing
    IV, etc.) and writing them would dirty the dataset.
    """
    ts = timestamp or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for p in vol_result.get("pillar_rows") or []:
        sigma_mid = _safe_decimal(p.get("sigma_ATM_pct"))
        sigma_fair = _safe_decimal(p.get("sigma_fair_pct"))
        if sigma_mid is None or sigma_fair is None:
            continue
        signal_type = p.get("signal")
        if signal_type not in {"CHEAP", "EXPENSIVE", "FAIR"}:
            continue
        rows.append({
            "timestamp": ts,
            "underlying": underlying,
            "tenor": str(p.get("tenor_label", ""))[:5],
            "dte": int(p.get("dte", 0)),
            "sigma_mid": sigma_mid,
            "sigma_fair": sigma_fair,
            "ecart": _safe_decimal(p.get("ecart_pct")) or Decimal("0"),
            "signal_type": signal_type,
            "rv": _safe_decimal(p.get("RV_pct")),
        })
    return rows


# --- positions + position_snapshots ----------------------------------------


_SEC_TYPE_TO_INSTRUMENT: dict[str, str] = {
    "FUT": "FUTURE",
    "FOP": "OPTION",
    "CASH": "SPOT",
}

_RIGHT_TO_OPTION_TYPE: dict[str, str] = {
    "C": "CALL",
    "P": "PUT",
    "CALL": "CALL",
    "PUT": "PUT",
}


def compute_position_id(
    symbol: str,
    side: str,
    instrument_type: str,
    strike: Decimal | None,
    maturity: date | None,
    option_type: str | None,
) -> int:
    """Deterministic 31-bit positions.id from the IB composite key.

    Two goals :
      - fits in a Postgres INTEGER column (31 bits, always non-negative)
      - same key on re-observation → same id, so ON CONFLICT DO NOTHING
        on positions.id lets the first sighting win and later cycles stay
        idempotent. position_snapshots and trades can reference the same
        id without a round-trip to read the DB-generated PK.

    Collision risk is negligible for realistic portfolios (birthday
    paradox : 1% at ~6500 distinct positions with a 31-bit space).
    If a real collision ever occurs, the second position silently shares
    rows with the first — documented limitation, acceptable for dev and
    a small PA book.
    """
    key = f"{symbol}|{side}|{instrument_type}|{strike or ''}|{maturity or ''}|{option_type or ''}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _parse_strike(raw: Any) -> Decimal | None:
    """Parse a strike coming from the UI row (string or number, '—' for none)."""
    if raw is None or raw in ("", "—"):
        return None
    try:
        return Decimal(str(raw).replace(",", ""))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _parse_expiry(raw: Any) -> date | None:
    """Parse an IB expiry string ('YYYYMMDD') into a date."""
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _open_position_common(
    open_position: dict,
) -> tuple[str, str, str, Decimal | None, date | None, str | None] | None:
    """Extract (symbol, side, instrument_type, strike, maturity, option_type).

    Returns ``None`` if any required field is missing — caller skips the row.
    """
    symbol = str(open_position.get("symbol") or "").strip().upper()
    side = str(open_position.get("side") or "").strip().upper()
    sec_type = str(open_position.get("sec_type") or "").strip().upper()
    instrument_type = _SEC_TYPE_TO_INSTRUMENT.get(sec_type)
    if not symbol or side not in {"BUY", "SELL"} or instrument_type is None:
        return None
    strike = _parse_strike(open_position.get("strike"))
    maturity = _parse_expiry(open_position.get("expiry"))
    right_raw = str(open_position.get("right") or "").strip().upper()
    option_type = _RIGHT_TO_OPTION_TYPE.get(right_raw) if instrument_type == "OPTION" else None
    return symbol, side, instrument_type, strike, maturity, option_type


def build_position_row(
    open_position: dict,
    timestamp: datetime | None = None,
) -> dict[str, Any] | None:
    """Map an open_position row (RiskEngine) to a ``positions`` table row.

    Returns ``None`` when required fields are missing — caller skips the
    enqueue. The row is written with ON CONFLICT (id) DO NOTHING (see
    writer.IDEMPOTENT_TABLES) so only the first observation wins. The
    ``entry_timestamp`` column is therefore "when we first saw the
    position", not the actual IB fill time ; IB does not expose fill
    timestamps in reqPositions() output.
    """
    common = _open_position_common(open_position)
    if common is None:
        return None
    symbol, side, instrument_type, strike, maturity, option_type = common

    qty = _safe_decimal(open_position.get("qty"))
    entry_price = _safe_decimal(open_position.get("fill_price"))
    if qty is None or entry_price is None:
        return None

    ts = timestamp or datetime.now(UTC)
    return {
        "id": compute_position_id(symbol, side, instrument_type, strike, maturity, option_type),
        "symbol": symbol,
        "instrument_type": instrument_type,
        "side": side,
        "quantity": qty,
        "strike": strike,
        "maturity": maturity,
        "option_type": option_type,
        "entry_price": entry_price,
        "entry_timestamp": ts,
        "status": "OPEN",
    }


def build_position_snapshot_row(
    open_position: dict,
    spot: float | None,
    timestamp: datetime | None = None,
) -> dict[str, Any] | None:
    """Map an open_position row to a ``position_snapshots`` row.

    ``spot`` comes from the RiskEngine result (top-level ``spot`` key).
    If the greek fields are None (e.g., market closed), we still write
    the row with NULLs so that an analytics query can tell the difference
    between "no snapshot ever" and "snapshot taken, no greeks available".
    """
    common = _open_position_common(open_position)
    if common is None:
        return None
    symbol, side, instrument_type, strike, maturity, option_type = common

    position_id = compute_position_id(symbol, side, instrument_type, strike, maturity, option_type)
    ts = timestamp or datetime.now(UTC)
    return {
        "position_id": position_id,
        "timestamp": ts,
        "spot": _safe_decimal(spot),
        "iv": _safe_decimal(open_position.get("iv_now_pct")),
        "delta_usd": _safe_decimal(open_position.get("delta")),
        "vega_usd": _safe_decimal(open_position.get("vega")),
        "gamma_usd": _safe_decimal(open_position.get("gamma")),
        "theta_usd": _safe_decimal(open_position.get("theta")),
        "pnl_usd": _safe_decimal(open_position.get("pnl")),
    }
