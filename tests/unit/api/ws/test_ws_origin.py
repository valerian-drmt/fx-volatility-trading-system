"""WebSocket Origin allowlist (#299, audit 01#2): a foreign origin is refused
at the handshake, an allowed one and a non-browser (no Origin) connect; the
channel key is gone once the last socket leaves."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.routers import ws as ws_mod
from api.ws.connection_manager import ConnectionManager
from api.ws.origin import origin_allowed

pytestmark = pytest.mark.unit


def test_origin_matching_is_exact_case_insensitive_and_permits_absence() -> None:
    allowed = {"https://valeriandarmente.dev", "http://localhost"}
    assert origin_allowed(None, allowed)
    assert origin_allowed("https://valeriandarmente.dev", allowed)
    assert origin_allowed("HTTPS://VALERIANDARMENTE.DEV/", allowed)
    assert not origin_allowed("https://valeriandarmente.dev.evil.example", allowed)
    assert not origin_allowed("http://localhost:3000", allowed), "a port is a different origin"
    assert not origin_allowed("null", allowed)


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, ConnectionManager]:
    monkeypatch.setattr(ws_mod, "allowed_origins", lambda: {"https://valeriandarmente.dev"})
    app = FastAPI()
    app.include_router(ws_mod.router)
    manager = ConnectionManager()
    app.state.ws_manager = manager
    return TestClient(app), manager


def test_foreign_origin_is_refused_before_accept(monkeypatch: pytest.MonkeyPatch) -> None:
    client, manager = _app(monkeypatch)
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/ws/ticks", headers={"origin": "https://evil.example"}):
        pass
    assert exc.value.code == 1008
    assert manager.count("ticks") == 0


def test_allowed_origin_and_no_origin_connect_and_the_channel_is_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    client, manager = _app(monkeypatch)
    with client.websocket_connect("/ws/ticks", headers={"origin": "https://valeriandarmente.dev"}):
        assert manager.count("ticks") == 1
    assert "ticks" not in manager._conns, "empty channel key dropped on the last disconnect"
    with client.websocket_connect("/ws/risk"):
        assert manager.count("risk_update") == 1
    assert manager.count("risk_update") == 0
