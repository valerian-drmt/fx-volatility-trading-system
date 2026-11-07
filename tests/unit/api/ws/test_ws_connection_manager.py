"""ConnectionManager fan-out (#339, audit 07#7): registration per channel,
broadcast reaches every live socket, a socket that errors or already closed
is dropped without stopping the others, disconnect is idempotent."""
from __future__ import annotations

import pytest
from starlette.websockets import WebSocketState

from api.ws.connection_manager import ConnectionManager

pytestmark = pytest.mark.asyncio


class _Socket:
    def __init__(self, *, fail: bool = False, state: WebSocketState = WebSocketState.CONNECTED) -> None:
        self.fail = fail
        self.application_state = state
        self.accepted = False
        self.sent: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, message: str) -> None:
        if self.fail:
            raise RuntimeError("peer went away")
        self.sent.append(message)


async def test_connect_accepts_and_registers_per_channel() -> None:
    m = ConnectionManager()
    a, b, c = _Socket(), _Socket(), _Socket()
    await m.connect("risk_update", a)
    await m.connect("risk_update", b)
    await m.connect("ticks", c)
    assert a.accepted and b.accepted and c.accepted
    assert m.count("risk_update") == 2 and m.count("ticks") == 1 and m.count("vol_update") == 0


async def test_broadcast_reaches_every_live_socket_on_the_channel_only() -> None:
    m = ConnectionManager()
    a, b, other = _Socket(), _Socket(), _Socket()
    await m.connect("risk_update", a)
    await m.connect("risk_update", b)
    await m.connect("ticks", other)
    await m.broadcast("risk_update", '{"greeks": 1}')
    assert a.sent == ['{"greeks": 1}'] and b.sent == ['{"greeks": 1}']
    assert other.sent == []


async def test_a_failing_socket_is_dropped_and_its_peers_still_receive() -> None:
    m = ConnectionManager()
    dead, live = _Socket(fail=True), _Socket()
    await m.connect("risk_update", dead)
    await m.connect("risk_update", live)
    await m.broadcast("risk_update", "x")
    assert live.sent == ["x"]
    assert m.count("risk_update") == 1, "the socket that raised left the registry"
    await m.broadcast("risk_update", "y")
    assert live.sent == ["x", "y"]


async def test_a_socket_that_already_closed_is_skipped_and_pruned() -> None:
    m = ConnectionManager()
    closed = _Socket(state=WebSocketState.DISCONNECTED)
    live = _Socket()
    await m.connect("positions", closed)
    await m.connect("positions", live)
    await m.broadcast("positions", "p")
    assert closed.sent == [] and live.sent == ["p"]
    assert m.count("positions") == 1


async def test_disconnect_is_idempotent_and_unknown_channel_broadcast_is_a_noop() -> None:
    m = ConnectionManager()
    s = _Socket()
    await m.connect("account", s)
    await m.disconnect("account", s)
    await m.disconnect("account", s)
    assert m.count("account") == 0
    await m.broadcast("never_registered", "z")  # no raise
