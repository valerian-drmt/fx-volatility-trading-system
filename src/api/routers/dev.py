"""Dev console endpoints — read-only inspection of Redis (and later DB, engines).

R9 sandbox spike. **NOT prod-ready** : no auth, hardcoded key whitelist, etc.
A feature flag (`VITE_DEV_TABS=false` côté frontend, allow-list `/dev/*` bloqué
côté nginx) sera ajoutée avant le déploiement EC2.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from redis import asyncio as aioredis

from api.dependencies import get_redis

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])

# Whitelist : seules ces clés sont inspectables. Évite que /dev/redis/value
# soit utilisé pour scanner Redis arbitrairement (clé sensible, scan KEYS *).
KNOWN_KEYS: list[str] = [
    "heartbeat:market_data",
    "heartbeat:vol_engine",
    "heartbeat:risk_engine",
    "heartbeat:db_writer",
    "latest_spot:EURUSD",
    "latest_vol_surface:EURUSD",
    "latest_signals:EURUSD",
    "latest_greeks:portfolio",
    "latest_pnl_curve",
]


@router.get("/redis/keys")
async def redis_keys(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Return the whitelist of known keys with TTL + age (for heartbeats)."""
    out: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for key in KNOWN_KEYS:
        ttl = await redis.ttl(key)  # -2 = missing, -1 = no expire
        exists = ttl != -2
        age_s: float | None = None
        if exists and key.startswith("heartbeat:"):
            raw = await redis.get(key)
            if raw is not None:
                try:
                    raw_str = raw.decode() if isinstance(raw, bytes) else raw
                    ts = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
                    age_s = round((now - ts).total_seconds(), 2)
                except (ValueError, AttributeError):
                    pass
        out.append({
            "key": key,
            "exists": exists,
            "ttl": ttl if exists else None,
            "age_s": age_s,
        })
    return {"keys": out}


@router.get("/redis/value")
async def redis_value(
    key: str,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """GET a single key's value. Parses JSON if applicable."""
    if key not in KNOWN_KEYS:
        raise HTTPException(status_code=404, detail=f"key {key!r} not in whitelist")
    raw = await redis.get(key)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"key {key!r} not present")
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        parsed = json.loads(text)
        return {"key": key, "value": parsed, "raw": text, "is_json": True}
    except json.JSONDecodeError:
        return {"key": key, "value": text, "raw": text, "is_json": False}
