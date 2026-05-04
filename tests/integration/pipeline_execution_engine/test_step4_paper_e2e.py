"""STEP4 phase 2 E2E acceptance tests against IB Paper Account.

Spec : ``docs/vol_trading_pca/specs/STEP4_EXECUTION.md`` §10 (5 tests).

Gated by ``IB_RUN_INTEGRATION=1`` — these require :
  * a live ``ib-gateway`` container with a paper account logged in,
  * the full docker-compose stack up (api + execution-engine + db-writer),
  * vol-engine producing a fresh ``latest_vol_surface:EURUSD`` in Redis.

Skeletons : the assertions below describe the acceptance contract. They
are intentionally not fleshed out further until a real paper-trading
session lets us tune timeouts and the exact IB error mapping.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("IB_RUN_INTEGRATION", "0") != "1",
        reason="needs IB Paper Account up — set IB_RUN_INTEGRATION=1",
    ),
]


@pytest.mark.skip(reason="paper-trading session required ; skeleton only")
def test_submit_creates_structure_and_orders():
    """Spec §10 test 1.

    POST /api/v1/trade/submit {execution_mode='live'} on a valid 3M ATM
    EUR/USD straddle preview qty=1. Expect within 30 s :
      * trade_structures row, state='fully_filled'
      * 2 structure_orders rows, both state='filled'
      * 2 structure_fills rows with non-null spot_at_fill (when market_data
        cache is populated — V1 leaves them null)
      * trade_positions row with state='open'
    """
    raise NotImplementedError


@pytest.mark.skip(reason="paper-trading session required ; skeleton only")
def test_partial_fill_updates_state():
    """Spec §10 test 2.

    Submit qty=50 on an exotic tenor where ATM liquidity < 50 contracts.
    Expect : structure transitions through 'partial_fill' before either
    completing fully or staying partial after IB cancel.
    """
    raise NotImplementedError


@pytest.mark.skip(reason="paper-trading session required ; skeleton only")
def test_rejection_triggers_rollback():
    """Spec §10 test 3.

    Submit qty > buying_power. Leg 0 rejected → rollback runner cancels
    leg 1, unwinds any partial fill on leg 0. Expect rows :
      * structure_orders[0].state = 'rejected'
      * audit log : 'order_rejected' + 'order_cancelled' + (optionally)
        'unwind_order_created'
    """
    raise NotImplementedError


@pytest.mark.skip(reason="paper-trading session required ; skeleton only")
def test_idempotence_duplicate_fills():
    """Spec §10 test 4.

    Disconnect / reconnect IB during fill stream — fill events are
    redelivered. ``apply_fill_idempotent`` must dedupe on
    ``ib_execution_id`` ; the structure_fills table stays unique.
    """
    raise NotImplementedError


@pytest.mark.skip(reason="paper-trading session required ; skeleton only")
def test_lock_prevents_double_submit():
    """Spec §10 test 5.

    Two near-simultaneous POST /trade/submit on the same preview_id.
    Redis NX lock (TTL 10 s) admits the first ; the second receives 409.
    Exactly one structure row is created.
    """
    raise NotImplementedError
