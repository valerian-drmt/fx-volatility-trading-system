"""Build DB row payloads from engine outputs.

Pure transformations: engine-native dict → DB table dict ready to enqueue.
No state, no IO — all testable in isolation. The Controller wires these
into its result-handling callbacks (``_on_market_data_payload``,
``_on_vol_result``) and passes the output to ``enqueue_db_event``.

Scope of R2 PR #4 :
    - account_snaps   (from MarketDataEngine portfolio_payload)
    - vol_surfaces    (from VolEngine result)
    - signals         (from VolEngine pillar_rows)

Not yet handled (needs IB-position → DB-position_id mapping layer,
planned for a follow-up PR) :
    - position_snapshots (RiskEngine result)
    - trades, positions  (OrderExecutor fills)

Reference : releases/architecture_finale_project/08-postgresql.md
"""

from __future__ import annotations

from datetime import UTC, datetime
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
