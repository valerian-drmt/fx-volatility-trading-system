"""Auth endpoints — login (set the httpOnly cookie), logout (clear it), me (status).

NOTE: no ``from __future__ import annotations`` here — slowapi's @limiter.limit
wrapper exposes ``__globals__`` from the slowapi module, where postponed
annotation strings (LoginBody, SettingsDep) cannot be resolved; FastAPI would
then demote the body/DI params to required query params (422 on every login).
"""
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from starlette.requests import Request

from api.auth import COOKIE_NAME, issue_token, verify_password, verify_token
from api.config import Settings, get_settings
from api.middleware.rate_limit import limiter
from api.schemas.auth import AuthStatus, LoginBody

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.post("/login", response_model=AuthStatus)
@limiter.limit("5/minute")  # strict per-IP brute-force throttle on the credential check
def login(
    request: Request, body: LoginBody, response: Response, settings: SettingsDep
) -> AuthStatus:
    """Verify the single-trader credentials and set the auth cookie."""
    ok = body.username == settings.auth_username and verify_password(
        body.password, settings.auth_salt, settings.auth_password_hash
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    # Mint from the server-side username — just checked equal to body.username,
    # so identical semantics, but no client-supplied bytes can ever reach the
    # Set-Cookie header (CWE-020).
    token = issue_token(settings.auth_username, settings.auth_secret, settings.auth_ttl_seconds)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.auth_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return AuthStatus(authenticated=True)


@router.post("/logout", response_model=AuthStatus)
def logout(response: Response) -> AuthStatus:
    response.delete_cookie(COOKIE_NAME, path="/")
    return AuthStatus(authenticated=False)


@router.get("/me", response_model=AuthStatus)
def me(settings: SettingsDep, fxvol_auth: Annotated[str | None, Cookie()] = None) -> AuthStatus:
    """Cheap status check for the frontend (drives the login UI)."""
    valid = bool(fxvol_auth and verify_token(fxvol_auth, settings.auth_secret))
    return AuthStatus(authenticated=valid)
