"""Typed, bounded bodies on every write route (#300, audit 01#14) and the
timing-safe login check (01#3)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.config import Settings
from api.routers.trade import LegSpec, PreviewRequest, SubmitBody
from api.schemas.auth import LoginBody
from api.schemas.orders import ClosePositionBody, PlaceOrderBody

pytestmark = pytest.mark.unit


def test_submit_body_bounds() -> None:
    assert SubmitBody(preview_id="tp_abc").execution_mode == "mock"
    with pytest.raises(ValidationError):
        SubmitBody(preview_id="tp_abc", execution_mode="paper")
    with pytest.raises(ValidationError):
        SubmitBody(preview_id="x" * 65)
    with pytest.raises(ValidationError):
        SubmitBody(preview_id="")


def test_preview_request_bounds() -> None:
    assert PreviewRequest(structure_type="straddle", qty=5).qty == 5
    with pytest.raises(ValidationError):
        PreviewRequest(qty=0)
    with pytest.raises(ValidationError):
        PreviewRequest(qty=1001)
    with pytest.raises(ValidationError):
        PreviewRequest(strike_override=-1.0)
    with pytest.raises(ValidationError):
        PreviewRequest(strike_override=250.0)
    with pytest.raises(ValidationError):
        PreviewRequest(tenor="x" * 9)
    with pytest.raises(ValidationError):
        LegSpec(contract_type="call", side="BUY", tenor="3M", qty_factor=0)
    with pytest.raises(ValidationError):
        LegSpec(contract_type="put", side="SELL", tenor="3M", strike=0.0)


def test_place_order_body_mirrors_the_engine_bounds() -> None:
    ok = PlaceOrderBody(symbol="EUR", sec_type="CASH", side="BUY", qty=100_000)
    assert ok.limit_price is None
    with pytest.raises(ValidationError, match="5,000,000"):
        PlaceOrderBody(symbol="EUR", sec_type="CASH", side="BUY", qty=6_000_000)
    with pytest.raises(ValidationError, match="limit_price is required"):
        PlaceOrderBody(symbol="EUR", sec_type="FUT", side="BUY", qty=1)
    with pytest.raises(ValidationError, match="1000 contracts"):
        PlaceOrderBody(symbol="EUR", sec_type="FUT", side="BUY", qty=1001, limit_price=1.1)
    with pytest.raises(ValidationError):
        PlaceOrderBody(symbol="EUR", sec_type="FOP", side="BUY", qty=1, limit_price=0.01, expiry="2026-12")
    with pytest.raises(ValidationError):
        ClosePositionBody(limit_price=0)


def test_orders_route_refuses_a_bad_body_before_forwarding(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.auth import require_write
    from api.routers import orders as orders_mod

    forwarded: list[tuple[str, str, dict | None]] = []

    async def _fake_forward(method: str, path: str, json: dict | None = None) -> dict:
        forwarded.append((method, path, json))
        return {"ok": True}

    monkeypatch.setattr(orders_mod, "_forward", _fake_forward)
    app = FastAPI()
    app.include_router(orders_mod.router)
    app.dependency_overrides[require_write] = lambda: {"sub": "trader"}
    # the route takes a session for the spot gate (#491); a futures order does not use it
    from api.dependencies import get_db_session
    app.dependency_overrides[get_db_session] = lambda: None
    c = TestClient(app)

    r = c.post("/api/v1/orders", json={"symbol": "EUR", "sec_type": "FUT", "side": "BUY", "qty": 5})
    assert r.status_code == 422 and forwarded == []
    r = c.post("/api/v1/orders", json={"symbol": "EUR", "sec_type": "FUT", "side": "BUY", "qty": 5, "limit_price": 1.1})
    assert r.status_code == 200
    assert forwarded[0][1] == "/internal/orders" and forwarded[0][2]["qty"] == 5
    r = c.post("/api/v1/exec/positions/7/close", json={"limit_price": -1})
    assert r.status_code == 422
    # a valid body no longer reaches the engine: the route is switched off (#491)
    r = c.post("/api/v1/exec/positions/7/close", json={"limit_price": 0.01})
    assert r.status_code == 410 and len(forwarded) == 1


def test_login_body_is_bounded_and_a_wrong_username_is_a_401(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        LoginBody(username="x" * 65, password="p")
    from api import auth
    from api.config import get_settings
    from api.routers.auth import router as auth_router

    app = FastAPI()
    app.include_router(auth_router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        AUTH_SECRET="test-secret", AUTH_USERNAME="trader", AUTH_SALT="salt",
        AUTH_PASSWORD_HASH=auth.hash_password("hunter2", "salt"), AUTH_COOKIE_SECURE=False,
    )
    c = TestClient(app)
    assert c.post("/api/v1/auth/login", json={"username": "trader", "password": "x" * 300}).status_code == 422
    assert c.post("/api/v1/auth/login", json={"username": "", "password": "hunter2"}).status_code == 422
    assert c.post("/api/v1/auth/login", json={"username": "traderx", "password": "hunter2"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"username": "trader", "password": "hunter2"}).status_code == 200


def test_prod_boot_guard_refuses_the_insecure_defaults() -> None:
    strong = "s" * 40
    with pytest.raises(ValueError, match="AUTH_PASSWORD_HASH"):
        Settings(ENV="prod", AUTH_SECRET=strong, AUTH_COOKIE_SECURE=True, DATABASE_URL="postgresql+asyncpg://u:p@h/db")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(ENV="prod", AUTH_SECRET=strong, AUTH_COOKIE_SECURE=True, AUTH_PASSWORD_HASH="ab" * 32, DATABASE_URL="")
    ok = Settings(ENV="prod", AUTH_SECRET=strong, AUTH_COOKIE_SECURE=True, AUTH_PASSWORD_HASH="ab" * 32,
                  DATABASE_URL="postgresql+asyncpg://u:p@h/db")
    assert ok.ENV == "prod"
