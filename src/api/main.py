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
from api.routers import positions as positions_router
from api.routers import pricing as pricing_router
from api.routers import regime as regime_router
from api.routers import signals as signals_router
from api.routers import trade as trade_router
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
    events_scheduler = _build_events_scheduler()
    await events_scheduler.start()
    app.state.events_scheduler = events_scheduler

    from api.orchestration.pca_refit_scheduler import build_pca_refit_scheduler
    pca_scheduler = build_pca_refit_scheduler()
    await pca_scheduler.start()
    app.state.pca_refit_scheduler = pca_scheduler

    from api.orchestration.trade_preview_expirer import build_trade_preview_expirer
    tp_expirer = build_trade_preview_expirer()
    await tp_expirer.start()
    app.state.trade_preview_expirer = tp_expirer

    from api.orchestration.position_monitor import build_position_monitor_scheduler
    position_monitor = build_position_monitor_scheduler()
    await position_monitor.start()
    app.state.position_monitor = position_monitor

    # R9 : api redevient pure stateless. L'IB connection + le sync loop
    # vivent désormais dans le container `execution-engine` (cf. routers/
    # orders.py qui forwarde via httpx).

    # One-shot seed : si vol_config n'a que la row initiale (version=1,
    # config={}), pousse une version 2 avec le dump complet des defaults.
    # Permet de voir les params dans la table au lieu d'avoir un JSONB
    # vide qui force la lecture côté code.
    try:
        from sqlalchemy import text

        from api.orchestration.config_service import get_current, update
        from core.config import VolTradingConfig
        from persistence.db import get_sessionmaker
        async with get_sessionmaker()() as db:
            current = await get_current(db)
            if current.version <= 1 and current.config.model_dump() == VolTradingConfig().model_dump():
                # Seed only if still on the initial empty placeholder row.
                empty_check_row = (await db.execute(
                    text("SELECT config FROM vol_engine_config WHERE version=1")
                )).scalar_one_or_none()
                if empty_check_row in (None, {}, "{}"):
                    await update(
                        db, client,
                        patch=VolTradingConfig().model_dump(),
                        user="system",
                        comment="default config seed (api lifespan)",
                    )
                    log.info("vol_config_seeded version=2 with full defaults")
    except Exception:
        log.exception("vol_config_seed_failed")

    log.info("api_startup", redis_url=settings.redis_url)
    try:
        yield
    finally:
        bridge_task.cancel()
        await events_scheduler.stop()
        await pca_scheduler.stop()
        await tp_expirer.stop()
        await position_monitor.stop()
        try:
            await bridge_task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("bridge_task_crashed_on_shutdown")
        await client.aclose()
        log.info("api_shutdown")


def _build_events_scheduler():
    """Wire the events pipeline (cf. docs/.../events_pipeline_spec.md §5).

    FRED only is implemented today (covers ~60% of high-impact events).
    The 6 other sources are stubbed in ``sources/stubs.py`` and contribute
    0 events with a NotImplementedError caught by the scheduler — they will
    be enabled one by one as their parsers are written.
    """
    import os as _os

    from api.orchestration.events.deduplicator import EventDeduplicator
    from api.orchestration.events.repository import EventsRepository
    from api.orchestration.events.scheduler import EventsScheduler
    from api.orchestration.events.sources.boe import BoESource
    from api.orchestration.events.sources.ecb import ECBSource
    from api.orchestration.events.sources.eurostat import EurostatSource
    from api.orchestration.events.sources.fomc import FOMCSource
    from api.orchestration.events.sources.fred import FREDSource
    from api.orchestration.events.sources.ons import ONSSource
    from persistence.db import get_sessionmaker

    sources: list = []
    fred_key = _os.environ.get("FRED_API_KEY") or ""
    if fred_key:
        sources.append(FREDSource(api_key=fred_key))
    sources.extend([
        ECBSource(), BoESource(), FOMCSource(),
        EurostatSource(), ONSSource(),
    ])

    return EventsScheduler(
        sources=sources,
        repository=EventsRepository(get_sessionmaker()),
        deduplicator=EventDeduplicator(),
    )


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
    app.include_router(analytics_router.router)
    app.include_router(cockpit_router.router)
    app.include_router(admin_router.router)
    app.include_router(dev_router.router)
    app.include_router(orders_router.router)
    app.include_router(regime_router.router)
    app.include_router(signals_router.router)
    app.include_router(trade_router.router)
    app.include_router(positions_router.router)
    app.include_router(ws_router.router)
    # Remaining planned : orders router (PR #5b) — requires OrderExecutor wiring.
    return app


app = create_app()
