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
from api.routers import admin as admin_router
from api.routers import analytics as analytics_router
from api.routers import cockpit as cockpit_router
from api.routers import dev as dev_router
from api.routers import health as health_router
from api.routers import orders as orders_router
from api.routers import portfolio as portfolio_router
from api.routers import portfolio_panel as portfolio_panel_router
from api.routers import positions as positions_router
from api.routers import pricing as pricing_router
from api.routers import regime as regime_router
from api.routers import signals as signals_router
from api.routers import trade as trade_router
from api.routers import trades as trades_router
from api.routers import vol as vol_router
from api.routers import ws as ws_router
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

    # Background schedulers (each spawns a task that sleeps a startup delay
    # before its first cycle, so app/TestClient boot is never blocked).
    from api.orchestration.pca_refit_scheduler import build_pca_refit_scheduler
    from api.orchestration.trade_preview_expirer import build_trade_preview_expirer

    pca_refit_scheduler = build_pca_refit_scheduler()
    await pca_refit_scheduler.start()
    app.state.pca_refit_scheduler = pca_refit_scheduler

    trade_preview_expirer = build_trade_preview_expirer()
    await trade_preview_expirer.start()
    app.state.trade_preview_expirer = trade_preview_expirer

    log.info("api_startup", redis_url=settings.redis_url)
    try:
        yield
    finally:
        await pca_refit_scheduler.stop()
        await trade_preview_expirer.stop()
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
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
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
    app.include_router(portfolio_panel_router.router)
    app.include_router(analytics_router.router)
    app.include_router(regime_router.router)
    app.include_router(signals_router.router)
    app.include_router(trade_router.router)
    app.include_router(orders_router.router)
    app.include_router(positions_router.router)
    app.include_router(admin_router.router)
    app.include_router(cockpit_router.router)
    app.include_router(dev_router.router)
    app.include_router(trades_router.router)
    app.include_router(ws_router.router)
    # Remaining planned : orders router (PR #5b) — requires OrderExecutor wiring.
    return app


app = create_app()
