"""#497: an order request carrying an Idempotency-Key runs once."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from api.middleware.idempotency import HEADER, ORDER_ROUTES, IdempotencyMiddleware, is_order_request
from persistence.models import IdempotencyKey

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
KEY = "11111111-2222-3333-4444-555555555555"


def _app(sm: Any, calls: dict[str, int]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware, sessionmaker_factory=lambda: sm)

    @app.post("/api/v1/positions/{pid}/close")
    async def close(pid: int) -> dict[str, Any]:
        calls["close"] = calls.get("close", 0) + 1
        return {"position_id": pid, "order_id": 100 + calls["close"]}

    @app.post("/api/v1/orders")
    async def order() -> dict[str, Any]:
        calls["order"] = calls.get("order", 0) + 1
        if calls.get("refuse"):
            raise HTTPException(400, detail={"error": "pretrade_gate_failed", "failures": ["trading_halted"]})
        if calls.get("unauth"):
            raise HTTPException(401, detail="login required")
        return {"spot_order_id": calls["order"]}

    @app.post("/api/v1/trade/preview")
    async def preview() -> dict[str, Any]:
        calls["preview"] = calls.get("preview", 0) + 1
        return {"n": calls["preview"]}

    return app


def _client(sm: Any, calls: dict[str, int]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(sm, calls)), base_url="http://test")


async def _rows(sm: Any) -> list[IdempotencyKey]:
    async with sm() as db:
        return list((await db.execute(select(IdempotencyKey))).scalars().all())


async def test_the_same_request_sent_twice_creates_one_order(sqlite_sessionmaker: Any) -> None:
    calls: dict[str, int] = {}
    async with _client(sqlite_sessionmaker, calls) as c:
        first = await c.post("/api/v1/positions/7/close", json={"qty": 1}, headers={HEADER: KEY})
        again = await c.post("/api/v1/positions/7/close", json={"qty": 1}, headers={HEADER: KEY})
    assert calls["close"] == 1, "the handler ran once"
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json() == {"position_id": 7, "order_id": 101}
    assert again.headers["Idempotent-Replay"] == "true" and "Idempotent-Replay" not in first.headers


async def test_a_refusal_is_replayed_too(sqlite_sessionmaker: Any) -> None:
    """The answer is the answer: a repeat of a refused request does not run
    the checks again as if it were a new action."""
    calls: dict[str, int] = {"refuse": 1}
    async with _client(sqlite_sessionmaker, calls) as c:
        first = await c.post("/api/v1/orders", json={}, headers={HEADER: KEY})
        again = await c.post("/api/v1/orders", json={}, headers={HEADER: KEY})
    assert calls["order"] == 1
    assert first.status_code == again.status_code == 400
    assert again.json() == first.json()


async def test_two_actions_are_two_orders(sqlite_sessionmaker: Any) -> None:
    """A key protects a repeat of ONE request. Two clicks are two keys: the
    trader decides, not the application."""
    calls: dict[str, int] = {}
    async with _client(sqlite_sessionmaker, calls) as c:
        await c.post("/api/v1/orders", json={}, headers={HEADER: KEY})
        await c.post("/api/v1/orders", json={}, headers={HEADER: KEY.replace("1", "9")})
        await c.post("/api/v1/orders", json={})          # no key: runs as before
    assert calls["order"] == 3


async def test_a_key_is_bound_to_its_request(sqlite_sessionmaker: Any) -> None:
    calls: dict[str, int] = {}
    async with _client(sqlite_sessionmaker, calls) as c:
        await c.post("/api/v1/positions/7/close", json={}, headers={HEADER: KEY})
        other = await c.post("/api/v1/positions/8/close", json={}, headers={HEADER: KEY})
        bad = await c.post("/api/v1/orders", json={}, headers={HEADER: "short"})
    assert other.status_code == 422 and other.json()["detail"]["error"] == "idempotency_key_reused"
    assert bad.status_code == 422 and bad.json()["detail"]["error"] == "idempotency_key_invalid"
    assert calls == {"close": 1}


async def test_a_request_that_never_answered_is_not_run_again(sqlite_sessionmaker: Any) -> None:
    """The api died after it may have placed the order: the outcome is not
    known, and a blind second run is what the key exists to prevent."""
    async with sqlite_sessionmaker() as db:
        db.add(IdempotencyKey(key=KEY, scope="POST /api/v1/positions/7/close", state="in_flight"))
        await db.commit()
    calls: dict[str, int] = {}
    async with _client(sqlite_sessionmaker, calls) as c:
        r = await c.post("/api/v1/positions/7/close", json={}, headers={HEADER: KEY})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "request_in_progress"
    assert calls == {}


async def test_an_unauthenticated_request_leaves_no_key(sqlite_sessionmaker: Any) -> None:
    calls: dict[str, int] = {"unauth": 1}
    async with _client(sqlite_sessionmaker, calls) as c:
        assert (await c.post("/api/v1/orders", json={}, headers={HEADER: KEY})).status_code == 401
        calls.pop("unauth")
        ok = await c.post("/api/v1/orders", json={}, headers={HEADER: KEY})   # after logging in: it runs
    assert ok.status_code == 200 and calls["order"] == 2
    assert [(r.state, r.status_code) for r in await _rows(sqlite_sessionmaker)] == [("done", 200)]


async def test_only_order_requests_are_keyed(sqlite_sessionmaker: Any) -> None:
    calls: dict[str, int] = {}
    async with _client(sqlite_sessionmaker, calls) as c:
        await c.post("/api/v1/trade/preview", json={}, headers={HEADER: KEY})
        await c.post("/api/v1/trade/preview", json={}, headers={HEADER: KEY})
    assert calls["preview"] == 2 and await _rows(sqlite_sessionmaker) == []
    assert is_order_request("POST", "/fx-volatility-trading-system/api/v1/trades/12/close")
    assert is_order_request("DELETE", "/api/v1/orders/55")
    assert not is_order_request("GET", "/api/v1/orders")
    assert len(ORDER_ROUTES) == 6


def test_every_order_endpoint_of_the_browser_sends_a_key() -> None:
    """Close, trade close, spot and manual ticket, both cancels, submit."""
    src = (ROOT / "frontend/src/api/endpoints.ts").read_text(encoding="utf-8")
    for needle in (
        r'"/api/v1/orders", body, once\(\)',
        r"/api/v1/orders/\$\{ibOrderId\}`, once\(\)",
        r"/api/v1/positions/\$\{positionId\}/close`, \{ qty \}, once\(\)",
        r"/api/v1/trades/\$\{tradeId\}/close`, \{\}, once\(\)",
        r"/cancel`, \{\},\s+orderId != null \? \{ query: \{ order_id: orderId \}, \.\.\.once\(\) \} : once\(\)",
        r"execution_mode: executionMode,\s+\}, once\(\)",
    ):
        assert re.search(needle, src), needle
    main = (ROOT / "src/api/main.py").read_text(encoding="utf-8")
    assert "app.add_middleware(IdempotencyMiddleware" in main
