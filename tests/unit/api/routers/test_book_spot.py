"""The level the stress grid, Greeks ladder, VaR factors and pin-risk revalue
around (#432). On 2026-11-11 prod returned `no_spot` on all of them: paper IB
marked no position (`market_price` null), so neither the future mark nor the
strike proxy (which also required a mark) applied, while the vol engine's
surface forward sat fresh in Redis."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from api.routers import portfolio_panel as pp

pytestmark = pytest.mark.asyncio

FUT = "6EH7"
PUT = "EUUG7 P1120"


class _Redis:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def get(self, _key: str) -> Any:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def _pos(structure: str, market_price: float | None, pid: int = 1) -> Any:
    return SimpleNamespace(id=pid, structure=structure, market_price=market_price, iv=0.06,
                           quantity=1, side="BUY", expiry="2027-03-15")


def _surface(forward: Any) -> str:
    return json.dumps({"symbol": "EURUSD", "timestamp": "2026-11-11T09:00:00Z", "forward": forward, "surface": {}})


async def test_a_held_future_mark_wins() -> None:
    spot, src = await pp._book_spot([_pos(PUT, None), _pos(FUT, 1.1290, 2)], _Redis(_surface(1.1249)))
    assert (spot, src) == (1.1290, "future_mark")


async def test_the_publishers_real_payload_is_read() -> None:
    """`publish_vol_update` writes the calibration level as `spot` and no
    `forward` key: reading only `forward` fell through to a strike 900 pips
    off on prod (2026-11-11). Build the payload with the real publisher."""
    from bus import publisher

    class _Capture:
        def __init__(self) -> None:
            self.kv: dict[str, str] = {}

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.kv[key] = value

        async def publish(self, _channel: str, _data: str) -> None:
            return None

    cap = _Capture()
    await publisher.publish_vol_update(cap, symbol="EURUSD", surface_data={}, signals_data=[], spot=1.1183)
    spot, src = await pp._book_spot([_pos(PUT, None)], _Redis(cap.kv["latest_vol_surface:EURUSD"]))
    assert (spot, src) == (1.1183, "surface_forward")


async def test_unmarked_book_uses_the_surface_forward() -> None:
    """The prod case: no IB mark anywhere, fresh surface in Redis."""
    spot, src = await pp._book_spot([_pos(PUT, None), _pos(FUT, None, 2)], _Redis(_surface(1.1249)))
    assert (spot, src) == (1.1249, "surface_forward")


async def test_strike_is_the_last_resort_and_needs_no_mark() -> None:
    spot, src = await pp._book_spot([_pos(PUT, None)], _Redis(None))
    assert (spot, src) == (1.12, "strike_proxy")


@pytest.mark.parametrize("bad", [None, "not json", _surface(None), _surface("nan"), _surface(-1), RuntimeError("redis down")])
async def test_a_missing_or_bad_forward_never_raises(bad: Any) -> None:
    spot, src = await pp._book_spot([_pos(FUT, None)], _Redis(bad))
    assert (spot, src) == (None, None)


async def test_resolved_book_flags_the_forward_as_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def scalars(self) -> Any:
            return self

        def all(self) -> list[Any]:
            return [_pos(FUT, None)]

    class _Db:
        async def execute(self, _stmt: Any) -> Any:
            return _Result()

    book = await pp._resolve_book(_Db(), _Redis(_surface(1.1249)))
    assert book.spot == 1.1249 and book.spot_source == "surface_forward"
    assert book.fallback == {"spot": "surface_forward"}
    assert not [d for d in book.dropped if d["reason"] == "no_spot"]
