"""Browser latency beacon (#370, hot/cold T6).

The desk posts, for 1 push in 10, how long a pushed row took to reach the
screen: ``publish_to_screen_ms`` (server publish stamp to render; includes
the browser clock skew, so read it as a trend) and ``receive_to_screen_ms``
(WebSocket receive to render; pure client time). Both land in
``desk_publish_to_screen_seconds{channel,kind}`` on the api's ``/metrics`` so
the "Desk fluidity" Grafana panel can draw the whole path next to the two
engine histograms.

Public, no auth: it is a measurement, not a mutation. Values are clamped,
labels are whitelisted, and the route is rate limited per IP.

NOTE: no ``from __future__ import annotations`` here — slowapi's
``@limiter.limit`` wraps the endpoint and the body model must resolve eagerly
(same constraint as ``api.routers.auth``).
"""
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.middleware.rate_limit import limiter
from api.schemas import route_models as rm

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

MAX_MS = 60_000.0


class BeaconBody(BaseModel):
    channel: Literal["positions", "account", "spot_orders", "orders", "ticks"]
    publish_to_screen_ms: float | None = Field(None, ge=0, le=MAX_MS)
    receive_to_screen_ms: float | None = Field(None, ge=0, le=MAX_MS)


def record_beacon(body: BeaconBody) -> dict[str, int]:
    """Observe the non-null timings. Split out so the router test needs no app."""
    from shared.observability import desk_publish_to_screen_seconds

    n = 0
    for kind, value in (("publish", body.publish_to_screen_ms), ("receive", body.receive_to_screen_ms)):
        if value is None:
            continue
        desk_publish_to_screen_seconds.labels(channel=body.channel, kind=kind).observe(value / 1000.0)
        n += 1
    return {"recorded": n}


@router.post("/beacon", response_model=rm.BeaconAck, response_model_exclude_unset=True, status_code=202)
@limiter.limit("120/minute")
async def beacon(request: Request, body: BeaconBody) -> dict[str, int]:
    """Browser latency beacon: one timing sample from the desk, clamped and whitelisted, rate limited per IP. Not a mutation."""
    return record_beacon(body)
