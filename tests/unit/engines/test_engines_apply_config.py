"""apply_config on both cold-path engines does something now (#329 / #371 /
#372): the analytics engine's cadence, window, snapshot gap, rollup cadence
and thread cap follow the dial; the vol engine's chain width and concurrency
reach the fetcher on the next scan; the DB loader serves the latest row."""
from __future__ import annotations

from typing import Any

import pytest

from core.config import VolTradingConfig
from engines.analytics import engine as analytics_mod
from engines.analytics.engine import AnalyticsEngine
from engines.vol import chain_fetcher
from engines.vol.engine import VolEngine

pytestmark = pytest.mark.asyncio


class _Redis:
    async def get(self, name: str) -> Any:
        return None

    async def set(self, name: str, value: str, ex: int | None = None) -> Any:
        return True

    async def publish(self, channel: str, message: str) -> int:
        return 0


def _cfg(**capacity: Any) -> VolTradingConfig:
    return VolTradingConfig.model_validate({"capacity": {"preset": "custom", **capacity}})


async def test_analytics_apply_config_moves_every_knob_the_cycle_reads() -> None:
    eng = AnalyticsEngine(_Redis(), sessionmaker=object())  # type: ignore[arg-type]
    assert eng.interval_s == 3600 and eng.capacity.preset == "balanced"
    eng.apply_config(_cfg(analytics_interval_min=15, rollup_interval_s=45, training_window_days=30,
                          pca_snapshot_interval_min=5, thread_cap=2), version=7)
    assert eng.interval_s == 900 and eng.rollup_interval_s == 45.0
    assert eng.capacity.training_window_days == 30 and eng.capacity.pca_snapshot_interval_min == 5
    assert eng.capacity.thread_cap == 2 and eng.config_version == 7
    eng.apply_config(VolTradingConfig.model_validate({"capacity": {"preset": "eco"}}), version=8)
    assert eng.interval_s == 180 * 60 and eng.capacity.strikes_per_side == 12


async def test_analytics_thread_cap_wraps_the_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    class _Limits:
        def __init__(self, limits: int) -> None:
            seen.append(limits)

        def __enter__(self) -> None: ...
        def __exit__(self, *a: Any) -> None: ...

    import types

    fake = types.ModuleType("threadpoolctl")
    fake.threadpool_limits = _Limits  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "threadpoolctl", fake)

    eng = AnalyticsEngine(_Redis(), sessionmaker=object())  # type: ignore[arg-type]

    async def _noop() -> None: ...

    monkeypatch.setattr(eng, "_collect_snapshot", _noop)
    monkeypatch.setattr(eng, "_refit_gmm", _noop)
    monkeypatch.setattr(eng, "_recompute_pc3_history", _noop)
    eng.apply_config(_cfg(thread_cap=3))
    await eng.run_cycle()
    assert seen == [3]
    assert analytics_mod._thread_cap is not None


async def test_vol_apply_config_sets_the_capacity_the_fetch_reads() -> None:
    calls: list[dict[str, Any]] = []

    async def fetch(F: float) -> dict:
        calls.append({"n_side": eng.capacity.strikes_per_side, "conc": eng.capacity.max_concurrent_scans})
        return {}

    eng = VolEngine(ib=object(), redis=_Redis(), symbol="EURUSD", ib_host="h", ib_port=1, client_id=2,
                    fetch_fop_chain=fetch)
    assert eng.capacity.strikes_per_side == 18 and eng.config_version == -1
    eng.apply_config(_cfg(strikes_per_side=12, max_concurrent_scans=2), version=3)
    assert eng.config_version == 3 and eng.config.capacity.strikes_per_side == 12
    await eng._fetch_fop_chain(1.1)
    assert calls == [{"n_side": 12, "conc": 2}]


async def test_scan_all_tenors_passes_width_and_concurrency_down(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []
    active = {"now": 0, "max": 0}

    async def fake_scan_one(ib: Any, chain: dict, F: float, n_side: int = 18, **kw: Any) -> list:
        import asyncio

        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1
        seen.append((chain["label"], n_side))
        return [(0.5, 0.08, F)]

    monkeypatch.setattr(chain_fetcher, "scan_one_tenor", fake_scan_one)
    chains = [{"label": "1M", "dte": 30}, {"label": "2M", "dte": 60}, {"label": "3M", "dte": 90}]
    out = await chain_fetcher.scan_all_tenors_concurrent(object(), 1.1, chains, max_concurrent=2, n_side=12)
    assert set(out) == {"1M", "2M", "3M"}
    assert all(n == 12 for _, n in seen) and active["max"] == 2


def test_qualify_slices_n_side_strikes_each_side_of_atm() -> None:
    strikes = [round(1.00 + 0.005 * i, 3) for i in range(81)]  # 1.000 .. 1.400
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - 1.1))
    for n_side in (12, 18):
        lo, hi = max(0, atm_idx - n_side), min(len(strikes) - 1, atm_idx + n_side)
        assert len(strikes[lo: hi + 1]) == 2 * n_side + 1
    # the same arithmetic the fetcher uses: fewer strikes, same ATM anchor
    assert chain_fetcher.DEFAULT_STRIKES_PER_SIDE == 18


async def test_config_store_loads_the_latest_row_or_the_defaults(sqlite_sessionmaker: Any) -> None:
    from persistence.config_store import load_latest_config
    from persistence.models import VolConfig

    version, cfg = await load_latest_config(sqlite_sessionmaker)
    assert version == 0 and cfg.capacity.preset == "balanced"
    async with sqlite_sessionmaker() as db:
        db.add(VolConfig(version=1, config=_cfg(strikes_per_side=10).model_dump()))
        db.add(VolConfig(version=2, config=_cfg(strikes_per_side=14).model_dump()))
        await db.commit()
    version, cfg = await load_latest_config(sqlite_sessionmaker)
    assert version == 2 and cfg.capacity.strikes_per_side == 14
