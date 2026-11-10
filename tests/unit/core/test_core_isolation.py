"""core is deterministic and isolated (#332, audit 06#1 / 06#4): no wall
clock, no logger, no environment, no shared import; the contract metadata
and the market-hours rule live in core; the import-linter contract says so."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "src" / "core"

_FORBIDDEN = {
    "wall clock": re.compile(r"\bdatetime\.now\(|\bdate\.today\(|\btime\.time\(|\butcnow\("),
    "logger": re.compile(r"\blogging\.getLogger\(|\bstructlog\.get_logger\("),
    "environment": re.compile(r"\bos\.environ\b|\bos\.getenv\("),
    "shared import": re.compile(r"^\s*(from shared|import shared)", re.M),
    "randomness without a seed": re.compile(r"np\.random\.(rand|randn|random|normal)\("),
}


def test_core_has_no_clock_logger_environment_or_shared_import() -> None:
    offenders: list[str] = []
    for path in CORE.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for what, rx in _FORBIDDEN.items():
                if rx.search(line):
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {what}: {line.strip()[:80]}")
    assert offenders == [], "\n".join(offenders)


def test_import_linter_forbids_core_to_shared() -> None:
    cfg = (ROOT / ".importlinter").read_text(encoding="utf-8")
    block = cfg[cfg.index("[importlinter:contract:1]"): cfg.index("[importlinter:contract:2]")]
    assert re.search(r"forbidden_modules =(?:\n\s+\w+)*\n\s+shared\b", block), "contract 1 must list shared"


def test_contracts_and_market_hours_live_in_core() -> None:
    from core.contracts import build_ib_local_symbol, multiplier_for, parse_local_symbol
    from core.market_hours import is_fx_market_open

    assert multiplier_for("EUR") == 125_000.0 and parse_local_symbol is not None and build_ib_local_symbol is not None
    assert not (ROOT / "src" / "shared" / "contracts.py").exists()
    assert is_fx_market_open(datetime(2026, 11, 7, 9, 51, tzinfo=UTC)) is False    # Saturday
    assert is_fx_market_open(datetime(2026, 11, 8, 22, 0, tzinfo=UTC)) is True     # Sunday open
    assert is_fx_market_open(datetime(2026, 11, 6, 22, 0, tzinfo=UTC)) is False    # Friday close
    with pytest.raises(TypeError):
        is_fx_market_open()  # type: ignore[call-arg]  # the clock is the caller's


def test_shared_market_hours_adds_the_clock_and_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.market_hours import is_fx_market_open, market_gate_active

    assert isinstance(is_fx_market_open(), bool)
    assert is_fx_market_open(datetime(2026, 11, 7, 9, 51, tzinfo=UTC)) is False
    monkeypatch.setenv("FORCE_RUN_MARKET_HOURS", "1")
    assert market_gate_active() is False


def test_preview_builders_take_the_clock_from_the_caller() -> None:
    import inspect

    from core.trade_preview import build_from_legs, build_structure

    for fn in (build_structure, build_from_legs):
        p = inspect.signature(fn).parameters["as_of"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
