"""Dev console endpoints — read-only inspection of Redis, engines, IB,
DB schema, alembic migrations, Loki logs.

Auth boundary: the whole router requires a valid write-auth cookie
(``Depends(require_write)`` at router level), so the console is usable in
prod by the logged-in operator only. nginx additionally 404s the prefix
publicly as defense-in-depth (``infrastructure/nginx/nginx.conf``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from redis import asyncio as aioredis
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.dependencies import get_db_session, get_redis
from persistence.models import Base

router = APIRouter(
    prefix="/api/v1/dev",
    tags=["dev"],
    dependencies=[Depends(require_write)],
)

# Resolved at import time : versions/ lives next to this router via the
# PyPA src layout. Used by the alembic migrations inspector.
MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "persistence" / "migrations" / "versions"
)

# Whitelist: only these keys are inspectable. Prevents /dev/redis/value
# from being used to scan Redis arbitrarily (sensitive keys, KEYS * scans).
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
    # Bounded liveness ping — a downed Redis blocks on connect with no implicit
    # timeout, so short-circuit every engine to DOWN instead of hanging the probe.
    redis_up = await _redis_alive(redis)
    engines_out: list[dict[str, Any]] = []
    for cfg in ENGINES_CONFIG:
        if not redis_up:
            engines_out.append({
                "name": cfg["name"], "status": "DOWN", "hb_age_s": None,
                "hb_ttl_s": None, "stale_threshold_s": cfg["stale_s"],
                "out_key": cfg["out"], "out_age_s": None,
            })
            continue
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


async def _redis_alive(redis: aioredis.Redis, timeout_s: float = 2.0) -> bool:
    """Bounded Redis ping so a downed Redis can't hang the engines probe."""
    try:
        return bool(await asyncio.wait_for(redis.ping(), timeout=timeout_s))
    except Exception:
        return False


async def _key_age(redis: aioredis.Redis, key: str, now: datetime) -> float | None:
    raw = await redis.get(key)
    if raw is None:
        return None
    raw_str = raw.decode() if isinstance(raw, bytes) else raw
    return _parse_age(raw_str, now)


async def _ib_probe(host: str = "ib-gateway", port: int = 4002, timeout_s: float = 2.0) -> dict[str, Any]:
    """TCP probe on the IB Gateway API port. Same intent as the socat compose
    healthcheck: OK if TCP connects, DOWN otherwise. No API handshake here.
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


# Static metadata for the 17 containers de la stack — known at compose time.
# `image` est le tag attendu, `layer` groupe la viz, `desc` est un one-liner.
# Layer obs (6 containers) = profil docker compose ``obs`` : collectors
# (promtail, otel-collector), stores (prometheus, loki, tempo), UI (grafana).
STACK_LAYOUT: list[dict[str, Any]] = [
    {"name": "frontend",      "image": "fx-options-frontend:local",   "layer": "edge",    "desc": "React SPA (nginx static)"},
    {"name": "nginx",         "image": "nginx:alpine",                "layer": "edge",    "desc": "Reverse proxy (80/443)"},
    {"name": "api",           "image": "fx-options-api:local",        "layer": "app",     "desc": "FastAPI REST + WS bridge"},
    {"name": "redis",         "image": "redis:7-alpine",              "layer": "data",    "desc": "Pub/sub + cache"},
    {"name": "postgres",      "image": "postgres:16-alpine",          "layer": "data",    "desc": "Persistence (12 tables)"},
    {"name": "ib-gateway",    "image": "ghcr.io/gnzsnz/ib-gateway",   "layer": "external","desc": "IB API gateway (4002)"},
    {"name": "market-data",   "image": "fx-options-market-data:local","layer": "engines", "desc": "Tick stream → Redis"},
    {"name": "vol-engine",    "image": "fx-options-vol-engine:local", "layer": "engines", "desc": "SVI/SSVI fit + signals"},
    {"name": "risk-engine",   "image": "fx-options-risk-engine:local","layer": "engines", "desc": "Greeks + P&L curve"},
    {"name": "db-writer",     "image": "fx-options-db-writer:local",  "layer": "engines", "desc": "Redis events → Postgres"},
    {"name": "execution",     "image": "fx-options-execution:local",  "layer": "engines", "desc": "Orders/positions IB → DB (1s)"},
    # ─── Observability stack (profil obs) ───
    {"name": "promtail",      "image": "grafana/promtail",            "layer": "obs",     "desc": "Docker logs → Loki"},
    {"name": "otel-collector","image": "otel/opentelemetry-collector","layer": "obs",     "desc": "OTLP traces → Tempo"},
    {"name": "loki",          "image": "grafana/loki",                "layer": "obs",     "desc": "Logs store"},
    {"name": "prometheus",    "image": "prom/prometheus",             "layer": "obs",     "desc": "Metrics scrape + store"},
    {"name": "tempo",         "image": "grafana/tempo",               "layer": "obs",     "desc": "Traces store"},
    {"name": "grafana",       "image": "grafana/grafana-oss",         "layer": "obs",     "desc": "Dashboards UI (:3000)"},
]


@router.get("/stack")
async def stack_overview(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict[str, Any]:
    """Aggregate status for the 10 containers, derived from probes + heartbeats.

    We do not read the Docker socket (the api has no access, which is safer).
    Derived statuses:
      - postgres / redis / ib-gateway   : TCP probe or ping
      - frontend                        : HTTP probe http://frontend:8080/
      - nginx / api                     : implicit (the request arrives through them)
      - 4 engines                       : Redis heartbeat + age vs threshold
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

    # Observability stack — TCP probe on the canonical port of each
    # component when it exposes one. promtail / otel-collector have
    # no public TCP listener we can cheaply probe ; treat them as
    # presence-tracked at the docker level (fall back to "OK" when
    # the rest of the obs stack answers, "DOWN" otherwise).
    prom_status    = await _tcp_probe("prometheus",    9090)
    loki_status    = await _tcp_probe("loki",          3100)
    tempo_status   = await _tcp_probe("tempo",         3200)
    grafana_status = await _tcp_probe("grafana",       3000)
    obs_any_up = "OK" in (prom_status, loki_status, tempo_status, grafana_status)
    promtail_status = "OK" if obs_any_up else "DOWN"
    otel_status     = "OK" if obs_any_up else "DOWN"

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
        # Observability layer
        "promtail":      promtail_status,
        "otel-collector": otel_status,
        "loki":          loki_status,
        "prometheus":    prom_status,
        "tempo":         tempo_status,
        "grafana":       grafana_status,
    }

    out = []
    for entry in STACK_LAYOUT:
        out.append({**entry, "status": container_status[entry["name"]]})

    # Edges = relations cf. docs/container_deps.md (A → B = B depends on A).
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
        # Observability flows (kept minimal to avoid spaghetti)
        {"from": "promtail",       "to": "loki"},        # Docker logs → Loki
        {"from": "otel-collector", "to": "tempo"},       # OTLP traces → Tempo
        {"from": "loki",           "to": "grafana"},
        {"from": "prometheus",     "to": "grafana"},
        {"from": "tempo",          "to": "grafana"},
    ]

    return {
        "containers": out,
        "edges": edges,
        "timestamp": now.isoformat().replace("+00:00", "Z"),
    }


