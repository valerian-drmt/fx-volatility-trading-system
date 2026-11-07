"""Single-trader auth boundary.

Reads stay public; write endpoints depend on :func:`require_write`, which 401s
unless the request carries a valid, unexpired auth cookie.

The cookie is an HMAC-signed token — ``b64url(payload).b64url(hmac_sha256)`` —
not an RFC JWT, deliberately: it needs no extra dependency and the security
properties (tamper-proof, expiring) are equivalent for one operator. Passwords
are checked against a pbkdf2_hmac(sha256) hash held in settings (from SSM in
prod), so no plaintext credential is ever stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Annotated, Any

from fastapi import Cookie, Depends, HTTPException, status

from api.config import Settings, get_settings

COOKIE_NAME = "fxvol_auth"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes, secret: str) -> str:
    return _b64e(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def issue_token(sub: str, secret: str, ttl_s: int) -> str:
    """Mint a signed cookie token for ``sub`` valid for ``ttl_s`` seconds."""
    body = json.dumps({"sub": sub, "exp": int(time.time()) + ttl_s}, separators=(",", ":")).encode()
    return f"{_b64e(body)}.{_sign(body, secret)}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """Return the claims if the token's signature is valid and unexpired, else None."""
    try:
        body_b64, sig = token.split(".", 1)
        body = _b64d(body_b64)
        if not hmac.compare_digest(sig, _sign(body, secret)):
            return None
        claims = json.loads(body)
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        return claims  # type: ignore[no-any-return]
    except Exception:
        return None


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """Constant-time check. Empty ``expected_hash`` (unprovisioned) → always False."""
    if not expected_hash:
        return False
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


class LoginLockout:
    """Per-username failure counter (#296, audit 01#4). The per-IP throttle
    on ``/login`` is keyed on what nginx forwards; a lockout on the username
    holds whatever address the attempts come from. In-process state: the api
    runs one uvicorn worker, and a restart clears it (an operator who is
    locked out can also wait ``AUTH_LOCKOUT_SECONDS``)."""

    _MAX_TRACKED = 1000

    def __init__(self) -> None:
        self._state: dict[str, tuple[int, float]] = {}   # username -> (failures, locked_until)

    def locked_for(self, username: str, now: float | None = None) -> float:
        """Seconds left on the lock, 0 when the username may try."""
        now = time.time() if now is None else now
        _, until = self._state.get(username, (0, 0.0))
        return max(0.0, until - now)

    def record_failure(self, username: str, *, threshold: int, seconds: int, now: float | None = None) -> float:
        """Count one failure; returns the lock length when the threshold is reached, else 0."""
        now = time.time() if now is None else now
        failures, until = self._state.get(username, (0, 0.0))
        failures += 1
        if failures >= threshold:
            until = now + seconds
            failures = 0
        if len(self._state) >= self._MAX_TRACKED and username not in self._state:
            oldest = min(self._state, key=lambda k: self._state[k][1])
            self._state.pop(oldest, None)
        self._state[username] = (failures, until)
        return max(0.0, until - now)

    def reset(self, username: str) -> None:
        self._state.pop(username, None)

    def reset_all(self) -> None:
        self._state.clear()


lockout = LoginLockout()


def require_write(
    settings: Annotated[Settings, Depends(get_settings)],
    fxvol_auth: Annotated[str | None, Cookie()] = None,
) -> dict[str, Any]:
    """FastAPI dependency gating write endpoints — 401 without a valid auth cookie.

    Attach to a write route with ``Depends(require_write)`` (or the router's
    ``dependencies=[Depends(require_write)]``).
    """
    claims = verify_token(fxvol_auth, settings.auth_secret) if fxvol_auth else None
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return claims
