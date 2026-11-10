"""Shared secret between the api and the execution engine (#300, audit 01#1).

Every ``/internal/*`` route of the execution engine places or cancels orders.
They sit on the private compose network, but a network boundary is not an
auth boundary: any container on ``fxvol-internal`` could call them. The api
sends ``X-Internal-Token`` on every call and the engine refuses a request
without it. The token comes from ``EXECUTION_INTERNAL_TOKEN`` on both sides:
``/fxvol/prod/EXECUTION_INTERNAL_TOKEN`` in SSM, or a host-generated value
rendered by ``infrastructure/ec2/remote-deploy.sh`` (``docs/ops/secrets.md``);
the compose dev default is ``fxvol-dev-internal``.

Fail closed: an engine with no token configured answers 503 on every
internal route, never 200.
"""
from __future__ import annotations

import hmac
import os

INTERNAL_TOKEN_HEADER = "X-Internal-Token"
INTERNAL_TOKEN_ENV = "EXECUTION_INTERNAL_TOKEN"


def internal_token() -> str:
    return os.environ.get(INTERNAL_TOKEN_ENV, "").strip()


def token_matches(presented: str | None) -> bool:
    """Constant-time comparison; False when no token is configured."""
    expected = internal_token()
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def internal_headers() -> dict[str, str]:
    """Headers for an api -> execution-engine call: the trace headers plus the
    internal token (omitted when unset, so the engine's 503 names the cause)."""
    from shared.trace import trace_headers

    headers = trace_headers()
    token = internal_token()
    if token:
        headers[INTERNAL_TOKEN_HEADER] = token
    return headers