# --- DB Explorer ------------------------------------------------------------
#
# Tables are **auto-discovered** from ``Base.metadata.tables`` — adding an
# ORM class makes it queryable on the next API reload, no whitelist to
# maintain. Two safety layers protect against SQL injection :
#   1. ``name`` is validated against ``Base.metadata`` membership before
#      interpolation (set lookup, not free-form user input).
#   2. ``order_by`` / filter column names are validated against the
#      table's ``c.name`` set the same way ; filter VALUES go through
#      SQLAlchemy named-parameter binding (``:flt_0``, …) so they
#      never touch the SQL string.
# The endpoints only emit SELECT — no INSERT/UPDATE/DELETE here.


@router.get("/tables")
async def list_tables() -> dict[str, Any]:
    """Auto-discover every ORM-declared table with light metadata.

    Returns ``{ name, n_columns, pk: [col, ...] }`` per entry so the
    front-end can drive a search-as-you-type picker and surface the
    PK columns inline. The list always reflects the current
    ``Base.metadata`` — no out-of-band whitelist to maintain.
    """
    out: list[dict[str, Any]] = []
    for tname in sorted(Base.metadata.tables.keys()):
        t = Base.metadata.tables[tname]
        out.append({
            "name": tname,
            "n_columns": len(list(t.columns)),
            "pk": [c.name for c in t.primary_key.columns],
        })
    return {"tables": out}


# Per-table logical column order — overrides the physical Postgres
# column order returned by ``SELECT *``. Useful when migrations have
# appended columns in chronological order rather than logical order.
# Tables not in this map keep their physical order.
PREFERRED_COLUMN_ORDER: dict[str, list[str]] = {
    # Open positions : identity → spec → P&L → main greeks → secondary
    # greeks → metadata (cf. /api/v1/positions/open).
    "open_position": [
        "id", "package_id", "trade_id", "contract_id",
        "product_label", "structure", "side",
        "quantity", "tenor", "expiry",
        "current_pnl_usd", "market_price", "contract_price_entry", "nominal_eur",
        "delta_usd", "gamma_usd", "vega_usd", "theta_usd", "iv",
        "vanna_usd", "volga_usd",
        "timestamp", "entry_timestamp",
    ],
    "open_position_history": [
        # History adds position_id (FK) + timestamp upfront, no
        # entry_timestamp on history rows.
        "id", "position_id", "timestamp",
        "package_id", "trade_id", "contract_id",
        "product_label", "structure", "side",
        "quantity", "tenor", "expiry",
        "current_pnl_usd", "market_price", "contract_price_entry", "nominal_eur",
        "delta_usd", "gamma_usd", "vega_usd", "theta_usd", "iv",
        "vanna_usd", "volga_usd",
    ],
}


