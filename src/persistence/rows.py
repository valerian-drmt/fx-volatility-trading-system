"""Row serialisers shared by the REST readers and the Redis publishers (#366).

One function per table row, so a WebSocket message and the REST endpoint that
backs it carry the same keys and the desk can patch a row in place from either
source. ``api.routers.positions.list_open`` and ``api.routers.trade.
list_spot_orders`` call these; ``engines.execution`` publishes them on the
``positions``, ``account`` and ``spot_orders`` channels. The fixtures under
``tests/fixtures/channels/`` lock the key sets.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from core.vol.tenors import tenor_bucket_from_expiry
from persistence.models import AccountHistory, OpenPosition, SpotOrder


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


def open_position_row(r: OpenPosition, today: date | None = None) -> dict[str, Any]:
    """One ``open_position`` row, the shape of ``GET /positions/open``.

    Column order : identity / grouping, spec, P&L, main greeks, secondary
    greeks, metadata. ``tenor`` is the entry label and never rolls;
    ``tenor_now`` is the 1M..6M bucket by days to expiry, the same function the
    P&L attribution by tenor uses (#377)."""
    today = today or datetime.now(UTC).date()
    return {
        "id": r.id,
        "package_id": r.package_id,
        "trade_id": r.trade_id,
        "contract_id": r.contract_id,
        "product_label": r.product_label,
        "structure": r.structure,
        "side": r.side,
        "quantity": _f(r.quantity),
        "tenor": r.tenor,
        "tenor_now": tenor_bucket_from_expiry(r.expiry, today),
        "expiry": _iso(r.expiry),
        "current_pnl_usd": _f(r.current_pnl_usd),
        "market_price": _f(r.market_price),
        "contract_price_entry": _f(r.contract_price_entry),
        "nominal_eur": _f(r.nominal_eur),
        "delta_usd": _f(r.delta_usd),
        "gamma_usd": _f(r.gamma_usd),
        "vega_usd": _f(r.vega_usd),
        "theta_usd": _f(r.theta_usd),
        "iv": _f(r.iv),
        "vanna_usd": _f(r.vanna_usd),
        "volga_usd": _f(r.volga_usd),
        "timestamp": _iso(r.timestamp),
        "entry_timestamp": _iso(r.entry_timestamp),
    }


def account_snapshot_row(s: AccountHistory | None) -> dict[str, Any] | None:
    """One ``account_history`` row: ``latest`` / ``prev_24h`` of
    ``GET /portfolio/account`` and the body of the ``account`` channel."""
    if s is None:
        return None
    return {
        "timestamp": _iso(s.timestamp),
        "net_liq_usd": _f(s.net_liq_usd),
        "cash_usd": _f(s.cash_usd),
        "unrealized_pnl_usd": _f(s.unrealized_pnl_usd),
        "gross_position_value": _f(s.gross_position_value),
        "init_margin_req": _f(s.init_margin_req),
        "maint_margin_req": _f(s.maint_margin_req),
        "excess_liquidity": _f(s.excess_liquidity),
        "cushion": _f(s.cushion),
        "open_positions_count": s.open_positions_count,
        "currencies": s.currencies or {},
    }


def spot_order_row(o: SpotOrder) -> dict[str, Any]:
    """One ``spot_order`` row, the shape of ``GET /trade/spot-orders`` and of
    the ``spot_orders`` channel. Datetimes stay datetimes here (FastAPI and
    ``bus.publisher._dumps`` both render them ISO-8601)."""
    return {
        "id": int(o.id),
        "created_at": o.created_at,
        "symbol": o.symbol, "currency": o.currency, "exchange": o.exchange,
        "local_symbol": o.ib_local_symbol or f"{o.symbol}.{o.currency}",
        "side": o.side,
        "qty": int(o.qty or 0),
        "qty_filled": int(o.qty_filled or 0),
        "order_type": o.order_type,
        "limit_price": o.limit_price,
        "avg_fill_price": o.avg_fill_price,
        "commission_usd": o.commission_usd,
        "state": o.state,
        "state_updated_at": o.state_updated_at,
        "filled_at": o.filled_at,
        "rejection_text": o.rejection_text,
        # IB orderId : the handle the auth-gated DELETE /orders/{id} cancels.
        "ib_order_id": int(o.ib_order_id) if (o.ib_order_id or "").isdigit() else None,
        "ib_perm_id": o.ib_perm_id,
        # Raw IB status (PreSubmitted / Submitted / ...) + whyHeld: why a row waits (#376).
        "ib_status": o.ib_status,
        "why_held": o.why_held,
    }
