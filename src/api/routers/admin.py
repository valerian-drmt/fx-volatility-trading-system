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
from api.config import get_settings
from api.dependencies import get_db_session, get_redis
from api.orchestration import config_service
from api.schemas.admin import ConfigPatchRequest, ConfigResponse, ConfigRevertRequest
from core import config_catalog as cc
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
    """The active `VolConfig` (version, payload, author, comment)."""
    return _to_response(await config_service.get_current(db))


@router.get("/config/schema")
async def get_config_schema() -> dict:
    """Raw JSON Schema of :class:`VolTradingConfig` for React JSON Schema Form."""
    return config_service.export_json_schema()


@router.put("/config", response_model=ConfigResponse, dependencies=[Depends(require_write)])
async def update_config(
    req: ConfigPatchRequest, db: DbDep, redis: RedisDep,
) -> ConfigResponse:
    """Append a new `VolConfig` version from a partial patch, validated against the schema (422 with the errors otherwise), and broadcast it to the engines."""
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
    """Past `VolConfig` versions, newest first."""
    records = await config_service.history(db, limit=limit)
    return [_to_response(r) for r in records]


@router.post("/config/revert/{version}", response_model=ConfigResponse, dependencies=[Depends(require_write)])
async def revert_config(
    version: int, req: ConfigRevertRequest, db: DbDep, redis: RedisDep,
) -> ConfigResponse:
    """Append a new version equal to an earlier one (the history stays append-only). 404 when the version does not exist."""
    try:
        record = await config_service.revert(
            db, redis, target_version=version, user=req.user, comment=req.comment,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _to_response(record)


# ──────────────────────────────────────────────────────────────────────
# Domain settings — editable policy knobs per desk domain (config_scalar).
# Catalog in core.config_catalog; consumers read the same rows so edits are live.
# ──────────────────────────────────────────────────────────────────────


class DomainSettingsPatch(BaseModel):
    updates: dict[str, float] = Field(default_factory=dict)
    user: str = "desk"


async def _domain_payload(db: AsyncSession, domain: str) -> dict[str, Any]:
    spec = cc.DOMAINS[domain]
    namespaces = {p.namespace for p in spec}
    rows: dict[tuple[str, str], AppConfigScalar] = {}
    for ns in namespaces:
        for r in (await db.execute(
            select(AppConfigScalar).where(AppConfigScalar.namespace == ns)
        )).scalars().all():
            rows[(ns, r.name)] = r
    params = []
    for p in spec:
        row = rows.get((p.namespace, p.name))
        params.append({
            "name": p.name,
            "namespace": p.namespace,
            "value": float(row.value) if row is not None else p.default,
            "default": p.default,
            "unit": p.unit,
            "description": p.description,
            "is_default": row is None,
            "updated_by": row.updated_by if row is not None else None,
        })
    return {"domain": domain, "title": cc.DOMAIN_TITLES.get(domain, domain), "params": params}


@router.get("/settings/{domain}")
async def get_domain_settings(domain: str, db: DbDep) -> dict[str, Any]:
    """Effective settings for a domain = code defaults overlaid by config_scalar."""
    if domain not in cc.DOMAINS:
        raise HTTPException(status_code=404, detail=f"unknown settings domain: {domain}")
    return await _domain_payload(db, domain)


@router.put("/settings/{domain}", dependencies=[Depends(require_write)])
async def put_domain_settings(domain: str, req: DomainSettingsPatch, db: DbDep) -> dict[str, Any]:
    """Upsert a domain's policy knobs — applied live by the consuming endpoints."""
    if domain not in cc.DOMAINS:
        raise HTTPException(status_code=404, detail=f"unknown settings domain: {domain}")
    if not req.updates:
        raise HTTPException(status_code=422, detail="no updates provided")
    for name, value in req.updates.items():
        p = cc.param(domain, name)
        if p is None:
            raise HTTPException(status_code=422, detail=f"unknown param '{name}' for domain '{domain}'")
        err = cc.validate(p, float(value))
        if err is not None:
            raise HTTPException(status_code=422, detail=err)
        row = (await db.execute(
            select(AppConfigScalar).where(
                AppConfigScalar.namespace == p.namespace, AppConfigScalar.name == name,
            )
        )).scalar_one_or_none()
        if row is not None:
            row.value = float(value)
            row.updated_by = req.user
        else:
            db.add(AppConfigScalar(
                namespace=p.namespace, name=name, value=float(value),
                unit=p.unit, description=p.description, is_active=True, updated_by=req.user,
            ))
    await db.commit()
    return await _domain_payload(db, domain)


# ── Kill switch (#315) ───────────────────────────────────────────────────────
# GET is public (the desk badges it); POST / DELETE need the trader cookie.
# The flag lives in Postgres (persistence.halt); Redis mirrors it and carries
# the system_alerts message; the execution engine cancels working orders.


class HaltRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.get("/halt")
async def get_halt_state(db: DbDep) -> dict[str, Any]:
    """The kill switch: whether trading is halted, why, by whom and since when."""
    from persistence.halt import get_halt

    h = await get_halt(db)
    return {"halted": h.halted, "reason": h.reason, "set_by": h.set_by,
            "set_at": h.set_at.isoformat() if h.set_at else None}


async def _forward_halt_to_execution() -> dict[str, Any]:

    import httpx

    from shared.internal_auth import internal_headers
    url = get_settings().EXECUTION_URL + "/internal/halt"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, headers=internal_headers())
        return {"status": r.status_code, **(r.json() if r.headers.get("content-type", "").startswith("application/json") else {})}
    except Exception as e:  # the flag is already set; cancelling is best effort
        return {"status": None, "error": type(e).__name__}


@router.post("/halt", dependencies=[Depends(require_write)])
async def halt_trading(body: HaltRequest, db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Stop everything: flag set (durable), orders at IB cancelled, alert published."""
    from bus.halt import mirror_halt
    from persistence.halt import set_halt

    st = await set_halt(db, halted=True, reason=body.reason, by="trader")
    await db.commit()
    try:
        await mirror_halt(redis, halted=True, reason=body.reason, by="trader")
    except Exception:
        pass
    cancel = await _forward_halt_to_execution()
    return {"halted": True, "reason": st.reason, "set_at": st.set_at.isoformat() if st.set_at else None,
            "execution_cancel": cancel}


@router.delete("/halt", dependencies=[Depends(require_write)])
async def resume_trading(body: HaltRequest, db: DbDep, redis: RedisDep) -> dict[str, Any]:
    """Lift the kill switch. Requires the trader cookie and a reason."""
    from bus.halt import mirror_halt
    from persistence.halt import set_halt

    st = await set_halt(db, halted=False, reason=body.reason, by="trader")
    await db.commit()
    try:
        await mirror_halt(redis, halted=False, reason=body.reason, by="trader")
    except Exception:
        pass
    return {"halted": False, "reason": st.reason}
