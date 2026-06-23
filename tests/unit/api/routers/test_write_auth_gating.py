"""Fail-closed guard: every mutating route must require auth.

Reads stay public; writes (POST/PUT/PATCH/DELETE) must carry
``Depends(require_write)`` — except a small allowlist of public-by-design
writes (pure pricing calculators + the auth handshake itself). A new ungated
write endpoint makes this test fail, so the write boundary can't silently
regress.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from api.auth import require_write
from api.main import app

pytestmark = pytest.mark.unit

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Public-by-design writes: BS calculators (read-only compute, POST for the body)
# and the auth login/logout handshake. Everything else must be gated.
PUBLIC_WRITE_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/price",
    "/api/v1/greeks",
    "/api/v1/iv",
}


def _is_gated(route: APIRoute) -> bool:
    return any(getattr(d, "dependency", None) is require_write for d in route.dependencies)


def _write_routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and (r.methods & WRITE_METHODS)
    ]


def test_every_write_route_requires_auth_except_allowlist():
    offenders = [
        f"{sorted(r.methods & WRITE_METHODS)} {r.path}"
        for r in _write_routes()
        if r.path not in PUBLIC_WRITE_PATHS and not _is_gated(r)
    ]
    assert not offenders, f"ungated write routes (add Depends(require_write)): {offenders}"


def test_public_writes_stay_public():
    # Over-gating the calculators would break the read-only public desk.
    over_gated = [
        r.path
        for r in _write_routes()
        if r.path in PUBLIC_WRITE_PATHS and _is_gated(r)
    ]
    assert not over_gated, f"these writes must stay public: {over_gated}"


def test_known_critical_writes_are_gated():
    # Spot-check the order-sending / config paths explicitly.
    must_be_gated = {
        ("POST", "/api/v1/orders"),
        ("DELETE", "/api/v1/orders/{order_id}"),
        ("POST", "/api/v1/trade/submit"),
        ("PUT", "/api/v1/admin/config"),
        ("POST", "/api/v1/trades/{trade_id}/close"),
    }
    found = {
        (m, r.path)
        for r in _write_routes()
        for m in (r.methods & WRITE_METHODS)
        if _is_gated(r)
    }
    missing = must_be_gated - found
    assert not missing, f"expected gated but not found/gated: {missing}"
