"""ConnectionManager — fan-out layer between Redis bridge and WebSocket clients.

One instance lives on ``app.state.ws_manager`` (set by the lifespan). Each
WebSocket endpoint registers on connect and un-registers on disconnect.
``broadcast(channel, message)`` pushes to every live socket on that
channel and drops the ones that already errored.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger("api.ws")


class ConnectionManager:
    """Dict-of-sets registry (channel → WebSocket). Not thread-safe — used
    from the single FastAPI event loop only."""

    def __init__(self) -> None:
        self._conns: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, channel: str, ws: WebSocket) -> None:
        """Accept the WS handshake and register it on ``channel``."""
        await ws.accept()
        async with self._lock:
            self._conns[channel].add(ws)
        logger.info("ws_connect", extra={"channel": channel, "total": self.count(channel)})

    async def disconnect(self, channel: str, ws: WebSocket) -> None:
        async with self._lock:
            self._conns[channel].discard(ws)

    def count(self, channel: str) -> int:
        return len(self._conns.get(channel, set()))

    async def broadcast(self, channel: str, message: str) -> None:
        """Send ``message`` to every subscriber of ``channel``. Drops dead ones.

        ``CONNECTED`` state check avoids a raised ``RuntimeError`` when a
        client closed but the manager has not yet been notified. Exceptions
        during ``send_text`` mark the socket for removal.
        """
        dead: list[WebSocket] = []
        for ws in tuple(self._conns.get(channel, ())):
            if ws.application_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning("ws_send_failed", extra={"channel": channel, "error": str(e)})
                dead.append(ws)
        if dead:
            async with self._lock:
                self._conns[channel].difference_update(dead)
