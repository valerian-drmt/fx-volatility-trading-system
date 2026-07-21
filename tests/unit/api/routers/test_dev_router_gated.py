"""Contract: the /api/v1/dev routes split into a PUBLIC showcase and a
write-gated TRADER debug set (see releases/PLAN_trader_vs_public.md).

  * PUBLIC  (no auth)      : stack / engines / cycle-progress / db-schema /
    migrations — status + topology + schema only, no data/secrets/host internals.
  * TRADER  (require_write): tables (DB Explorer) / logs / redis / hardware /
    container metrics — the debug tools.

This test fail-closes: every dev route must be classified, every TRADER route
must carry require_write, and no PUBLIC route may. A new dev route that isn't
listed here fails the test, forcing an explicit public/trader decision.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.auth import require_write
from api.main import app

pytestmark = pytest.mark.unit

PUBLIC = {
    "/api/v1/dev/stack",
    "/api/v1/dev/engines",
    "/api/v1/dev/cycle-progress",
    "/api/v1/dev/db-schema",
    "/api/v1/dev/migrations",
    "/api/v1/dev/migrations/{rev_id}",
}
TRADER = {
    "/api/v1/dev/redis/keys",
    "/api/v1/dev/redis/value",
    "/api/v1/dev/tables",
    "/api/v1/dev/tables/{name}",
    "/api/v1/dev/logs/containers",
    "/api/v1/dev/logs/query",
    "/api/v1/dev/hardware",
    "/api/v1/dev/containers/metrics",
}


def _dev_routes() -> list[APIRoute]:
    return [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1/dev")
    ]


def _is_gated(route: APIRoute) -> bool:
    return any(
        getattr(d, "dependency", None) is require_write for d in route.dependencies
    )


def test_every_dev_route_is_classified():
    # Fail-closed: an unlisted dev route must be explicitly assigned public/trader.
    paths = {r.path for r in _dev_routes()}
    assert paths, "no /dev routes found"
    unclassified = paths - PUBLIC - TRADER
    assert not unclassified, f"unclassified /dev routes: {unclassified}"


def test_trader_routes_require_write():
    offenders = [r.path for r in _dev_routes() if r.path in TRADER and not _is_gated(r)]
    assert not offenders, f"TRADER routes missing require_write: {offenders}"


def test_public_routes_are_not_gated():
    offenders = [r.path for r in _dev_routes() if r.path in PUBLIC and _is_gated(r)]
    assert not offenders, f"PUBLIC routes wrongly gated: {offenders}"


def test_trader_endpoint_returns_401_without_cookie():
    resp = TestClient(app).get("/api/v1/dev/tables")
    assert resp.status_code == 401


def test_public_endpoint_is_not_401_without_cookie():
    # Public endpoints must not demand auth (may 200 or 5xx on missing deps in
    # unit context, but never 401). raise_server_exceptions=False turns an
    # unhandled 500 (no DB/redis in unit) into a response instead of re-raising.
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/dev/db-schema")
    assert resp.status_code != 401
