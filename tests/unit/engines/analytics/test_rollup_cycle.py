"""The analytics engine's rollup loop (#368): one pass refreshes
``portfolio_daily`` on a real (SQLite) session and asks for the attribution
of every warm lookback with the Redis spot fallback; a failing step is
reported, never raised."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _Redis:
    async def get(self, name: str) -> Any:
        return None


async def _seed(maker: Any) -> None:
    from persistence.models import AccountHistory
    now = datetime.now(UTC)
    async with maker() as db:
        for h in (30, 20, 4, 1):
            db.add(AccountHistory(timestamp=now - timedelta(hours=h), net_liq_usd=Decimal("100000") + h))
        await db.commit()


async def test_rollup_cycle_refreshes_daily_rows_and_requests_every_lookback(
    sqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import engines.analytics.engine as mod
    from persistence.rollups import DEFAULT_LOOKBACKS_H, read_portfolio_daily

    await _seed(sqlite_sessionmaker)
    seen: dict[str, Any] = {}

    async def _fake_refresh(db: Any, *, now: datetime, lookbacks=DEFAULT_LOOKBACKS_H, spot_fallback_for=None) -> dict:
        seen["lookbacks"] = tuple(lookbacks)
        seen["fallback"] = await spot_fallback_for(now - timedelta(hours=24)) if spot_fallback_for else "none"
        return {"lookbacks": list(lookbacks), "n_rows": 0}

    monkeypatch.setattr(mod, "refresh_attribution", _fake_refresh)
    engine = mod.AnalyticsEngine(_Redis(), sessionmaker=sqlite_sessionmaker, rollup_interval_s=1)
    out = await engine.run_rollup_cycle()

    assert out["portfolio_daily"]["rows"] >= 1 and "attribution" in out
    assert seen["lookbacks"] == DEFAULT_LOOKBACKS_H and seen["fallback"] == (None, None)
    async with sqlite_sessionmaker() as db:
        rows = await read_portfolio_daily(db, days=5)
    assert rows and rows[-1].net_liq_close == Decimal("100001")


async def test_rollup_cycle_survives_a_failing_step(sqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import engines.analytics.engine as mod

    async def _boom(*_a: Any, **_k: Any) -> dict:
        raise RuntimeError("lateral not supported")

    monkeypatch.setattr(mod, "refresh_attribution", _boom)
    engine = mod.AnalyticsEngine(_Redis(), sessionmaker=sqlite_sessionmaker)
    out = await engine.run_rollup_cycle()
    assert out.get("attribution_error") is True and "portfolio_daily" in out


def test_engine_takes_the_rollup_interval_from_the_capacity_dial() -> None:
    """Since #371 the cadence is a versioned-config knob, not an env var: the
    entry point wires the config watcher and the engine follows apply_config."""
    from pathlib import Path

    from core.config import VolTradingConfig
    from engines.analytics.engine import DEFAULT_ROLLUP_INTERVAL_S

    assert DEFAULT_ROLLUP_INTERVAL_S == 300.0
    src = Path("src/engines/analytics/main.py").read_text(encoding="utf-8")
    assert "ConfigWatcher" in src and "ROLLUP_INTERVAL_S" not in src and "os.environ" not in src
    from engines.analytics.engine import AnalyticsEngine

    eng = AnalyticsEngine(_Redis(), sessionmaker=object())  # type: ignore[arg-type]
    eng.apply_config(VolTradingConfig.model_validate({"capacity": {"preset": "custom", "rollup_interval_s": 45}}))
    assert eng.rollup_interval_s == 45.0
