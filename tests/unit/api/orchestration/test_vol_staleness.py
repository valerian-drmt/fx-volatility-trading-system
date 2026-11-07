"""Staleness contract on the vol read paths (#328, audit 05#10 / 09#1).

Every vol payload carries ``as_of``, ``source``, ``stale`` and ``age_s``; a
Redis hit is ``live``, the Postgres fallback is ``last-complete``, a
synthetic grid is never fresh; the term structure inherits the surface's
fields; the preview flags a synthetic surface instead of passing it off at
age 0.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from api.orchestration import vol_service
from api.orchestration.vol_service import staleness

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
SURFACE = {"3M": {"atm": {"iv": 0.07, "strike": 1.105}, "25dc": {"iv": 0.072, "strike": 1.13}, "25dp": {"iv": 0.078, "strike": 1.08}}}


def test_staleness_fields() -> None:
    fresh = staleness((NOW - timedelta(seconds=90)).isoformat(), "live", now=NOW)
    assert fresh == {"as_of": NOW - timedelta(seconds=90), "source": "live", "stale": False, "age_s": 90.0}
    old = staleness(NOW - timedelta(hours=12), "last-complete", now=NOW)
    assert old["stale"] is True and old["age_s"] == 43200.0
    synth = staleness(NOW, "synthetic", now=NOW)
    assert synth["stale"] is True, "a synthetic grid is never fresh, whatever its stamp"
    naive = staleness("2026-11-07T11:59:00", "historical", now=NOW)
    assert naive["as_of"].tzinfo is not None and naive["age_s"] == 60.0


class _Redis:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._p = payload

    async def get(self, key: str) -> str | None:
        return json.dumps(self._p) if self._p else None


class _Db:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def execute(self, _stmt: Any) -> Any:
        return SimpleNamespace(scalar_one_or_none=lambda: self._row)


@pytest.mark.asyncio
async def test_redis_hit_is_live_and_db_fallback_is_last_complete() -> None:
    ts = (NOW - timedelta(seconds=30)).isoformat()
    live = await vol_service.get_latest_surface(_Redis({"symbol": "EURUSD", "timestamp": ts, "surface": SURFACE}), "EURUSD")  # type: ignore[arg-type]
    assert live.source == "live" and live.as_of.isoformat() == ts and live.timestamp == live.as_of

    row = SimpleNamespace(underlying="EURUSD", timestamp=NOW - timedelta(hours=14), surface_data=SURFACE)
    fallback = await vol_service.get_latest_surface(_Redis(None), "EURUSD", db=_Db(row))  # type: ignore[arg-type]
    assert fallback.source == "last-complete" and fallback.stale is True and fallback.age_s > 3600

    term = await vol_service.get_term_structure(_Redis(None), "EURUSD", db=_Db(row))  # type: ignore[arg-type]
    assert (term.source, term.stale, term.as_of) == ("last-complete", True, fallback.as_of)


def test_response_models_require_the_contract() -> None:
    from pydantic import ValidationError

    from api.schemas.vol import SurfaceResponse

    with pytest.raises(ValidationError):
        SurfaceResponse(symbol="EURUSD", timestamp=NOW, surface={})  # type: ignore[call-arg]
    ok = SurfaceResponse(symbol="EURUSD", timestamp=NOW, surface={}, as_of=NOW, source="live", stale=False, age_s=1.0)
    assert ok.model_dump()["source"] == "live"


def test_preview_flags_a_synthetic_surface() -> None:
    from pathlib import Path

    src = Path("src/api/routers/trade.py").read_text(encoding="utf-8")
    assert 'surface_source = "synthetic"' in src and "surface_age_s = 999.0" in src
    assert '"warnings": (["synthetic_surface"]' in src
    assert "surface_age_s = 0.0" not in src, "a synthetic grid must not be reported as age 0"
