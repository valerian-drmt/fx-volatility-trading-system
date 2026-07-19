"""Fail-closed guard: every /api/v1/dev route must require write auth.

The dev console dumps DB tables, Redis values and container logs — it is
gated at router level with ``Depends(require_write)``. This test asserts
the gate on every route under the prefix so a future addition (or a
refactor dropping the router-level dependency) cannot silently regress.
"""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.auth import require_write
from api.main import app

pytestmark = pytest.mark.unit


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


def test_dev_prefix_has_routes():
    # Guard the guard: an empty list would make the gating test pass vacuously.
    assert len(_dev_routes()) >= 10


def test_every_dev_route_requires_write_auth():
    offenders = [r.path for r in _dev_routes() if not _is_gated(r)]
    assert not offenders, f"ungated /dev routes (require_write missing): {offenders}"


def test_dev_endpoint_returns_401_without_cookie():
    client = TestClient(app)
    resp = client.get("/api/v1/dev/engines")
    assert resp.status_code == 401