def _reorder_columns(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Re-emit ``row`` with the preferred column order for ``table``.
    Unknown / extra columns land at the end, in their original order.
    """
    preferred = PREFERRED_COLUMN_ORDER.get(table)
    if not preferred:
        return row
    out: dict[str, Any] = {}
    seen: set[str] = set()
    for c in preferred:
        if c in row:
            out[c] = row[c]
            seen.add(c)
    for c, v in row.items():
        if c not in seen:
            out[c] = v
    return out


@router.get("/tables/{name}")
async def read_table(
    name: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    order_dir: str = "desc",
    filters: str | None = None,
) -> dict[str, Any]:
    """Read N rows from ``name`` with optional sort / pagination / filter.

    Query params
    ------------
    - ``limit`` (1..10000, default 50)
    - ``offset`` (≥0, default 0)
    - ``order_by`` : column name. Defaults to the first PK column.
    - ``order_dir`` : ``asc`` | ``desc`` (default ``desc``).
    - ``filters`` : ``col:value`` pairs joined by ``,``. Exact-match.
      Example : ``filters=structure:butterfly,side:long``
      String columns can also use ``col:%substr%`` for ILIKE matching.

    Response
    --------
    Adds ``columns_meta`` (per-col type / nullable / pk / fk) so the
    front-end can render with type-aware formatting (right-align
    numbers, render JSONB as JSON, locale-format timestamps, badge
    booleans, etc.) without a second round-trip to ``/db-schema``.
    """
    if name not in Base.metadata.tables:
        raise HTTPException(
            status_code=404,
            detail=f"table {name!r} not in ORM metadata",
        )
    if not 1 <= limit <= 10000:
        raise HTTPException(status_code=400, detail="limit must be in [1, 10000]")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")
    if order_dir.lower() not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="order_dir must be asc/desc")

    from sqlalchemy.dialects import postgresql
    pg_dialect = postgresql.dialect()
    t = Base.metadata.tables[name]
    col_names = {c.name for c in t.columns}
    pks = [c.name for c in t.primary_key.columns]

    # Default ORDER BY = first PK column DESC (matches the old behavior
    # for every table that had ``id`` as PK in the legacy whitelist).
    if order_by is None:
        order_by = pks[0] if pks else next(iter(col_names))
    if order_by not in col_names:
        raise HTTPException(
            status_code=400,
            detail=f"order_by {order_by!r} is not a column of {name}",
        )

    # Parse filters : ``col:value`` pairs, comma-joined.
    #
    # Matching semantics (designed for a "search" UX, not "WHERE clause" UX):
    #
    #   - ``col:foo``     → ``col::text ILIKE '%foo%'``
    #                       case-insensitive substring match. Works for
    #                       string columns ("long" finds "Long") and for
    #                       numeric columns where the text cast adds
    #                       precision ("10" finds "10.000000").
    #
    #   - ``col:%foo_``   → ``col::text ILIKE '%foo_'``
    #                       user provided wildcards (``%`` or ``_``) — used
    #                       as-is, still case-insensitive.
    #
    #   - ``col:=foo``    → ``col::text = 'foo'``
    #                       leading ``=`` forces exact, case-sensitive
    #                       match. Use for IDs and FK lookups.
    #
    # The ``::text`` cast is mandatory because we don't know the column
    # type at parse time and ``WHERE int_col ILIKE '%10%'`` would fail
    # without it. PG's planner pushes the cast cleanly.
    #
    # Built with SQLAlchemy Core expressions — identifiers come from the
    # ORM metadata objects themselves and every value is a bound parameter,
    # so no request-derived string ever reaches raw SQL (CWE-089).
    conditions: list[Any] = []
    if filters:
        for pair in filters.split(","):
            if ":" not in pair:
                continue
            col, val = pair.split(":", 1)
            col = col.strip()
            val = val.strip()
            if not col or not val:
                continue
            if col not in col_names:
                raise HTTPException(
                    status_code=400,
                    detail=f"filter column {col!r} is not in {name}",
                )
            col_txt = cast(t.c[col], Text)
            if val.startswith("="):
                # Exact case-sensitive match.
                conditions.append(col_txt == val[1:])
            elif "%" in val or "_" in val:
                # Caller-supplied wildcards.
                conditions.append(col_txt.ilike(val))
            else:
                # Default : implicit substring match, case-insensitive.
                conditions.append(col_txt.ilike(f"%{val}%"))

    order_col = t.c[order_by]
    order_expr = order_col.desc() if order_dir.lower() == "desc" else order_col.asc()

    # Total count with same filter applied.
    count_stmt = select(func.count()).select_from(t).where(*conditions)
    total = int((await db.execute(count_stmt)).scalar_one())

    # Rows.
    rows_stmt = (
        select(t).where(*conditions).order_by(order_expr)
        .limit(limit).offset(offset)
    )
    rows_res = await db.execute(rows_stmt)
    rows = [_reorder_columns(name, dict(r)) for r in rows_res.mappings().all()]

    # Column metadata so the front-end can format types correctly.
    fk_col_names: set[str] = set()
    for fkc in t.foreign_key_constraints:
        for col in fkc.columns:
            fk_col_names.add(col.name)
    columns_meta: list[dict[str, Any]] = []
    for c in t.columns:
        try:
            sql_type = c.type.compile(dialect=pg_dialect)
        except Exception:
            sql_type = str(c.type)
        columns_meta.append({
            "name": c.name,
            "type": sql_type,
            "nullable": bool(c.nullable),
            "pk": c.name in set(pks),
            "fk": c.name in fk_col_names,
        })

    columns = list(rows[0].keys()) if rows else [c["name"] for c in columns_meta]
    return {
        "table": name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "order_dir": order_dir.lower(),
        "filters": filters or "",
        "columns": columns,
        "columns_meta": columns_meta,
        "rows": jsonable_encoder(rows),
    }


# ──────────────────────────────────────────────────────────────────────
# Log Search — Loki proxy
# ──────────────────────────────────────────────────────────────────────
# The obs profile bundles a Loki instance fed by promtail (Docker logs
# → Loki). Grafana Explore is the canonical UI but is 5-10 s + 6 clicks
# away for a "tail vol-engine for errors" question. This proxy gives the
# DB-Schema-style dev tab a 1-click answer.
#
# Endpoints :
#   GET /logs/containers   → distinct values of the ``container`` label
#   GET /logs/query        → query_range with a simplified API
#
# The container's API talks to Loki via the internal Docker network at
# ``http://loki:3100``. If the obs profile isn't running, every call
# raises a clean 503 — the front-end surfaces that with "Loki down".

LOKI_BASE = "http://loki:3100"


@router.get("/logs/containers")
async def logs_containers() -> dict[str, Any]:
    """Return the distinct values of the ``container`` Loki label."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as cli:
            r = await cli.get(f"{LOKI_BASE}/loki/api/v1/label/container/values")
            if r.status_code != 200:
                raise HTTPException(
                    status_code=503,
                    detail=f"loki returned {r.status_code}",
                )
            data = r.json()
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"loki unreachable : {type(e).__name__}",
        ) from e
    values = sorted(data.get("data", []))
    return {"containers": values}


