"""One env-var schema (#333, audit 06#13): no raw environment read outside the
settings modules, one name for the execution engine's URL, empty flags parse
as defaults, and the per-service classes extend the shared one."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"

SETTINGS_MODULES = {
    "shared/config.py",
    "api/config.py",
    "engines/execution/settings.py",
    "engines/vol/settings.py",
    "persistence/migrations/env.py",   # alembic's own process, reads DATABASE_URL before any Settings exists
}
_RAW = re.compile(r"\bos\.(getenv|environ)\b")


def test_no_raw_environment_read_outside_the_settings_modules() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel in SETTINGS_MODULES:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _RAW.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{n}: {line.strip()[:80]}")
    assert offenders == [], "read it through a Settings class:\n" + "\n".join(offenders)


def test_one_name_for_the_execution_engine_url() -> None:
    hits = [p.relative_to(ROOT).as_posix() for p in [*SRC.rglob("*.py"), ROOT / "docker-compose.yml"]
            if "EXECUTION_ENGINE_URL" in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == [], hits
    from api.config import Settings

    assert Settings().EXECUTION_URL == "http://execution-engine:8001"


def test_empty_flags_parse_as_their_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from engines.execution.settings import ExecutionSettings
    from shared.config import Settings, truthy

    for name in ("OTEL_SDK_DISABLED", "FORCE_RUN_MARKET_HOURS", "DB_STATEMENT_TIMEOUT_MS"):
        monkeypatch.setenv(name, "")
    s = Settings()
    assert s.otel_sdk_disabled is False and s.force_run_market_hours is False and s.db_statement_timeout_ms == 0
    monkeypatch.setenv("IB_READ_ONLY", "")
    monkeypatch.setenv("EXECUTION_USE_COMBO", "")
    e = ExecutionSettings()
    assert e.ib_read_only is False and e.execution_use_combo is False and e.reconcile_autoclose is True
    assert e.max_sync_age_s == 120.0
    assert truthy("YES") and truthy("1") and not truthy("no") and not truthy(None)


def test_shared_fields_are_available_on_every_service_class(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.config import Settings as ApiSettings
    from engines.execution.settings import ExecutionSettings
    from engines.vol.settings import VolSettings
    from shared.config import Settings

    monkeypatch.setenv("ALLOWED_SYMBOLS", "eur, m6e,")
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    for cls in (Settings, ApiSettings, ExecutionSettings, VolSettings):
        s = cls()
        assert s.allowed_symbols == ("EUR", "M6E") and s.trading_mode == "live", cls.__name__
    assert ExecutionSettings().IB_CLIENT_ID == 5 and Settings().IB_CLIENT_ID == 1


def test_settings_caches_are_cleared_between_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.config import get_settings

    monkeypatch.setenv("GIT_SHA", "deadbeef")
    assert get_settings().GIT_SHA == "deadbeef"
