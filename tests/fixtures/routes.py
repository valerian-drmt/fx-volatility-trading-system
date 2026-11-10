"""Materialised API routes of a FastAPI app.

FastAPI >= 0.160 includes routers lazily: ``app.routes`` lists an
``_IncludedRouter`` per ``include_router`` call instead of the routes
themselves (#303). The guard tests that walk every route go through
``iter_route_contexts`` so they keep seeing the real ``APIRoute`` objects
with their full path and their ``dependencies``.
"""
from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute, iter_route_contexts


def api_routes(app: Any) -> list[APIRoute]:
    return [ctx.route for ctx in iter_route_contexts(app.routes) if isinstance(ctx.route, APIRoute)]


def route_dependency_calls(route: APIRoute) -> list[Any]:
    """Every dependency callable attached to the route (decorator ``dependencies=``
    and parameter ``Depends``), whichever FastAPI stores it on."""
    calls = [getattr(d, "dependency", None) for d in route.dependencies]
    calls += [getattr(d, "call", None) for d in route.dependant.dependencies]
    return [c for c in calls if c is not None]