@router.get("/logs/query")
async def logs_query(
    container: str | None = None,
    pattern: str | None = None,
    level: str | None = None,
    minutes: int = 15,
    limit: int = 200,
) -> dict[str, Any]:
    """Query Loki for log lines matching the given filters.

    Params
    ------
    - ``container`` : exact value of the Loki ``container`` label.
      Omitted → all containers.
    - ``pattern`` : free-text regex applied as a LogQL ``|~``. Case-
      insensitive by default (we prepend ``(?i)`` if there's no
      explicit (?-flag).
    - ``level`` : ERROR / WARNING / INFO / DEBUG. Matched against
      structlog's ``"level": "..."`` JSON field. Omitted → no level
      filter.
    - ``minutes`` : lookback window. Default 15.
    - ``limit`` : max lines returned (Loki cap is typically 5000).

    Response
    --------
    ``{ entries: [{ ts, container, message, labels }], total, query }``
    where ``ts`` is RFC3339 (browser-friendly) and ``message`` is the
    raw log line (often already structlog JSON — front-end pretty-prints
    if so).
    """
    import httpx
    if not 1 <= minutes <= 24 * 60:
        raise HTTPException(400, "minutes must be in [1, 1440]")
    if not 1 <= limit <= 5000:
        raise HTTPException(400, "limit must be in [1, 5000]")

    # Build LogQL.
    selectors: list[str] = []
    if container:
        # Quote-escape the container value defensively even though
        # we don't allow free text.
        safe = container.replace('"', '\\"')
        selectors.append(f'container="{safe}"')
    else:
        # Promtail tags every container with a non-empty ``container``
        # label, so this selector picks up everything the obs stack
        # collects.
        selectors.append('container=~".+"')

    query = "{" + ",".join(selectors) + "}"
    if pattern:
        safe_pat = pattern.replace("\\", "\\\\").replace('"', '\\"')
        flag = "" if "(?" in pattern else "(?i)"
        query += f' |~ "{flag}{safe_pat}"'
    if level:
        lvl = level.upper().replace('"', '')
        query += f' |~ "(?i)\\"level\\"[: ]+\\"{lvl}\\""'

    import time as _time
    end_ns = int(_time.time() * 1e9)
    start_ns = end_ns - minutes * 60 * 1_000_000_000

    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            r = await cli.get(
                f"{LOKI_BASE}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": str(limit),
                    "direction": "backward",
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"loki unreachable : {type(e).__name__}",
        ) from e
    if r.status_code != 200:
        raise HTTPException(
            status_code=503,
            detail=f"loki returned {r.status_code} : {r.text[:200]}",
        )

    payload = r.json()
    streams = payload.get("data", {}).get("result", [])
    entries: list[dict[str, Any]] = []
    for stream in streams:
        labels = stream.get("stream", {})
        cont = labels.get("container", "?")
        for ts_ns_str, line in stream.get("values", []):
            try:
                ts_ns = int(ts_ns_str)
            except (TypeError, ValueError):
                continue
            # Convert ns → ISO so the front-end can format with locale.
            ts_iso = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).isoformat()
            entries.append({
                "ts": ts_iso,
                "container": cont,
                "message": line,
                "labels": labels,
            })
    # Loki returns each stream sorted ; we want global desc order so
    # the newest line shows first.
    entries.sort(key=lambda e: e["ts"], reverse=True)
    entries = entries[:limit]
    return {
        "entries": entries,
        "total": len(entries),
        "query": query,
        "minutes": minutes,
    }


# ──────────────────────────────────────────────────────────────────────
# Alembic Migrations Inspector
# ──────────────────────────────────────────────────────────────────────
# The schema is versioned with alembic ; ``src/persistence/migrations/
# versions/*.py`` holds one file per revision with the standard
# ``revision`` / ``down_revision`` constants. We walk that directory
# (no DB hit), reconstruct the chain, then look up the current DB
# revision via ``SELECT version_num FROM alembic_version``. The front-
# end uses the result to flag which migrations are applied, which is
# CURRENT, and which are PENDING.
#
# This is the "is my prod schema up to date ?" question, answered in
# one click and one DB round-trip.



def _parse_migration_file(path: Path) -> dict[str, Any] | None:
    """Extract the alembic metadata from a single revision file.

    Looks for ``revision = "..."`` / ``down_revision = "..."`` (both
    accept the optional ``: str = ...`` PEP-526 annotation that alembic
    started emitting), the docstring first line, and the ``Create Date``
    marker. Returns ``None`` if the revision constant can't be found
    (e.g. ``__init__.py`` or a partial template).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None
    rev = re.search(
        r'revision(?:\s*:[^=]+)?\s*=\s*["\']([^"\']+)["\']', content,
    )
    if rev is None:
        return None
    down = re.search(
        r'down_revision(?:\s*:[^=]+)?\s*=\s*'
        r'(?:["\']([^"\']*)["\']|(None))', content,
    )
    down_id = down.group(1) if (down and down.group(1)) else None

    doc = re.match(r'\s*"""(.+?)"""', content, re.DOTALL)
    title = ""
    if doc:
        title = doc.group(1).strip().split("\n")[0].strip()

    date_m = re.search(r"Create Date:\s*(.+?)\s*$", content, re.MULTILINE)
    created = date_m.group(1).strip() if date_m else None

    return {
        "id": rev.group(1),
        "down_revision": down_id,
        "filename": path.name,
        "title": title,
        "created": created,
    }


def _build_migration_chain() -> list[dict[str, Any]]:
    """Read versions/ and return the migrations as a base → head list."""
    revs: list[dict[str, Any]] = []
    if not MIGRATIONS_DIR.is_dir():
        return revs
    for p in sorted(MIGRATIONS_DIR.glob("*.py")):
        parsed = _parse_migration_file(p)
        if parsed is not None:
            revs.append(parsed)
    # Build the linear chain : start at the rev whose ``down_revision``
    # is None (base) and walk via ``by_parent`` lookup until we hit a
    # gap. Detached / branched migrations land at the end in their
    # natural file order — alembic itself would refuse to upgrade a
    # branched tree without merge, so the diagram surfaces that as a
    # visual orphan.
    by_id = {r["id"]: r for r in revs}
    by_parent: dict[str | None, dict[str, Any]] = {}
    for r in revs:
        by_parent[r["down_revision"]] = r
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur = by_parent.get(None)
    while cur is not None and cur["id"] not in seen:
        chain.append(cur)
        seen.add(cur["id"])
        cur = by_parent.get(cur["id"])
    # Append any unreachable revisions at the end so they're still
    # visible in the UI (probably a branching mistake).
    for r in revs:
        if r["id"] not in seen:
            chain.append(r)
    # Resolve the parent title for each entry (handy in the UI).
    for r in chain:
        parent_id = r["down_revision"]
        r["parent_title"] = by_id[parent_id]["title"] if parent_id in by_id else None
    return chain


@router.get("/migrations")
async def list_migrations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Return the full alembic chain + the currently-applied revision.

    Every entry is tagged ``status`` ∈ {applied, current, pending}.
    A ``pending`` count > 0 means a release shipped migrations the
    deployed API didn't run yet — surface it to the operator.
    """
    chain = _build_migration_chain()

    current_id: str | None = None
    try:
        r = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        current_id = r.scalar_one_or_none()
    except Exception:
        # alembic_version doesn't exist yet (fresh DB) — treat as
        # "nothing applied, every migration is pending".
        current_id = None

    found_current = current_id is None  # if None, *every* rev is pending
    for r in chain:
        if r["id"] == current_id:
            r["status"] = "current"
            found_current = True
        elif not found_current:
            r["status"] = "applied"
        else:
            r["status"] = "pending"

    head_id = chain[-1]["id"] if chain else None
    n_pending = sum(1 for r in chain if r["status"] == "pending")
    n_applied = sum(1 for r in chain if r["status"] in ("applied", "current"))
    return {
        "chain": chain,
        "head": head_id,
        "current": current_id,
        "n_total": len(chain),
        "n_applied": n_applied,
        "n_pending": n_pending,
        "in_sync": n_pending == 0 and current_id == head_id,
    }


