"""Idempotency keys on every order request (#497).

Only the structured submit was protected against a repeat (a preview can be
actioned once). A close, a trade close, a spot order, a manual ticket and a
cancel created a new order on every request; prod has produced duplicate
spot orders that way.

The browser sends ``Idempotency-Key: <uuid>``, one per user action. For the
routes below:

* first request with a key: the key is recorded as in flight, the request
  runs, and its answer (status and body) is stored with the key;
* the same request again with the same key: the stored answer is returned,
  with ``Idempotent-Replay: true``; nothing runs a second time;
* again while the first is still running, or when the first never finished
  (the api died after it may have placed an order): 409. The outcome is not
  known here, and running it again is exactly what the key is for
  preventing; the blotter says what happened;
* the same key on a different request: 422.

A request without the header runs as before. A request the api refused for
lack of authentication leaves no key behind.

Scope and limits: the key protects against a REPEAT OF ONE REQUEST (double
click, retry after a timeout, a proxy resend). It does not stop the trader
from sending the same order twice on purpose with two clicks: that is the
close guard's job for closes, and the trader's decision otherwise.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from persistence.models import IdempotencyKey

logger = logging.getLogger(__name__)

HEADER = "Idempotency-Key"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{8,80}$")
RETENTION = timedelta(days=2)
# The order requests: everything that can create or cancel an order.
ORDER_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"/api/v1/positions/\d+/close$")),
    ("POST", re.compile(r"/api/v1/trades/\d+/close$")),
    ("POST", re.compile(r"/api/v1/trades/\d+/cancel$")),
    ("POST", re.compile(r"/api/v1/orders$")),
    ("DELETE", re.compile(r"/api/v1/orders/\d+$")),
    ("POST", re.compile(r"/api/v1/trade/submit$")),
)
# Answers that say "not you": nothing ran, the key is given back.
_NOT_STORED = (401, 403, 429)


def is_order_request(method: str, path: str) -> bool:
    return any(m == method and p.search(path) for m, p in ORDER_ROUTES)


def _refuse(status: int, error: str, reason: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"error": error, "reason": reason}})


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, sessionmaker_factory: Callable[[], async_sessionmaker[AsyncSession]]) -> None:
        super().__init__(app)
        self._sm_factory = sessionmaker_factory

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        key = request.headers.get(HEADER)
        if not key or not is_order_request(request.method, request.url.path):
            return await call_next(request)
        if not KEY_PATTERN.match(key):
            return _refuse(422, "idempotency_key_invalid", "8 to 80 letters, digits, '-' or '_'")
        scope = f"{request.method} {request.url.path}"[:200]
        try:
            claimed = await self._claim(key, scope)
        except Exception:
            # The key store cannot be reached: refusing is the safe answer for
            # a request whose whole point is not to run twice.
            logger.exception("idempotency_store_unavailable")
            return _refuse(503, "idempotency_store_unavailable", "the request was not run; send it again")
        if claimed is not None:
            return claimed

        try:
            response = await call_next(request)
        except Exception:
            await self._forget(key)   # nothing was answered: the request may be sent again
            raise
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        if response.status_code in _NOT_STORED:
            await self._forget(key)
        else:
            await self._store(key, response.status_code, body)
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return Response(content=body, status_code=response.status_code, headers=headers,
                        media_type=response.media_type)

    async def _claim(self, key: str, scope: str) -> Response | None:
        """None when this request owns the key and must run; otherwise the
        answer to give instead."""
        now = datetime.now(UTC)
        async with self._sm_factory()() as db:
            await db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < now - RETENTION))
            db.add(IdempotencyKey(key=key, scope=scope, state="in_flight", created_at=now))
            try:
                await db.commit()
                return None
            except IntegrityError:
                await db.rollback()
            row = (await db.execute(select(IdempotencyKey).where(IdempotencyKey.key == key))).scalar_one_or_none()
        if row is None:   # deleted between the insert and the read: treat as in flight
            return _refuse(409, "request_in_progress", "the first request with this key is still running")
        if row.scope != scope:
            return _refuse(422, "idempotency_key_reused", f"this key was used for {row.scope}")
        if row.state != "done":
            return _refuse(409, "request_in_progress",
                           "the first request with this key has not answered; its outcome is not known "
                           "here, check the Orders blotter before sending it again")
        payload: Any = row.response
        return JSONResponse(status_code=int(row.status_code or 200), content=payload,
                            headers={"Idempotent-Replay": "true"})

    async def _store(self, key: str, status: int, body: bytes) -> None:
        try:
            content: Any = json.loads(body) if body else None
        except ValueError:
            content = {"detail": body.decode("utf-8", "replace")[:2000]}
        try:
            async with self._sm_factory()() as db:
                await db.execute(
                    update(IdempotencyKey).where(IdempotencyKey.key == key)
                    .values(state="done", status_code=status, response=content, completed_at=datetime.now(UTC)))
                await db.commit()
        except Exception:
            # The request ran and its answer goes out; the key stays in
            # flight, so a repeat gets 409, never a second run.
            logger.exception("idempotency_store_failed key=%s", key)

    async def _forget(self, key: str) -> None:
        try:
            async with self._sm_factory()() as db:
                await db.execute(delete(IdempotencyKey).where(IdempotencyKey.key == key))
                await db.commit()
        except Exception:
            logger.exception("idempotency_forget_failed key=%s", key)
