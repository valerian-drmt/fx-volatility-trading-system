"""Dev console endpoints — read-only inspection of Redis, engines, IB.

R9 sandbox spike. **NOT prod-ready** : no auth, hardcoded key whitelist, etc.
A feature flag (`VITE_DEV_TABS=false` côté frontend, allow-list `/dev/*` bloqué
côté nginx) sera ajoutée avant le déploiement EC2.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from redis import asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session, get_redis

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


def _parse_age(raw_str: str, now: datetime) -> float | None:
    """Try to extract a timestamp from `raw_str` (ISO bare or JSON with `timestamp`)
    and compute age vs `now`. Returns None if neither shape applies.
    """
    # Heartbeat keys store a bare ISO-8601 string.
    try:
        ts = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
        return round((now - ts).total_seconds(), 2)
    except ValueError:
        pass
    # latest_* keys store JSON with a top-level `timestamp` field.
    try:
        payload = json.loads(raw_str)
        if isinstance(payload, dict):
            ts_str = payload.get("timestamp")
            if isinstance(ts_str, str):
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                return round((now - ts).total_seconds(), 2)
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass
    return None


@router.get("/redis/keys")
async def redis_keys(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Return the whitelist of known keys with TTL + age (parsed from value)."""
    out: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for key in KNOWN_KEYS:
        ttl = await redis.ttl(key)  # -2 = missing, -1 = no expire
        exists = ttl != -2
        age_s: float | None = None
        if exists:
            raw = await redis.get(key)
            if raw is not None:
                raw_str = raw.decode() if isinstance(raw, bytes) else raw
                age_s = _parse_age(raw_str, now)
        out.append({
            "key": key,
            "exists": exists,
            "ttl": ttl if exists else None,
            "age_s": age_s,
        })
    return {"keys": out}


# Per-engine config : (heartbeat key, primary output key, stale threshold sec).
# Threshold matche le compose healthcheck (max age avant unhealthy).
ENGINES_CONFIG: list[dict[str, Any]] = [
    {"name": "market_data",  "hb": "heartbeat:market_data",  "out": "latest_spot:EURUSD",          "stale_s": 60},
    {"name": "vol_engine",   "hb": "heartbeat:vol_engine",   "out": "latest_vol_surface:EURUSD",   "stale_s": 300},
    {"name": "risk_engine",  "hb": "heartbeat:risk_engine",  "out": "latest_greeks:portfolio",     "stale_s": 30},
    {"name": "db_writer",    "hb": "heartbeat:db_writer",    "out": None,                          "stale_s": 30},
]


@router.get("/engines")
async def engines_status(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Aggregate health for each of the 4 engines + IB Gateway TCP probe."""
    now = datetime.now(UTC)
    engines_out: list[dict[str, Any]] = []
    for cfg in ENGINES_CONFIG:
        hb_age = await _key_age(redis, cfg["hb"], now)
        hb_ttl = await redis.ttl(cfg["hb"])
        out_age = await _key_age(redis, cfg["out"], now) if cfg["out"] else None
        # status : OK si HB existe et < stale_s, STALE si trop vieux, DOWN sinon
        if hb_age is None:
            status = "DOWN"
        elif hb_age < cfg["stale_s"]:
            status = "OK"
        else:
            status = "STALE"
        engines_out.append({
            "name": cfg["name"],
            "status": status,
            "hb_age_s": hb_age,
            "hb_ttl_s": hb_ttl if hb_ttl != -2 else None,
            "stale_threshold_s": cfg["stale_s"],
            "out_key": cfg["out"],
            "out_age_s": out_age,
        })

    ib = await _ib_probe()
    return {"engines": engines_out, "ib_gateway": ib, "timestamp": now.isoformat().replace("+00:00", "Z")}


async def _key_age(redis: aioredis.Redis, key: str, now: datetime) -> float | None:
    raw = await redis.get(key)
    if raw is None:
        return None
    raw_str = raw.decode() if isinstance(raw, bytes) else raw
    return _parse_age(raw_str, now)


async def _ib_probe(host: str = "ib-gateway", port: int = 4002, timeout_s: float = 2.0) -> dict[str, Any]:
    """TCP probe sur le port API IB Gateway. Same intent que socat dans le compose
    healthcheck : OK si TCP connect, DOWN sinon. Pas de handshake API ici.
    """
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_s)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return {"status": "OK", "host": host, "port": port}
    except (TimeoutError, OSError, asyncio.TimeoutError) as e:
        return {"status": "DOWN", "host": host, "port": port, "error": str(e)[:80]}


# --- DB Explorer ------------------------------------------------------------

# Whitelist hardcodée. Évite SQL injection : le nom est validé par membership
# dans cette liste avant interpolation. Pas d'autres requêtes que SELECT.
ALLOWED_TABLES: tuple[str, ...] = (
    "positions",
    "position_snapshots",
    "trades",
    "account_snaps",
    "vol_surfaces",
    "signals",
    "svi_params",
    "ssvi_params",
    "backtest_runs",
    "vol_config",
)


@router.get("/tables")
async def list_tables() -> dict[str, Any]:
    """Return the static whitelist of tables that DB Explorer can read."""
    return {"tables": list(ALLOWED_TABLES)}


@router.get("/tables/{name}")
async def read_table(
    name: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Read N rows from `name` (whitelisted), ordered by id DESC.

    JSONB / datetime / Decimal sont sérialisés via FastAPI's jsonable_encoder.
    """
    if name not in ALLOWED_TABLES:
        raise HTTPException(status_code=404, detail=f"table {name!r} not in whitelist")
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400, detail="limit must be in [1, 1000]")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    # Tous les modèles ORM ont une PK `id`. Si une table le perd un jour, le
    # SELECT échouera explicitement — facile à diagnostiquer.
    rows_res = await db.execute(
        text(f"SELECT * FROM {name} ORDER BY id DESC LIMIT :lim OFFSET :off"),  # noqa: S608
        {"lim": limit, "off": offset},
    )
    rows = [dict(r) for r in rows_res.mappings().all()]

    count_res = await db.execute(text(f"SELECT COUNT(*) AS n FROM {name}"))  # noqa: S608
    total = int(count_res.scalar_one())

    columns = list(rows[0].keys()) if rows else []
    return {
        "table": name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "columns": columns,
        "rows": jsonable_encoder(rows),
    }


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
