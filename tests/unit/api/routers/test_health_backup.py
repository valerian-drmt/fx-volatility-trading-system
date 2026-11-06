"""Backup freshness in /health/extended and the Prometheus gauge (#343).

The stamp file is the only link between the host cron
(infrastructure/ec2/postgres_backup.sh) and the api; these tests pin its
format and the three states the desk can show: OK, STALE, UNKNOWN.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from api.routers import health

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _stamp(tmp_path: Path, age: timedelta, extra: str = "") -> str:
    ts = int((NOW - age).timestamp())
    p = tmp_path / "postgres-backup.last"
    p.write_text(f"ts={ts} size=1234567 key=postgres/fxvol-x.dump{extra}\n", encoding="utf-8")
    return str(p)


def test_not_configured_means_no_component() -> None:
    assert health._check_backup("", NOW) is None
    assert health._backup_age_seconds("", NOW) is None


def test_fresh_stamp_is_ok_with_age_in_hours(tmp_path: Path) -> None:
    path = _stamp(tmp_path, timedelta(hours=3, minutes=30))
    assert health._check_backup(path, NOW) == "OK (3.5h)"
    assert health._backup_age_seconds(path, NOW) == pytest.approx(3.5 * 3600)


def test_stamp_older_than_26h_is_stale(tmp_path: Path) -> None:
    path = _stamp(tmp_path, timedelta(hours=30))
    assert health._check_backup(path, NOW) == "STALE (30h)"


def test_boundary_just_under_26h_is_still_ok(tmp_path: Path) -> None:
    path = _stamp(tmp_path, timedelta(hours=26) - timedelta(seconds=1))
    assert health._check_backup(path, NOW).startswith("OK")


def test_missing_or_garbage_stamp_is_unknown(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope")
    assert health._check_backup(missing, NOW) == "UNKNOWN (no backup stamp)"
    garbage = tmp_path / "bad"
    garbage.write_text("hello\n", encoding="utf-8")
    assert health._check_backup(str(garbage), NOW) == "UNKNOWN (no backup stamp)"


def test_clock_skew_never_yields_negative_age(tmp_path: Path) -> None:
    path = _stamp(tmp_path, timedelta(minutes=-5))  # stamp 5 min in the future
    assert health._backup_age_seconds(path, NOW) == 0.0


def test_gauge_reports_age_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The collector reads the real clock: stamp one hour before *now*, not NOW.
    ts = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    p = tmp_path / "postgres-backup.last"
    p.write_text(f"ts={ts} size=1 key=k\n", encoding="utf-8")
    monkeypatch.setattr(health, "_backup_state_path", lambda: str(p))
    value = REGISTRY.get_sample_value("fxvol_postgres_backup_age_seconds")
    assert value is not None
    assert 3500 < value < 3700


def test_gauge_absent_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "_backup_state_path", lambda: "")
    assert REGISTRY.get_sample_value("fxvol_postgres_backup_age_seconds") is None


def test_unknown_backup_degrades_the_aggregate_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured host whose cron never succeeded must not read OK."""
    import asyncio

    class _Redis:
        async def ping(self) -> bool:
            return True

        async def get(self, _key: str) -> str:
            return datetime.now(UTC).isoformat()

    class _Db:
        async def execute(self, _stmt: object) -> None:
            return None

    monkeypatch.setattr(health, "_backup_state_path", lambda: "/nonexistent/stamp")
    body = asyncio.run(health.health_extended(_Redis(), _Db()))  # type: ignore[arg-type]
    assert body["components"]["backup"] == "UNKNOWN (no backup stamp)"
    assert body["status"] == "DEGRADED"

    monkeypatch.setattr(health, "_backup_state_path", lambda: "")
    body = asyncio.run(health.health_extended(_Redis(), _Db()))  # type: ignore[arg-type]
    assert "backup" not in body["components"]
    assert body["status"] == "OK"
