"""FastAPI entrypoint — wires middleware, lifespan, routers, WebSocket bridge."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis import asyncio as aioredis
from slowapi.errors import RateLimitExceeded
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import Response

from api.config import get_settings
from api.dependencies import set_redis_client
from api.middleware.logging import AccessLogMiddleware, configure_logging
from api.middleware.rate_limit import build_limiter, rate_limit_exceeded_handler
from api.middleware.timing import TimingMiddleware
from api.routers import analytics as analytics_router
from api.routers import health as health_router
from api.routers import portfolio as portfolio_router
from api.routers import pricing as pricing_router
from api.routers import vol as vol_router
from api.ws.connection_manager import ConnectionManager
from api.ws.redis_bridge import redis_to_ws_bridge


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open Redis pool + ws bridge at startup, cancel + dispose on shutdown."""
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger("api.lifespan")

    client = aioredis.from_url(
        settings.redis_url, max_connections=50, decode_responses=True
    )
    set_redis_client(client)

    manager = ConnectionManager()
    app.state.ws_manager = manager
    bridge_task = asyncio.create_task(redis_to_ws_bridge(client, manager))

    log.info("api_startup", redis_url=settings.redis_url)
    try:
        yield
    finally:
        bridge_task.cancel()
        # Await the task so its cleanup (pubsub.aclose) runs. CancelledError
        # is expected and swallowed — any other exception would be logged.
        try:
            await bridge_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("bridge_task_crashed_on_shutdown")
        await client.aclose()
        log.info("api_shutdown")


def create_app() -> FastAPI:
    """Factory — used by uvicorn + by tests (no side effect at import)."""
    settings = get_settings()
    app = FastAPI(
        title="FXVol API",
        version="0.4.0",
        lifespan=lifespan,
    )

    limiter = build_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(TimingMiddleware)
    app.add_middleware(AccessLogMiddleware)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health_router.router)
    app.include_router(pricing_router.router)
    app.include_router(vol_router.router)
    app.include_router(portfolio_router.router)
    app.include_router(analytics_router.router)
    # Next routers : orders (#5b), WebSocket (#7 + #8)
    return app


app = create_app()
