"""Admin router — manage the versioned vol trading config.

Endpoints :
    GET    /api/v1/admin/config                  -> current version + payload
    GET    /api/v1/admin/config/schema           -> JSON Schema for RJSF
    PUT    /api/v1/admin/config                  -> deep-merge patch, new version
    GET    /api/v1/admin/config/history?limit=50 -> audit trail, newest first
    POST   /api/v1/admin/config/revert/{version} -> duplicate a past version

No auth for now (solo-trader cockpit, bound to localhost in dev and
behind Nginx + Let's Encrypt in prod). When the cockpit opens to
multiple users, wrap the PUT / POST routes in a ``Depends(require_admin)``.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.dependencies import get_db_session, get_redis
from api.orchestration import config_service
from api.schemas.admin import ConfigPatchRequest, ConfigResponse, ConfigRevertRequest
from core.risk import greek_limits as gl
from persistence.models import AppConfigScalar

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


def _to_response(record: config_service.ConfigRecord) -> ConfigResponse:
    return ConfigResponse(
        version=record.version,
        config=record.config,
        updated_at=record.updated_at,
        updated_by=record.updated_by,
        comment=record.comment,
    )


@router.get("/config", response_model=ConfigResponse)
async def get_current_config(db: DbDep) -> ConfigResponse:
    return _to_response(await config_service.get_current(db))


@router.get("/config/schema")
async def get_config_schema() -> dict:
    """Raw JSON Schema of :class:`VolTradingConfig` for React JSON Schema Form."""
    return config_service.export_json_schema()


@router.put("/config", response_model=ConfigResponse, dependencies=[Depends(require_write)])
async def update_config(
    req: ConfigPatchRequest, db: DbDep, redis: RedisDep,
) -> ConfigResponse:
    try:
        record = await config_service.update(
            db, redis, patch=req.patch, user=req.user, comment=req.comment,
        )
    except ValidationError as e:
        # e.errors() keeps raw ValueError objects in 'ctx', not JSON-serializable.
        # e.json() is Pydantic's canonical serializer -- re-parse it to a clean
        # list of dicts so FastAPI can emit it as the 422 body.
        raise HTTPException(
            status_code=422,
            detail={
                "message": "patch violates vol_config schema",
                "errors": json.loads(e.json()),
            },
        ) from e
    return _to_response(record)


@router.get("/config/history", response_model=list[ConfigResponse])
async def list_config_history(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[ConfigResponse]:
    records = await config_service.history(db, limit=limit)
    return [_to_response(r) for r in records]


@router.post("/config/revert/{version}", response_model=ConfigResponse, dependencies=[Depends(require_write)])
async def revert_config(
    version: int, req: ConfigRevertRequest, db: DbDep, redis: RedisDep,
) -> ConfigResponse:
    try:
        record = await config_service.revert(
            db, redis, target_version=version, user=req.user, comment=req.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _to_response(record)


# ──────────────────────────────────────────────────────────────────────
# Risk settings — greek-limit policy params (config_scalar 'greek_limits')
# ──────────────────────────────────────────────────────────────────────

_RISK_NS = "greek_limits"


class RiskConfigPatch(BaseModel):
    updates: dict[str, float] = Field(default_factory=dict)
    user: str = "desk"
    comment: str | None = None


def _validate_risk_param(name: str, value: float) -> None:
    """Light bounds — these are policy knobs, not free numbers."""
    if name not in gl.CONFIG_DEFAULTS:
        raise HTTPException(status_code=422, detail=f"unknown risk param: {name}")
    if name == "alpha" and not 0 < value <= 1:
        raise HTTPException(status_code=422, detail="alpha must be in (0, 1]")
    if name.startswith("beta_") and not 0 <= value <= 1:
        raise HTTPException(status_code=422, detail=f"{name} must be in [0, 1]")
    if name in {"shock_spot", "shock_vol", "nav_halflife_days"} and value <= 0:
        raise HTTPException(status_code=422, detail=f"{name} must be > 0")
    if name == "nav_hwm_floor" and not 0 < value <= 1:
        raise HTTPException(status_code=422, detail="nav_hwm_floor must be in (0, 1]")


async def _risk_config_payload(db: AsyncSession) -> dict[str, Any]:
    rows = {
        r.name: r
        for r in (await db.execute(
            select(AppConfigScalar).where(AppConfigScalar.namespace == _RISK_NS)
        )).scalars().all()
    }
    params = []
    for name, default in gl.CONFIG_DEFAULTS.items():
        unit, desc = gl.CONFIG_META.get(name, ("", ""))
        row = rows.get(name)
        params.append({
            "name": name,
            "value": float(row.value) if row is not None else default,
            "default": default,
            "unit": unit,
            "description": desc,
            "is_default": row is None,
            "updated_by": row.updated_by if row is not None else None,
        })
    return {"namespace": _RISK_NS, "params": params}


@router.get("/risk-config")
async def get_risk_config(db: DbDep) -> dict[str, Any]:
    """Effective greek-limit policy = code defaults overlaid by config_scalar."""
    return await _risk_config_payload(db)


@router.put("/risk-config", dependencies=[Depends(require_write)])
async def put_risk_config(req: RiskConfigPatch, db: DbDep) -> dict[str, Any]:
    """Upsert greek-limit policy params (hot-applied on the next /greek-limits)."""
    if not req.updates:
        raise HTTPException(status_code=422, detail="no updates provided")
    existing = {
        r.name: r
        for r in (await db.execute(
            select(AppConfigScalar).where(AppConfigScalar.namespace == _RISK_NS)
        )).scalars().all()
    }
    for name, value in req.updates.items():
        _validate_risk_param(name, float(value))
        unit, desc = gl.CONFIG_META.get(name, ("", ""))
        row = existing.get(name)
        if row is not None:
            row.value = float(value)
            row.updated_by = req.user
        else:
            db.add(AppConfigScalar(
                namespace=_RISK_NS, name=name, value=float(value),
                unit=unit, description=desc, is_active=True, updated_by=req.user,
            ))
    await db.commit()
    return await _risk_config_payload(db)
