"""Shared stubs for the analytics-engine unit tests.

Mirrors the risk-engine mocking style : an in-memory Redis stub
(get/set/publish/hset/hgetall/expire) + a fake async sessionmaker that serves
canned SQLAlchemy-style results in call order. No DB, no Redis, no IB.
"""
from __future__ import annotations

import json
from collections import deque
from types import SimpleNamespace
from typing import Any

from core.vol.pca_engine import DELTAS, TENORS


class FakeResult:
    """Canned result covering the three access shapes the engine uses:
    ``scalar_one_or_none()``, ``.all()``, and ``.scalars().all()``."""

    def __init__(
        self,
        *,
        scalar: Any = None,
        rows: list[Any] | None = None,
        scalars: list[Any] | None = None,
    ) -> None:
        self._scalar = scalar
        self._rows = rows if rows is not None else []
        self._scalars = scalars if scalars is not None else []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._scalars))


class FakeSession:
    """Async-context-manager session that pops the next result per execute."""

    def __init__(self, results: deque[FakeResult]) -> None:
        self._results = results

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def execute(self, *_args: Any, **_kwargs: Any) -> FakeResult:
        if not self._results:
            return FakeResult()
        return self._results.popleft()


class FakeSessionmaker:
    """Callable returning a fresh session ; results are served in order across
    all sessions opened during a run (one execute per step)."""

    def __init__(self, results: list[FakeResult] | None = None) -> None:
        self._results: deque[FakeResult] = deque(results or [])

    def __call__(self) -> FakeSession:
        return FakeSession(self._results)


class FakeRedis:
    """Minimal in-memory async Redis stub."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = dict(store or {})
        self.sets: dict[str, tuple[Any, int | None]] = {}
        self.published: list[tuple[str, str]] = []
        self.hashes: dict[str, dict[Any, Any]] = {}

    async def get(self, name: str) -> Any:
        return self.store.get(name)

    async def set(self, name: str, value: Any, ex: int | None = None) -> bool:
        self.store[name] = value
        self.sets[name] = (value, ex)
        return True

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def hset(
        self, name: str, key: Any = None, value: Any = None,
        mapping: dict[Any, Any] | None = None,
    ) -> int:
        h = self.hashes.setdefault(name, {})
        if mapping:
            h.update(mapping)
        elif key is not None:
            h[key] = value
        return 1

    async def hgetall(self, name: str) -> dict[Any, Any]:
        return dict(self.hashes.get(name, {}))

    async def expire(self, name: str, ttl: int) -> bool:
        return True

    def db_events(self) -> list[dict[str, Any]]:
        """Parsed ``db_events`` frames published so far."""
        return [
            json.loads(msg)
            for channel, msg in self.published
            if channel == "db_events"
        ]


def make_surface(iv_base: float = 0.09) -> dict[str, Any]:
    """A complete surface : every TENOR × DELTA node has an ``iv`` + ``strike``,
    so ``feature_vector_from_surface`` returns the full 30-dim vector."""
    surface: dict[str, Any] = {}
    for i, t in enumerate(TENORS):
        node: dict[str, Any] = {}
        for j, d in enumerate(DELTAS):
            node[d] = {"iv": iv_base + 0.001 * i + 0.0005 * j, "strike": 1.10 + 0.001 * j}
        surface[t] = node
    return surface


def make_snapshot_row(ts: Any, *, null: bool = False, bias: float = 0.0) -> SimpleNamespace:
    """A ``SurfaceSnapshotHourly``-shaped object : ``timestamp`` + 30 ``iv_*``
    columns. ``null=True`` leaves the IV columns None (row is skipped)."""
    attrs: dict[str, Any] = {"timestamp": ts, "symbol": "EURUSD"}
    for i, t in enumerate(TENORS):
        for j, d in enumerate(DELTAS):
            col = f"iv_{t.lower()}_{d}"
            attrs[col] = None if null else (9.0 + 0.1 * i + 0.05 * j + bias)
    return SimpleNamespace(**attrs)
