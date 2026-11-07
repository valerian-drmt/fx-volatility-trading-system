"""Redis pub/sub channel names.

Channels are flat strings — no template, no per-symbol namespacing.
Subscribers interested in a specific symbol filter on the payload.

Reference : releases/architecture_finale_project/09-redis.md
"""

from __future__ import annotations

# Market Data Engine publishes tick updates here (throttled ~200ms).
# Subscribers : FastAPI WebSocket bridge.
CH_TICKS: str = "ticks"

# Market Data Engine publishes the account snapshot every 10s.
CH_ACCOUNT: str = "account"

# Vol Engine publishes at the end of each scan (~3 min).
CH_VOL_UPDATE: str = "vol_update"

# Risk Engine publishes at the end of each cycle (~60s).
CH_RISK_UPDATE: str = "risk_update"

# Any engine publishes errors/warnings worth surfacing in the UI.
CH_SYSTEM_ALERTS: str = "system_alerts"

# Admin endpoint publishes the new version number (as string) whenever
# /api/v1/admin/config accepts a PUT or POST revert. Engines subscribe
# to hot-reload their Pydantic config without a restart.
CH_CONFIG_CHANGED: str = "config:changed"

# STEP4 — execution-engine publishes per-structure order events
# (acknowledged / filled / rejected / cancelled / unwind_created). Channel
# name is dynamic : ``orders:<structure_id>``. The Redis-WS bridge uses a
# pattern subscription (``orders:*``) and forwards to ConnectionManager
# keyed on the full channel name ; clients of ``/ws/orders/{structure_id}``
# only see their own structure.
CH_ORDERS_PATTERN: str = "orders:*"


def orders_channel(structure_id: int) -> str:
    """Build the per-structure channel name."""
    return f"orders:{structure_id}"


# STEP5 — position_monitor publishes one MTM snapshot per cycle.
CH_POSITIONS: str = "positions"

# STEP5 — position_monitor publishes one row per ExitAlert that fires.
CH_EXIT_ALERTS: str = "exit_alerts"

# #366 — execution-engine publishes one message per spot (CASH) order change,
# same row shape as ``GET /trade/spot-orders``. The ``positions`` channel also
# carries the execution-engine's ``positions_changed`` event (rows upserted /
# closed by a position sync, same shape as ``GET /positions/open``) and the
# ``account`` channel its ``account_snapshot`` event (``latest`` of
# ``GET /portfolio/account``). Messages carry ``event`` + ``source``.
CH_SPOT_ORDERS: str = "spot_orders"

# Engines → db-writer: one ``{"table", "payload"}`` frame per row to persist
# (``shared.db_events.publish_db_event``). Pub/sub, fire and forget.
CH_DB_EVENTS: str = "db_events"
