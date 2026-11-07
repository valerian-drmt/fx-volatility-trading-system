"""Execution-engine channel payloads (#366, hot/cold T2).

One test per publisher: the message the engine emits on ``positions``,
``account`` and ``spot_orders`` has exactly the keys of the fixture under
``tests/fixtures/channels/``, and the row inside has the keys of the REST
reader it mirrors (``/positions/open``, ``/portfolio/account`` ``latest``,
``/trade/spot-orders``), because both call the same ``persistence.rows``
serialiser. A shape change must change the fixture.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio

CHANNELS = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "channels"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((CHANNELS / f"{name}.json").read_text(encoding="utf-8"))


class _Redis:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.set_keys: dict[str, str] = {}

    async def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, json.loads(data)))

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.set_keys[key] = value


def _position(**over: Any) -> Any:
    from persistence.models import OpenPosition
    base = dict(
        id=52, package_id="PKG-87", trade_id="T-87", contract_id=853091540, product_label="Straddle",
        structure="EUUZ6 C1130", side="BUY", quantity=Decimal("10"), tenor="3M", expiry=date(2026, 12, 4),
        current_pnl_usd=Decimal("2864.23"), market_price=Decimal("0.0131"), contract_price_entry=Decimal("0.0123"),
        nominal_eur=Decimal("1250000"), delta_usd=Decimal("612000"), gamma_usd=Decimal("14.7"),
        vega_usd=Decimal("2779.58"), theta_usd=Decimal("-92.41"), iv=Decimal("0.0605"),
        vanna_usd=Decimal("120.5"), volga_usd=Decimal("33.1"),
        timestamp=datetime(2026, 11, 6, 12, 0, tzinfo=UTC), entry_timestamp=datetime(2026, 11, 5, 9, 30, tzinfo=UTC),
    )
    base.update(over)
    return OpenPosition(**base)


def _snap() -> Any:
    from persistence.models import AccountHistory
    return AccountHistory(
        timestamp=datetime(2026, 11, 6, 12, 0, tzinfo=UTC), net_liq_usd=Decimal("176904.63"),
        cash_usd=Decimal("172209.42"), unrealized_pnl_usd=Decimal("2801.1"), accrued_cash=Decimal("0"),
        gross_position_value=Decimal("4695.21"), init_margin_req=Decimal("4695.21"),
        maint_margin_req=Decimal("3756.17"), excess_liquidity=Decimal("173148.46"), cushion=Decimal("0.97"),
        currencies={"USD": {"CashBalance": 150000.0}, "EUR": {"CashBalance": 19800.0}}, open_positions_count=3,
    )


def _spot() -> Any:
    from persistence.models import SpotOrder
    return SpotOrder(
        id=3, created_at=datetime(2026, 11, 6, 12, 0, tzinfo=UTC), symbol="EUR", currency="USD",
        exchange="IDEALPRO", ib_local_symbol="EUR.USD", side="BUY", qty=25000, qty_filled=0, order_type="LMT",
        limit_price=1.1226, avg_fill_price=None, commission_usd=None, state="submitted",
        state_updated_at=datetime(2026, 11, 6, 12, 0, 1, tzinfo=UTC), filled_at=None, rejection_text=None,
        ib_order_id="1427", ib_perm_id="1915668542", ib_status="PreSubmitted", why_held=None,
    )


async def test_positions_changed_matches_the_fixture_and_the_rest_row() -> None:
    from bus.publisher import publish_positions_changed
    from persistence.rows import open_position_row

    want = fixture("positions")
    redis = _Redis()
    rows = [open_position_row(_position(), date(2026, 11, 6))]
    await publish_positions_changed(redis, rows=rows, closed=[{"id": 51, "structure": "6EZ6", "contract_id": 1}],
                                    n_open=3, timestamp=datetime(2026, 11, 6, 12, 0, 2, tzinfo=UTC))

    (channel, got), = redis.published
    assert channel == "positions"
    assert got.keys() == want.keys() and got["event"] == "positions_changed"
    assert got["rows"][0].keys() == want["rows"][0].keys()
    assert got["closed"][0].keys() == want["closed"][0].keys()
    assert got["rows"][0]["tenor_now"] == "1M" and got["rows"][0]["quantity"] == 10.0


async def test_account_snapshot_matches_the_fixture_and_sets_the_cache_key() -> None:
    from bus.publisher import publish_account_snapshot
    from persistence.rows import account_snapshot_row

    want = fixture("account")
    redis = _Redis()
    await publish_account_snapshot(redis, account_snapshot_row(_snap()) or {},
                                   timestamp=datetime(2026, 11, 6, 12, 0, 1, tzinfo=UTC))

    (channel, got), = redis.published
    assert channel == "account"
    assert got.keys() == want.keys() and got["latest"].keys() == want["latest"].keys()
    assert got["latest"]["net_liq_usd"] == 176904.63
    assert "account_snapshot" in redis.set_keys


async def test_spot_order_changed_matches_the_fixture_and_the_rest_row() -> None:
    from bus.publisher import publish_spot_order
    from persistence.rows import spot_order_row

    want = fixture("spot_orders")
    redis = _Redis()
    await publish_spot_order(redis, spot_order_row(_spot()), timestamp=datetime(2026, 11, 6, 12, 0, 1, tzinfo=UTC))

    (channel, got), = redis.published
    assert channel == "spot_orders"
    assert got.keys() == want.keys() and got["row"].keys() == want["row"].keys()
    assert got["row"]["ib_order_id"] == 1427 and got["row"]["created_at"] == "2026-11-06T12:00:00Z"


def test_rest_readers_use_the_same_serialisers() -> None:
    """The api serialisers are the persistence ones, by identity of output."""
    from api.routers.portfolio_panel import _serialize_snap
    from api.routers.trade import serialize_spot_order
    from persistence.rows import account_snapshot_row, spot_order_row

    assert serialize_spot_order(_spot()) == spot_order_row(_spot())
    assert _serialize_snap(_snap()) == account_snapshot_row(_snap())


def test_bridge_forwards_and_routes_serve_the_three_channels() -> None:
    from api.routers import ws as ws_routes
    from api.ws.redis_bridge import _FORWARDED
    from bus import channels

    assert {channels.CH_POSITIONS, channels.CH_ACCOUNT, channels.CH_SPOT_ORDERS} <= set(_FORWARDED)
    paths = {r.path for r in ws_routes.router.routes}
    assert {"/ws/positions", "/ws/account", "/ws/spot_orders"} <= paths


async def test_position_sync_publishes_only_when_a_row_changed(sqlite_sessionmaker: Any) -> None:
    """Two syncs on the same IB snapshot: the first publishes the new rows,
    the second publishes nothing (no churn on the hot channel)."""
    from engines.execution.position_sync import sync_positions_from_ib

    class _Exec:
        def is_connected(self) -> bool:
            return True

        def account_is_reporting(self) -> bool:
            return True

        async def list_positions(self) -> list[dict[str, Any]]:
            return [{"local_symbol": "6EZ6", "symbol": "EUR", "position": 7.0, "avg_cost": 140_000.0,
                     "expiry": "20261214", "con_id": 1}]

    redis = _Redis()
    async with sqlite_sessionmaker() as db:
        first = await sync_positions_from_ib(db, _Exec(), redis)  # type: ignore[arg-type]
    async with sqlite_sessionmaker() as db:
        second = await sync_positions_from_ib(db, _Exec(), redis)  # type: ignore[arg-type]

    assert first["opened"] == 1 and first["published"] is True
    assert second["opened"] == 0 and second["published"] is False
    assert len(redis.published) == 1
    channel, msg = redis.published[0]
    assert channel == "positions" and msg["event"] == "positions_changed"
    assert msg["rows"][0]["structure"] == "6EZ6" and msg["n_open"] == 1 and msg["closed"] == []
