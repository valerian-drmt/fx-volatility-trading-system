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
    {"name": "execution",    "hb": "heartbeat:execution",    "out": None,                          "stale_s": 10},
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
    except (TimeoutError, OSError) as e:
        return {"status": "DOWN", "host": host, "port": port, "error": str(e)[:80]}


async def _tcp_probe(host: str, port: int, timeout_s: float = 2.0) -> str:
    """Generic TCP probe → 'OK' / 'DOWN'."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout_s)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return "OK"
    except Exception:
        return "DOWN"


# Static metadata for the 10 containers de la stack — known at compose time.
# `image` est le tag attendu, `layer` groupe la viz, `desc` est un one-liner.
STACK_LAYOUT: list[dict[str, Any]] = [
    {"name": "frontend",    "image": "fx-options-frontend:local",   "layer": "edge",    "desc": "React SPA (nginx static)"},
    {"name": "nginx",       "image": "nginx:alpine",                "layer": "edge",    "desc": "Reverse proxy (80/443)"},
    {"name": "api",         "image": "fx-options-api:local",        "layer": "app",     "desc": "FastAPI REST + WS bridge"},
    {"name": "redis",       "image": "redis:7-alpine",              "layer": "data",    "desc": "Pub/sub + cache"},
    {"name": "postgres",    "image": "postgres:16-alpine",          "layer": "data",    "desc": "Persistence (12 tables)"},
    {"name": "ib-gateway",  "image": "ghcr.io/gnzsnz/ib-gateway",   "layer": "external","desc": "IB API gateway (4002)"},
    {"name": "market-data", "image": "fx-options-market-data:local","layer": "engines", "desc": "Tick stream → Redis"},
    {"name": "vol-engine",  "image": "fx-options-vol-engine:local", "layer": "engines", "desc": "SVI/SSVI fit + signals"},
    {"name": "risk-engine", "image": "fx-options-risk-engine:local","layer": "engines", "desc": "Greeks + P&L curve"},
    {"name": "db-writer",   "image": "fx-options-db-writer:local",  "layer": "engines", "desc": "Redis events → Postgres"},
    {"name": "execution",   "image": "fx-options-execution:local",  "layer": "engines", "desc": "Orders/positions IB → DB (1s)"},
]


@router.get("/stack")
async def stack_overview(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Aggregate status pour les 10 containers, dérivé de probes + heartbeats.

    On ne lit pas le Docker socket (api n'a pas accès, et c'est mieux comme ça).
    Statuses dérivés :
      - postgres / redis / ib-gateway   : TCP probe ou ping
      - frontend                        : HTTP probe http://frontend:8080/
      - nginx / api                     : implicite (la requête arrive via eux)
      - 4 engines                       : heartbeat Redis + age vs threshold
    """
    now = datetime.now(UTC)

    # 1. Probes data sources
    redis_ok = "DOWN"
    try:
        redis_ok = "OK" if await redis.ping() else "DOWN"
    except Exception:
        redis_ok = "DOWN"

    pg_status = await _tcp_probe("postgres", 5432)
    ib_status = (await _ib_probe()).get("status", "DOWN")
    fe_status = await _tcp_probe("frontend", 8080)
    exec_status = await _tcp_probe("execution-engine", 8001)

    # 2. Engines : reuse the per-engine config from /engines
    engines_status: dict[str, str] = {}
    for cfg in ENGINES_CONFIG:
        hb_age = await _key_age(redis, cfg["hb"], now)
        if hb_age is None:
            engines_status[cfg["name"]] = "DOWN"
        elif hb_age < cfg["stale_s"]:
            engines_status[cfg["name"]] = "OK"
        else:
            engines_status[cfg["name"]] = "STALE"

    # 3. Compose final status par container
    container_status = {
        "frontend":   fe_status,
        "nginx":      "OK",                  # implicite
        "api":        "OK",                  # implicite (we're in it)
        "redis":      redis_ok,
        "postgres":   pg_status,
        "ib-gateway": ib_status,
        "market-data": engines_status.get("market_data", "DOWN"),
        "vol-engine":  engines_status.get("vol_engine", "DOWN"),
        "risk-engine": engines_status.get("risk_engine", "DOWN"),
        "db-writer":   engines_status.get("db_writer", "DOWN"),
        "execution":  exec_status,
    }

    out = []
    for entry in STACK_LAYOUT:
        out.append({**entry, "status": container_status[entry["name"]]})

    # Edges = relations cf. docs/container_deps.md (A → B = B dépend de A).
    edges = [
        {"from": "postgres",    "to": "api"},
        {"from": "redis",       "to": "api"},
        {"from": "postgres",    "to": "db-writer"},
        {"from": "redis",       "to": "db-writer"},
        {"from": "redis",       "to": "market-data"},
        {"from": "ib-gateway",  "to": "market-data"},
        {"from": "redis",       "to": "vol-engine"},
        {"from": "ib-gateway",  "to": "vol-engine"},
        {"from": "redis",       "to": "risk-engine"},
        {"from": "ib-gateway",  "to": "risk-engine"},
        {"from": "api",         "to": "nginx"},
        {"from": "frontend",    "to": "nginx"},
    ]

    return {
        "containers": out,
        "edges": edges,
        "timestamp": now.isoformat().replace("+00:00", "Z"),
    }


