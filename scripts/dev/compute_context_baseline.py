"""One-shot CLI : populate ``vol_features_context_baseline``.

Thin wrapper over ``api.orchestration.baseline_compute.compute_baseline`` —
the actual logic lives there so the api scheduler can import it without a
sys.path hack into ``scripts/``.

Usage :
    docker compose exec api python scripts/dev/compute_context_baseline.py
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if db_url is None:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    from api.orchestration.baseline_compute import compute_baseline

    engine = create_async_engine(db_url)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as db:
        report = await compute_baseline(db)
    await engine.dispose()
    print(
        f"vol_features_context_baseline : "
        f"{report['valid']} valid · {report['insufficient']} insufficient · "
        f"{report['total_cells']} cells total"
    )


if __name__ == "__main__":
    asyncio.run(_main())
