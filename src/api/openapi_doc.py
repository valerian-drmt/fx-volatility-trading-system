"""OpenAPI post-processing (#352, audit 11#10).

FastAPI documents 200 and 422 only. This adds, per operation:

* the security requirement and a 401 for every route behind the trader cookie
  (``require_write``), declared once as the ``traderCookie`` scheme;
* the error codes the endpoint itself raises (read from its source: an
  ``HTTPException`` with a literal status), so the list follows the code;
* 429 everywhere: slowapi's default per-IP limit applies to every route.

Every error body is FastAPI's ``{"detail": ...}``; ``ErrorResponse`` names it.
"""
from __future__ import annotations

import inspect
import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

ERROR_TEXT = {
    400: "Request rejected by a business rule (the detail says which).",
    401: "No valid trader cookie: sign in through `POST /api/v1/auth/login`.",
    404: "The referenced object does not exist.",
    409: "Conflicts with the current state (already submitted, in flight, duplicate).",
    422: "Validation error.",
    429: "Rate limited (per client IP; login also locks a username after repeated failures). See `Retry-After`.",
    500: "Unexpected server error; the body carries a trace id, never internals.",
    503: "A dependency is unavailable (execution engine, database, cache, IB session).",
    504: "The execution engine did not answer in time.",
}
_RAISED = re.compile(r"HTTPException\(\s*(?:status_code\s*=\s*)?(?:status\.HTTP_)?(\d{3})")
_ERROR_SCHEMA = {
    "title": "ErrorResponse",
    "type": "object",
    "properties": {"detail": {"title": "Detail", "description": "A message, or a structured object for validation and gate refusals."}},
    "required": ["detail"],
}


def _raised_codes(route: APIRoute) -> set[int]:
    try:
        src = inspect.getsource(inspect.unwrap(route.endpoint))
    except (OSError, TypeError):
        return set()
    return {int(c) for c in _RAISED.findall(src)}


def _gated(route: APIRoute) -> bool:
    from api.auth import require_write

    calls = [getattr(d, "dependency", None) for d in route.dependencies]
    calls += [getattr(d, "call", None) for d in route.dependant.dependencies]
    return require_write in calls


def install(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("schemas", {})["ErrorResponse"] = _ERROR_SCHEMA
        components.setdefault("securitySchemes", {})["traderCookie"] = {
            "type": "apiKey",
            "in": "cookie",
            "name": "fxvol_auth",
            "description": "Set by `POST /api/v1/auth/login` (httpOnly, Secure in prod). Reads are public; writes, `/dev` and config need it.",
        }
        ref = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}}
        for ctx in iter_route_contexts(app.routes):
            route = ctx.route
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            item = schema["paths"].get(route.path_format, {})
            codes = _raised_codes(route) | {429}
            gated = _gated(route)
            if gated:
                codes.add(401)
            for method in route.methods:
                op = item.get(method.lower())
                if op is None:
                    continue
                if gated:
                    op["security"] = [{"traderCookie": []}]
                responses = op.setdefault("responses", {})
                for code in sorted(codes):
                    responses.setdefault(str(code), {"description": ERROR_TEXT.get(code, "Error."), **ref})
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
