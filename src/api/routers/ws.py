"""WebSocket endpoints — /ws/ticks, /ws/vol, /ws/risk subscribed via ConnectionManager."""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.ws.connection_manager import ConnectionManager
from bus import channels

logger = logging.getLogger("api.ws.endpoints")

router = APIRouter(tags=["websocket"])


async def _serve(channel: str, ws: WebSocket) -> None:
    """Register ``ws`` on ``channel``, keep the connection open until the client leaves."""
    manager: ConnectionManager = ws.app.state.ws_manager
    await manager.connect(channel, ws)
    try:
        # Keep the socket alive. We do not expect client messages, but
        # receive_text() blocks until the client sends or disconnects ;
        # incoming frames are ignored. This pattern also triggers
        # WebSocketDisconnect cleanly when the client closes.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        # CR/LF-scrub before logging — ``channel`` can embed a client-supplied
        # path param (``/ws/orders/{structure_id}``), CWE-117.
        safe_channel = channel.replace("\r", "\\r").replace("\n", "\\n")
        logger.exception("ws_handler_crashed", extra={"channel": safe_channel})
    finally:
        await manager.disconnect(channel, ws)


@router.websocket("/ws/ticks")
async def ws_ticks(ws: WebSocket) -> None:
    """Subscribe to the ``ticks`` Redis channel — ~5 messages/s (throttled)."""
    await _serve(channels.CH_TICKS, ws)


@router.websocket("/ws/vol")
async def ws_vol(ws: WebSocket) -> None:
    """Subscribe to ``vol_update`` — emitted at the end of each vol scan (~3 min)."""
    await _serve(channels.CH_VOL_UPDATE, ws)


@router.websocket("/ws/risk")
async def ws_risk(ws: WebSocket) -> None:
    """Subscribe to ``risk_update`` — emitted at the end of each risk cycle (~2s)."""
    await _serve(channels.CH_RISK_UPDATE, ws)


@router.websocket("/ws/orders/{structure_id}")
async def ws_orders(ws: WebSocket, structure_id: int) -> None:
    """Subscribe to ``orders:<structure_id>`` — emitted by execution-engine
    on each order_status / fill / rejection / cancel / unwind event."""
    await _serve(channels.orders_channel(structure_id), ws)


@router.websocket("/ws/positions")
async def ws_positions(ws: WebSocket) -> None:
    """Subscribe to ``positions`` — one MTM snapshot per monitor cycle."""
    await _serve(channels.CH_POSITIONS, ws)


@router.websocket("/ws/exit_alerts")
async def ws_exit_alerts(ws: WebSocket) -> None:
    """Subscribe to ``exit_alerts`` — one row per ExitAlert that fires."""
    await _serve(channels.CH_EXIT_ALERTS, ws)
