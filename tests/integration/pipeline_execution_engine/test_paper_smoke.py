"""IB paper smoke, read-only (#341, audit 07#9 / 07#12).

The one IB-gated suite. It replaces 11 assertion-less skeletons (step4/step5
paper e2e) whose behaviours are covered by unit and integration tests with IB
mocked: the map is in tests/STRUCTURE.md, "IB paper suite". What only a live
session can prove is that the execution engine's IB adapter talks to a real
Gateway: connect, account values, positions and open orders read back, in
read-only mode (no order can be placed).

Run (weekend window; the paper session is single and prod owns it, so stop the
prod gateway first, docs/ops/runbooks.md 4.5):

    IB_RUN_INTEGRATION=1 IB_HOST=127.0.0.1 IB_PORT=4002 python -m pytest -m integration -rs

then record the date and result in tests/STRUCTURE.md.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("IB_RUN_INTEGRATION", "0") != "1",
        reason="needs an IB paper Gateway: set IB_RUN_INTEGRATION=1 (see the module docstring)",
    ),
]


@pytest.fixture
async def executor() -> AsyncIterator[Any]:
    from engines.execution.order_executor import OrderExecutor

    ex = OrderExecutor(
        os.environ.get("IB_HOST", "127.0.0.1"),
        int(os.environ.get("IB_PORT", "4002")),
        int(os.environ.get("IB_CLIENT_ID", "99")),
        readonly=True,
    )
    await ex.connect(timeout=20.0)
    assert ex.is_connected(), "no IB session: gateway down, port wrong, or the client id is taken"
    try:
        yield ex
    finally:
        await ex.disconnect()


@pytest.mark.asyncio
async def test_account_values_arrive(executor: Any) -> None:
    summary: dict[str, Any] = {}
    for _ in range(20):  # account updates stream in after connect
        summary = await executor.account_summary()
        if summary.get("NetLiquidation"):
            break
        await asyncio.sleep(0.5)
    assert summary.get("NetLiquidation"), f"no NetLiquidation in {sorted(summary)[:12]}"
    assert summary.get("account"), "the paper account id is reported"


@pytest.mark.asyncio
async def test_positions_and_open_orders_read_back(executor: Any) -> None:
    positions = await executor.list_positions()
    orders = await executor.list_open_orders()
    assert isinstance(positions, list) and isinstance(orders, list)
    for p in positions:
        assert {"symbol", "position"} <= set(p), p