@router.get("/migrations/{rev_id}")
async def get_migration(rev_id: str) -> dict[str, Any]:
    """Return the full source + extracted upgrade/downgrade bodies of one
    revision, so the front-end can render syntax-highlighted code.
    """
    # Resolve rev_id → file path by walking versions/ ; the filename
    # may not match the revision string (numbered prefix is just a
    # convention, not a requirement).
    if not MIGRATIONS_DIR.is_dir():
        raise HTTPException(404, "migrations dir not found")
    target: Path | None = None
    for p in MIGRATIONS_DIR.glob("*.py"):
        parsed = _parse_migration_file(p)
        if parsed is not None and parsed["id"] == rev_id:
            target = p
            break
    if target is None:
        raise HTTPException(404, f"revision {rev_id!r} not in migrations dir")
    content = target.read_text(encoding="utf-8")

    # Extract the body of ``def upgrade()`` and ``def downgrade()`` —
    # naive but works because alembic-generated migrations have a
    # canonical layout (function defs at module level, no decorators).
    up = re.search(
        r"def upgrade\(\)[^\n]*:\n(.*?)(?=\n\s*def\s|\Z)",
        content, re.DOTALL,
    )
    down = re.search(
        r"def downgrade\(\)[^\n]*:\n(.*?)(?=\n\s*def\s|\Z)",
        content, re.DOTALL,
    )
    return {
        "id": rev_id,
        "filename": target.name,
        "content": content,
        "upgrade": up.group(1) if up else "",
        "downgrade": down.group(1) if down else "",
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


# ──────────────────────────────────────────────────────────────────────
# DB Schema — automatic introspection of the ORM models
# ──────────────────────────────────────────────────────────────────────
# Source of truth = ``persistence.models.Base.metadata``. Importing
# ``Base`` loads every ORM class and registers its ``Table`` with the
# metadata. No hardcoded list: adding an ORM class makes it show up
# on the schema at the next refresh.


@router.get("/db-schema")
async def db_schema(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    source: str = "orm",
) -> dict[str, Any]:
    """Return every table with its columns + foreign keys, from one of:

    - ``source=orm`` (default) : walks ``Base.metadata.tables`` — the
      compile-time view of what the API *thinks* the schema is.
      Cheap (<5 ms), no DB round-trip. Sees ORM-side metadata that the
      DB can't expose (``comment="..."``, ``CheckConstraint(name=...)``).
    - ``source=live`` : runs ``sqlalchemy.inspect()`` against the
      connected PostgreSQL — the actual on-disk schema. Slower
      (~50-200 ms), but reflects what's really there : manual
      ``ALTER TABLE``, tables created out-of-band, drift from the ORM.

    The two outputs share the exact same shape so the front-end
    renderer doesn't branch. The DB Schema dev tab uses both for the
    DIFF mode — it fetches both and surfaces what differs.
    """
    if source not in ("orm", "live"):
        raise HTTPException(status_code=400,
                            detail="source must be 'orm' or 'live'")
    if source == "live":
        return await _db_schema_live(db)
    return _db_schema_orm()


def _db_schema_orm() -> dict[str, Any]:
    """Walk ``Base.metadata`` and emit the unified schema payload."""
    from sqlalchemy import CheckConstraint, UniqueConstraint
    from sqlalchemy.dialects import postgresql
    pg_dialect = postgresql.dialect()
    tables_out: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []

    for tname in sorted(Base.metadata.tables.keys()):
        t = Base.metadata.tables[tname]
        pks = {c.name for c in t.primary_key.columns}

        # Pre-compute the set of (frozenset of column names) that have a
        # UNIQUE constraint on this table. Used to detect 1:1 vs N:1 and
        # to badge single-column UNIQUE columns inline on the diagram.
        unique_col_sets: list[set[str]] = []
        for constraint in t.constraints:
            if isinstance(constraint, UniqueConstraint):
                unique_col_sets.append({col.name for col in constraint.columns})
        # Same idea for indexes : column appears in a single-column Index
        # → badge it `IX` so a DBA can see the query-side at a glance.
        indexed_col_names: set[str] = set()
        for ix in t.indexes:
            ix_cols = [c.name for c in ix.columns]
            if len(ix_cols) == 1:
                indexed_col_names.add(ix_cols[0])

        # CHECK constraints — surface every named/anonymous CHECK with
        # its raw SQL text. Composite UNIQUEs aren't returned here ; they
        # are visible on the columns via the UQ badge if single-col, or
        # as a composite-UQ section the front-end will render at the
        # bottom of the card.
        check_constraints: list[dict[str, Any]] = []
        composite_unique: list[dict[str, Any]] = []
        for constraint in t.constraints:
            if isinstance(constraint, CheckConstraint):
                sqltext = (
                    str(constraint.sqltext)
                    if constraint.sqltext is not None else ""
                )
                if sqltext:
                    check_constraints.append({
                        "name": constraint.name or "",
                        "sql": sqltext,
                    })
            elif isinstance(constraint, UniqueConstraint):
                cols_ = [col.name for col in constraint.columns]
                if len(cols_) > 1:
                    composite_unique.append({
                        "name": constraint.name or "",
                        "columns": cols_,
                    })

        # FK constraints — walk ``foreign_key_constraints`` (one entry per
        # constraint, composite or not) rather than per-column ``c.foreign_keys``.
        # This collapses a 2-column FK into a single relationship row.
        fk_column_names: set[str] = set()
        for fkc in t.foreign_key_constraints:
            for col in fkc.columns:
                fk_column_names.add(col.name)
            if not fkc.elements:
                continue
            from_cols = [c.name for c in fkc.columns]
            to_cols = [el.column.name for el in fkc.elements]
            to_table = fkc.elements[0].column.table.name
            from_set = set(from_cols)
            # 1:1 iff the FROM side carries a UNIQUE-like constraint :
            # exact PK, exact UNIQUE constraint, or single col flagged
            # ``unique=True``.
            is_unique_side = (
                from_set == pks
                or from_set in unique_col_sets
                or (len(from_cols) == 1 and bool(next(iter(fkc.columns)).unique))
            )
            cardinality = "1:1" if is_unique_side else "N:1"
            optional = any(c.nullable for c in fkc.columns)
            label = (
                from_cols[0][:-3]
                if len(from_cols) == 1 and from_cols[0].endswith("_id")
                else (from_cols[0] if len(from_cols) == 1
                      else "(" + ", ".join(from_cols) + ")")
            )
            relationships.append({
                "from_table": t.name,
                "to_table": to_table,
                "from_columns": from_cols,
                "to_columns": to_cols,
                "label": label,
                "cardinality": cardinality,
                "optional": optional,
                "composite": len(from_cols) > 1,
                "self_loop": t.name == to_table,
                "on_delete": (fkc.ondelete or "NO ACTION").upper(),
                "on_update": (fkc.onupdate or "NO ACTION").upper(),
            })

        cols: list[dict[str, Any]] = []
        for c in t.columns:
            is_unique = bool(c.unique) or ({c.name} in unique_col_sets)
            is_indexed = c.name in indexed_col_names or bool(c.index)
            try:
                sql_type = c.type.compile(dialect=pg_dialect)
            except Exception:
                sql_type = str(c.type)
            # Server-side default = what appears in CREATE TABLE.
            # ``c.server_default`` is a ``DefaultClause`` or ``FetchedValue`` ;
            # its ``.arg`` is the SQL text (or a function/text clause).
            default_sql: str | None = None
            if c.server_default is not None:
                try:
                    default_sql = str(c.server_default.arg)  # type: ignore[attr-defined]
                except (AttributeError, Exception):
                    default_sql = str(c.server_default)
                # Trim noisy parentheses dagre would render literally
                if default_sql and len(default_sql) > 40:
                    default_sql = default_sql[:37] + "…"
            cols.append({
                "name": c.name,
                "type": sql_type,
                "nullable": bool(c.nullable),
                "pk": c.name in pks,
                "fk": c.name in fk_column_names,
                "unique": is_unique,
                "indexed": is_indexed,
                "default": default_sql,
                "comment": c.comment,
            })
        tables_out.append({
            "name": t.name,
            "columns": cols,
            "n_columns": len(cols),
            "comment": t.comment,
            "check_constraints": check_constraints,
            "composite_unique": composite_unique,
        })

    return {
        "tables": tables_out,
        "relationships": relationships,
        "n_tables": len(tables_out),
        "n_relationships": len(relationships),
        "source": "orm",
    }


async def _db_schema_live(db: AsyncSession) -> dict[str, Any]:
    """Introspect the live PostgreSQL schema via ``sqlalchemy.inspect``.

    Returns the same shape as ``_db_schema_orm()`` so the front-end
    renders both interchangeably. Anything that lives only in the
    ORM (Python-side ``comment="..."`` not echoed to DB COMMENT,
    Python defaults, etc.) is absent here — that's the point : if a
    field shows up in ``orm`` but not ``live``, you know the drift is
    in the migration layer.
    """
    def _introspect(sync_conn: Any) -> dict[str, Any]:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(sync_conn)
        tables_out: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []

        # Skip alembic's bookkeeping table — it would appear on the
        # diagram and add noise without educational value.
        table_names = sorted(
            n for n in inspector.get_table_names(schema="public")
            if n != "alembic_version"
        )

        for tname in table_names:
            cols_info = inspector.get_columns(tname)
            pk_constraint = inspector.get_pk_constraint(tname)
            pks = set(pk_constraint.get("constrained_columns", []))
            fks_info = inspector.get_foreign_keys(tname)
            indexes_info = inspector.get_indexes(tname)
            unique_info = inspector.get_unique_constraints(tname)
            try:
                checks_info = inspector.get_check_constraints(tname)
            except NotImplementedError:
                checks_info = []
            try:
                tc = inspector.get_table_comment(tname)
                table_comment = tc.get("text") if tc else None
            except NotImplementedError:
                table_comment = None

            indexed_col_names: set[str] = set()
            for ix in indexes_info:
                cols_ = ix.get("column_names", []) or []
                if len(cols_) == 1 and cols_[0]:
                    indexed_col_names.add(cols_[0])

            unique_col_sets: list[set[str]] = []
            for uq in unique_info:
                cols_set = {c for c in (uq.get("column_names") or []) if c}
                if cols_set:
                    unique_col_sets.append(cols_set)

            fk_col_names: set[str] = set()
            for fk in fks_info:
                for cname in fk.get("constrained_columns", []) or []:
                    fk_col_names.add(cname)

            cols: list[dict[str, Any]] = []
            nullable_by_col: dict[str, bool] = {}
            for c in cols_info:
                name = str(c["name"])
                nullable = bool(c.get("nullable", True))
                nullable_by_col[name] = nullable
                is_unique = ({name} in unique_col_sets)
                default = c.get("default")
                if default is not None:
                    default = str(default)
                    if len(default) > 40:
                        default = default[:37] + "…"
                # ``str(c["type"])`` collapses TIMESTAMP(timezone=True)
                # to just ``"TIMESTAMP"`` — strips the TZ qualifier,
                # which then drifts against the ORM side's
                # ``"TIMESTAMP WITH TIME ZONE"``. Compile against the
                # live connection's dialect (PG here) so both sides
                # produce the same canonical form for every type.
                try:
                    type_str = c["type"].compile(dialect=sync_conn.dialect)
                except Exception:
                    type_str = str(c["type"])
                cols.append({
                    "name": name,
                    "type": type_str,
                    "nullable": nullable,
                    "pk": name in pks,
                    "fk": name in fk_col_names,
                    "unique": is_unique,
                    "indexed": name in indexed_col_names,
                    "default": default,
                    "comment": c.get("comment"),
                })

            composite_unique = [
                {"name": uq.get("name", "") or "",
                 "columns": list(uq.get("column_names") or [])}
                for uq in unique_info
                if len(uq.get("column_names") or []) > 1
            ]
            check_constraints = [
                {"name": ck.get("name", "") or "",
                 "sql": str(ck.get("sqltext", "") or "")}
                for ck in checks_info
                if ck.get("sqltext")
            ]

            tables_out.append({
                "name": tname,
                "columns": cols,
                "n_columns": len(cols),
                "comment": table_comment,
                "check_constraints": check_constraints,
                "composite_unique": composite_unique,
            })

            for fk in fks_info:
                from_cols = list(fk.get("constrained_columns") or [])
                to_cols = list(fk.get("referred_columns") or [])
                to_table = fk.get("referred_table")
                if not from_cols or not to_table:
                    continue
                from_set = set(from_cols)
                is_unique_side = (
                    from_set == pks or from_set in unique_col_sets
                )
                cardinality = "1:1" if is_unique_side else "N:1"
                optional = any(
                    nullable_by_col.get(fc, True) for fc in from_cols
                )
                if len(from_cols) == 1:
                    label = (
                        from_cols[0][:-3]
                        if from_cols[0].endswith("_id")
                        else from_cols[0]
                    )
                else:
                    label = "(" + ", ".join(from_cols) + ")"
                options = fk.get("options") or {}
                relationships.append({
                    "from_table": tname,
                    "to_table": to_table,
                    "from_columns": from_cols,
                    "to_columns": to_cols,
                    "label": label,
                    "cardinality": cardinality,
                    "optional": optional,
                    "composite": len(from_cols) > 1,
                    "self_loop": tname == to_table,
                    "on_delete": (options.get("ondelete") or "NO ACTION").upper(),
                    "on_update": (options.get("onupdate") or "NO ACTION").upper(),
                })

        return {
            "tables": tables_out,
            "relationships": relationships,
            "n_tables": len(tables_out),
            "n_relationships": len(relationships),
            "source": "live",
        }

    conn = await db.connection()
    return await conn.run_sync(_introspect)


# ── Hardware / resource monitor ───────────────────────────────────────────────
# Host CPU/RAM/disk read straight from /proc (Docker leaves it host-wide, so no
# psutil dep needed) + best-effort GPU via nvidia-smi. Read-only, dev-only.

def _read_meminfo() -> dict[str, float]:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = int(rest.strip().split()[0])  # kB
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        used = max(0, total - avail)
        return {
            "total_gb": round(total / 1_048_576, 2),
            "used_gb": round(used / 1_048_576, 2),
            "percent": round(100 * used / total, 1) if total else 0.0,
        }
    except Exception:
        return {"total_gb": 0.0, "used_gb": 0.0, "percent": 0.0}


def _read_cpu_jiffies() -> dict[str, tuple[int, int]]:
    """Per-cpu (idle, total) jiffies from /proc/stat."""
    out: dict[str, tuple[int, int]] = {}
    with open("/proc/stat") as f:
        for line in f:
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            vals = [int(x) for x in parts[1:]]
            if len(vals) < 4:
                continue
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
            out[parts[0]] = (idle, sum(vals))
    return out


async def _read_cpu() -> dict[str, Any]:
    """Overall + per-core CPU % via two /proc/stat samples 200 ms apart."""
    try:
        a = _read_cpu_jiffies()
        await asyncio.sleep(0.2)
        b = _read_cpu_jiffies()

        def pct(name: str) -> float:
            i0, t0 = a.get(name, (0, 0))
            i1, t1 = b.get(name, (0, 0))
            dt = t1 - t0
            return round(100 * (1 - (i1 - i0) / dt), 1) if dt > 0 else 0.0

        cores = sorted((k for k in b if k != "cpu"), key=lambda c: int(c[3:] or 0))
        load = [0.0, 0.0, 0.0]
        try:
            with open("/proc/loadavg") as f:
                load = [float(x) for x in f.read().split()[:3]]
        except Exception:
            pass
        return {
            "percent": pct("cpu"),
            "cores": len(cores),
            "per_core": [pct(c) for c in cores],
            "load_avg": load,
        }
    except Exception:
        return {"percent": 0.0, "cores": 0, "per_core": [], "load_avg": [0.0, 0.0, 0.0]}


def _read_disk() -> dict[str, float]:
    try:
        total, used, _free = shutil.disk_usage("/")
        return {
            "total_gb": round(total / 1_073_741_824, 1),
            "used_gb": round(used / 1_073_741_824, 1),
            "percent": round(100 * used / total, 1) if total else 0.0,
        }
    except Exception:
        return {"total_gb": 0.0, "used_gb": 0.0, "percent": 0.0}


def _to_float(s: str) -> float | None:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


async def _read_gpu() -> list[dict[str, Any]]:
    """nvidia-smi best-effort — empty list when no GPU / tool is unavailable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        gpus: list[dict[str, Any]] = []
        for line in out.decode().strip().splitlines():
            cells = [c.strip() for c in line.split(",")]
            if len(cells) < 5:
                continue
            name, util, mem_used, mem_total, temp = cells[:5]
            gpus.append({
                "name": name,
                "util_percent": _to_float(util),
                "mem_used_mb": _to_float(mem_used),
                "mem_total_mb": _to_float(mem_total),
                "temp_c": _to_float(temp),
            })
        return gpus
    except Exception:
        return []


@router.get("/hardware")
async def hardware() -> dict[str, Any]:
    """Host CPU / RAM / disk (from /proc) + best-effort GPU (nvidia-smi).

    Read straight from the container's /proc, which Docker leaves host-wide, so
    it reflects the machine running the stack. ``gpu`` is empty when no NVIDIA
    GPU is exposed to the container. Dev-only, read-only, no auth.
    """
    return {
        "cpu": await _read_cpu(),
        "memory": _read_meminfo(),
        "disk": _read_disk(),
        "gpu": await _read_gpu(),
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


# ── Per-container resource history (Prometheus/cAdvisor) ──────────────────────
# The pro way to graph "how much does each container consume over time" : the
# data lives in Prometheus (scraped from cAdvisor), this just range-queries it
# so the dev Hardware tab can plot the curves. Needs the `obs` compose profile.
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
# Fixed queries (no user-controlled URL/expr → no SSRF surface).
_CPU_QUERY = 'sum by (name) (rate(container_cpu_usage_seconds_total{name=~"fxvol-.*"}[1m])) * 100'
_MEM_QUERY = 'sum by (name) (container_memory_working_set_bytes{name=~"fxvol-.*"})'


async def _prom_range(
    client: httpx.AsyncClient, query: str, start: float, end: float, step: int,
) -> list[dict[str, Any]]:
    r = await client.get(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={"query": query, "start": start, "end": end, "step": step},
    )
    r.raise_for_status()
    series: list[dict[str, Any]] = []
    for s in r.json().get("data", {}).get("result", []):
        name = s.get("metric", {}).get("name", "?")
        pts = [[float(t), float(v)] for t, v in s.get("values", [])]
        series.append({"name": name, "points": pts})
    series.sort(key=lambda x: x["name"])
    return series


# Docker-socket fallback: per-container stats where cAdvisor can't see them
# (Docker Desktop / WSL2 only exposes the root cgroup). Reads the Docker Engine
# API over the mounted socket and keeps a short in-memory history, sampled on
# each poll. The socket is mounted in local dev only (compose override).
_DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
_HISTORY: dict[str, deque[tuple[float, float, float]]] = {}  # name -> (ts, cpu%, mem bytes)
_HISTORY_MAXLEN = 1080  # ~3 h at one sample / 10 s
_last_sample = 0.0
_MIN_SAMPLE_GAP_S = 5.0


def _docker_cpu_pct(stats: dict[str, Any]) -> float | None:
    try:
        cpu, pre = stats["cpu_stats"], stats["precpu_stats"]
        cd = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sd = cpu["system_cpu_usage"] - pre.get("system_cpu_usage", 0)
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        if sd > 0 and cd >= 0:
            return round(cd / sd * ncpu * 100, 1)
    except Exception:
        pass
    return None


async def _sample_docker() -> None:
    """Append one per-container (cpu%, mem) sample to the in-memory history."""
    global _last_sample
    now = time.time()
    if now - _last_sample < _MIN_SAMPLE_GAP_S:
        return
    try:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCK)
        async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=8.0) as cli:
            containers = (await cli.get("/containers/json")).json()
            targets = [
                (c["Id"], (c.get("Names") or ["?"])[0].lstrip("/"))
                for c in containers
                if (c.get("Names") or ["?"])[0].lstrip("/").startswith("fxvol-")
            ]

            async def _one(cid: str, name: str) -> tuple[str, float | None, float]:
                try:
                    s = (await cli.get(f"/containers/{cid}/stats", params={"stream": "false"})).json()
                    ms = s.get("memory_stats", {})
                    inactive = (ms.get("stats", {}) or {}).get("inactive_file", 0) or 0
                    mem = max(0.0, float(ms.get("usage", 0)) - float(inactive))
                    return name, _docker_cpu_pct(s), mem
                except Exception:
                    return name, None, 0.0

            results = await asyncio.gather(*[_one(cid, n) for cid, n in targets])
        for name, cpu_pct, mem in results:
            if cpu_pct is None:
                continue
            _HISTORY.setdefault(name, deque(maxlen=_HISTORY_MAXLEN)).append((now, cpu_pct, mem))
        _last_sample = now
    except Exception:
        pass  # socket absent (prod) / docker unreachable → leave history untouched


@router.get("/containers/metrics")
async def container_metrics(minutes: int = 15) -> dict[str, Any]:
    """Per-container CPU % + RAM (bytes) time-series over the last ``minutes``.

    Prefers Prometheus (cAdvisor — persistent, used on Linux/EC2). Falls back to
    the Docker socket (sampled in-memory each poll) when Prometheus has no
    per-container series — e.g. Docker Desktop / WSL2, where cAdvisor only sees
    the root cgroup. ``source`` says which path served the data.
    """
    minutes = max(1, min(180, minutes))
    end = datetime.now(UTC)
    start = end - timedelta(minutes=minutes)
    step = max(15, minutes * 60 // 120)  # ~120 points across the window
    base = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "step": step,
    }
    # 1) Prometheus / cAdvisor (works on Linux/EC2, persistent history).
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            cpu = await _prom_range(client, _CPU_QUERY, start.timestamp(), end.timestamp(), step)
            mem = await _prom_range(client, _MEM_QUERY, start.timestamp(), end.timestamp(), step)
        if any(s["points"] for s in cpu):
            return {**base, "reachable": True, "source": "prometheus", "cpu": cpu, "mem": mem}
    except Exception:
        pass
    # 2) Docker-socket fallback (Docker Desktop): sample now, return the history.
    await _sample_docker()
    cutoff = time.time() - minutes * 60
    cpu = [{"name": n, "points": [[ts, c] for ts, c, _m in pts if ts >= cutoff]} for n, pts in sorted(_HISTORY.items())]
    mem = [{"name": n, "points": [[ts, m] for ts, _c, m in pts if ts >= cutoff]} for n, pts in sorted(_HISTORY.items())]
    return {**base, "reachable": bool(_HISTORY), "source": "docker", "cpu": cpu, "mem": mem}
