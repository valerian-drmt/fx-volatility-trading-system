"""Auth endpoints — login (set the httpOnly cookie), logout (clear it), me (status).

NOTE: no ``from __future__ import annotations`` here — slowapi's @limiter.limit
wrapper exposes ``__globals__`` from the slowapi module, where postponed
annotation strings (LoginBody, SettingsDep) cannot be resolved; FastAPI would
then demote the body/DI params to required query params (422 on every login).
"""
import hmac
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from starlette.requests import Request

from api.auth import COOKIE_NAME, issue_token, lockout, verify_password, verify_token
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
    # Lockout first (#296): a locked username is refused before any
    # credential work, with the time left in Retry-After.
    remaining = lockout.locked_for(body.username)
    if remaining > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed logins; try again later",
            headers={"Retry-After": str(int(remaining) + 1)},
        )
    # Both checks always run, in constant time, so neither the username nor
    # the password branch leaks through timing (#300, audit 01#3).
    user_ok = hmac.compare_digest(body.username.encode(), settings.auth_username.encode())
    ok = user_ok & verify_password(
        body.password, settings.auth_salt, settings.auth_password_hash
    )
    if not ok:
        lockout.record_failure(
            body.username, threshold=settings.AUTH_LOCKOUT_THRESHOLD, seconds=settings.AUTH_LOCKOUT_SECONDS,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    lockout.reset(body.username)
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
