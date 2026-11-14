"""#491: one door to the broker. Every order the engine sends passes the
kill switch and the ready gate, whatever path it took."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from engines.execution import door
from persistence.halt import set_halt

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]


class _IB:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def placeOrder(self, contract: Any, order: Any) -> str:
        self.sent.append((contract, order))
        return "trade"


class _Session:
    def __init__(self, ready: bool) -> None:
        self.state = type("S", (), {})()
        self.state.ready = asyncio.Event()
        self.state.ready_reason = "first pass not done"
        if ready:
            self.state.ready.set()


@pytest.fixture(autouse=True)
def _reset_door() -> Any:
    yield
    door.configure(None, None)


async def test_an_order_goes_through_when_nothing_forbids_it(sqlite_sessionmaker: Any) -> None:
    door.configure(sqlite_sessionmaker, _Session(ready=True))
    ib = _IB()
    assert await door.place_at_broker(ib, "c", "o") == "trade"
    assert ib.sent == [("c", "o")]


async def test_the_kill_switch_stops_every_order(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        await set_halt(db, halted=True, reason="test", by="test")
        await db.commit()
    door.configure(sqlite_sessionmaker, _Session(ready=True))
    ib = _IB()
    with pytest.raises(HTTPException) as e:
        await door.place_at_broker(ib, "c", "o")
    assert e.value.status_code == 423 and ib.sent == []


async def test_a_halt_flag_that_cannot_be_read_is_a_refusal() -> None:
    def broken() -> Any:
        raise RuntimeError("db down")

    door.configure(broken, _Session(ready=True))  # type: ignore[arg-type]
    ib = _IB()
    with pytest.raises(HTTPException) as e:
        await door.place_at_broker(ib, "c", "o")
    assert e.value.status_code == 503 and ib.sent == []


async def test_nothing_leaves_while_the_book_is_behind_the_broker(sqlite_sessionmaker: Any) -> None:
    door.configure(sqlite_sessionmaker, _Session(ready=False))
    ib = _IB()
    with pytest.raises(HTTPException) as e:
        await door.place_at_broker(ib, "c", "o")
    assert e.value.status_code == 503 and ib.sent == []
    assert e.value.detail["code"] == "reconciliation_in_progress"


def test_nothing_else_in_the_engine_sends_an_order() -> None:
    """A new path that calls IB directly fails here: it goes through the door."""
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "src/engines/execution/door.py":
            continue
        if re.search(r"\.placeOrder\(", path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []
    main = (ROOT / "src/engines/execution/main.py").read_text(encoding="utf-8")
    assert "door.configure(sm, session)" in main, "the engine arms the door at startup"