# --- DB Explorer ------------------------------------------------------------

# Whitelist hardcodée. Évite SQL injection : le nom est validé par membership
# dans cette liste avant interpolation. Pas d'autres requêtes que SELECT.
# Value = colonne pour ORDER BY DESC (PK la plupart du temps, mais pas toujours
# `id` — vol_config utilise `version`).
ALLOWED_TABLES: dict[str, str] = {
    "order_events": "id",
    "orders": "id",
    "trades": "id",
    "positions": "id",
    "position_snapshots": "id",
    "account_snaps": "id",
    "vol_surface_snapshot": "id",
    "vol_engine_config": "version",
    # Step 1 — regime gating
    "regime_feature_snapshot": "id",
    "feature_history_30d": "id",
    "macro_event": "id",
    "vrp_default_curve": "id",
    # Step 2 — PCA factor model
    "surface_snapshots_hourly": "id",
    "pca_model": "id",
    "pca_projection_snapshot": "id",
    "pca_structure_recommendation": "id",
}


@router.get("/tables")
async def list_tables() -> dict[str, Any]:
    """Return the static whitelist of tables that DB Explorer can read."""
    return {"tables": list(ALLOWED_TABLES.keys())}


@router.get("/tables/{name}")
async def read_table(
    name: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Read N rows from `name` (whitelisted), ordered DESC by its PK column.

    JSONB / datetime / Decimal sont sérialisés via FastAPI's jsonable_encoder.
    """
    order_col = ALLOWED_TABLES.get(name)
    if order_col is None:
        raise HTTPException(status_code=404, detail=f"table {name!r} not in whitelist")
    if not 1 <= limit <= 10000:
        raise HTTPException(status_code=400, detail="limit must be in [1, 10000]")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    # `name` et `order_col` sont validés via la whitelist (pas user input
    # libre), donc l'interpolation est safe.
    rows_res = await db.execute(
        text(f"SELECT * FROM {name} ORDER BY {order_col} DESC LIMIT :lim OFFSET :off"),
        {"lim": limit, "off": offset},
    )
    rows = [dict(r) for r in rows_res.mappings().all()]

    count_res = await db.execute(text(f"SELECT COUNT(*) AS n FROM {name}"))
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


# --- Cycle progress ---------------------------------------------------------
# The vol-engine writes per-stage / per-task progress to the Redis hash
# ``cycle_progress:vol_engine`` as the cycle walks through its 5 pipelines.
# This endpoint surfaces that hash so the dev panel can render real progress
# (vs the time-based fake we used pre-instrumentation).

@router.get("/cycle-progress")
async def cycle_progress(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Return the vol-engine's current cycle stage + task and the list of
    completed (stage, task) pairs in this cycle."""
    raw = await redis.hgetall("cycle_progress:vol_engine")
    if not raw:
        return {
            "cycle_started_at": None,
            "stage": None,
            "task": None,
            "completed": [],
        }
    decoded = {
        (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    completed_csv = decoded.get("completed", "")
    completed_list = [s for s in completed_csv.split(",") if s]
    return {
        "cycle_started_at": decoded.get("cycle_started_at") or None,
        "stage": decoded.get("stage") or None,
        "task": decoded.get("task") or None,
        "completed": completed_list,
    }
