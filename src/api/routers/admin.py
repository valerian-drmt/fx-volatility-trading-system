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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from redis import asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_write
from api.dependencies import get_db_session, get_redis
from api.orchestration import config_service
from api.schemas.admin import ConfigPatchRequest, ConfigResponse, ConfigRevertRequest

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
