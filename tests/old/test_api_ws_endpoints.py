"""Unit tests for /ws/ticks, /ws/vol, /ws/risk using FastAPI TestClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


@pytest.fixture
def client():
    """TestClient with mocked Redis — the WS bridge task never receives real messages."""
    fake_redis = AsyncMock()
    fake_redis.aclose = AsyncMock()
    fake_pubsub = AsyncMock()
    fake_pubsub.subscribe = AsyncMock()
    fake_pubsub.aclose = AsyncMock()
    async def _listen():
        # Block forever so the bridge stays "listening" without dispatching anything.
        import asyncio
        await asyncio.sleep(3600)
        if False:
            yield None  # make the function an async generator
    fake_pubsub.listen = _listen
    fake_redis.pubsub = lambda: fake_pubsub

    with patch("api.main.aioredis.from_url", return_value=fake_redis), \
         TestClient(create_app()) as c:
        yield c


@pytest.mark.unit
class TestWebSocketEndpoints:
    def test_ticks_endpoint_accepts_connection(self, client):
        """WS /ticks connects and registers on the manager."""
        with client.websocket_connect("/ws/ticks") as ws:
            # After connect, manager should count 1 subscriber on 'ticks'.
            assert client.app.state.ws_manager.count("ticks") == 1
            ws.close()
        # After close, registry cleaned up (disconnect in finally).
        assert client.app.state.ws_manager.count("ticks") == 0

    def test_vol_endpoint_uses_vol_update_channel(self, client):
        with client.websocket_connect("/ws/vol") as ws:
            assert client.app.state.ws_manager.count("vol_update") == 1
            ws.close()

    def test_risk_endpoint_uses_risk_update_channel(self, client):
        with client.websocket_connect("/ws/risk") as ws:
            assert client.app.state.ws_manager.count("risk_update") == 1
            ws.close()

    def test_broadcast_reaches_connected_ws(self, client):
        """A direct call to manager.broadcast sends the message to all live clients."""
        import asyncio
        with client.websocket_connect("/ws/ticks") as ws:
            manager = client.app.state.ws_manager
            # Run the broadcast coroutine in the test thread's loop.
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(manager.broadcast("ticks", "hello"))
            finally:
                loop.close()
            # TestClient drains the send buffer.
            assert ws.receive_text() == "hello"

    def test_endpoints_hidden_from_openapi_schema(self, client):
        """FastAPI exposes WS routes in OpenAPI only as a note — websockets stay out."""
        schema = client.get("/openapi.json").json()
        # WS endpoints do not appear in the paths dict (FastAPI design).
        assert "/ws/ticks" not in schema["paths"]
        assert "/ws/vol" not in schema["paths"]
        assert "/ws/risk" not in schema["paths"]
