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
