"""#492: every limit is on the contract's grid."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.execution.ticks import OPTION_TICK, snap, tick_for

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]


def test_each_contract_has_its_tick() -> None:
    assert tick_for("EUUG7 C1110", is_option=True) == 0.0001
    assert tick_for("6EZ6", is_option=False) == 0.00005
    assert tick_for("M6EH7", is_option=False) == 0.0001
    assert tick_for("E7H7", is_option=False) == 0.0001
    # unknown future: a tick that is on every known grid
    assert tick_for("XYZ", is_option=False) == 0.0001
    assert tick_for(None, is_option=False) == 0.0001


@pytest.mark.parametrize(("price", "tick", "toward", "expected"), [
    (0.01234, 0.0001, "nearest", 0.0123),
    (0.01236, 0.0001, "nearest", 0.0124),
    (1.166327, 0.00005, "down", 1.1663),
    (1.166327, 0.00005, "up", 1.16635),
    (1.16635, 0.00005, "down", 1.16635),   # on the grid: unchanged
    (1.16635, 0.00005, "up", 1.16635),
    (1.1, 0.0001, "down", 1.1),            # 1.1 / 0.0001 is 10999.999... in binary
    (0.00002, 0.0001, "down", 0.0001),     # never below one tick
])
def test_snap(price: float, tick: float, toward: str, expected: float) -> None:
    assert snap(price, tick, toward=toward) == pytest.approx(expected, abs=1e-12)


def test_snap_refuses_nonsense() -> None:
    with pytest.raises(ValueError):
        snap(1.0, 0.0)
    with pytest.raises(ValueError):
        snap(1.0, 0.0001, toward="sideways")


def test_the_close_path_snaps_every_limit_it_computes() -> None:
    """The operator's limit and the outside-hours future limit were rounded to
    six decimals, not snapped."""
    src = (ROOT / "src/api/routers/positions.py").read_text(encoding="utf-8")
    assert "snap(float(limit_price_override), tick)" in src
    assert not re.search(r"round\(\s*_close_limit_from_mark", src)
    assert not re.search(r"round\(float\(limit_price_override\)", src)
    # one table: no private copy of the option tick
    for rel in ("src/api/routers/positions.py", "src/engines/execution/order_executor.py"):
        assert "_OPT_TICK = 0.0001" not in (ROOT / rel).read_text(encoding="utf-8"), rel
    assert OPTION_TICK == 0.0001
