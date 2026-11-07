"""WebSocket Origin allowlist (#299, audit 01#2).

A browser sends ``Origin`` on every WebSocket upgrade and cannot forge it, so
checking it stops a page on another site from opening the desk's streams in
a visitor's browser. A request with no ``Origin`` (curl, a script, a test
client) is not a browser and is allowed: the streams are public reads.
"""
from __future__ import annotations

from api.config import Settings, get_settings


def _norm(origin: str) -> str:
    return origin.strip().rstrip("/").lower()


def allowed_origins(settings: Settings | None = None) -> set[str]:
    s = settings or get_settings()
    return {_norm(o) for o in [*s.cors_origins, *s.WS_ALLOWED_ORIGINS] if o}


def origin_allowed(origin: str | None, allowed: set[str]) -> bool:
    if origin is None:
        return True
    return _norm(origin) in allowed
