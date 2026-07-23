"""``_derive_ib_status`` folds the TCP probe with the market-data heartbeat.

The regression it guards: a bare TCP probe on the gateway's socat port reads OK
even after the nightly IBC restart drops the upstream IBKR session (Error 1100),
so the /dev stack view would show ib-gateway green while no data flows. The
heartbeat age is the authoritative signal — STALE surfaces the upstream-dead case.
"""
from __future__ import annotations

import pytest

from api.routers.dev import _derive_ib_status

pytestmark = pytest.mark.unit

STALE_S = 60.0


def test_tcp_down_is_down() -> None:
    # Gateway container down: heartbeat age is irrelevant.
    assert _derive_ib_status("DOWN", None, STALE_S) == "DOWN"
    assert _derive_ib_status("DOWN", 1.0, STALE_S) == "DOWN"


def test_tcp_up_fresh_heartbeat_is_ok() -> None:
    assert _derive_ib_status("OK", 5.0, STALE_S) == "OK"


def test_tcp_up_stale_heartbeat_is_stale() -> None:
    # Socket up but IBKR upstream dead — the nightly-reset failure mode.
    assert _derive_ib_status("OK", 3600.0, STALE_S) == "STALE"


def test_tcp_up_missing_heartbeat_is_stale() -> None:
    # No heartbeat key at all (engine never wrote one) reads as upstream-dead.
    assert _derive_ib_status("OK", None, STALE_S) == "STALE"


def test_heartbeat_age_boundary_is_exclusive() -> None:
    # age == stale_s is NOT fresh (matches the < comparison the engines use).
    assert _derive_ib_status("OK", STALE_S, STALE_S) == "STALE"
    assert _derive_ib_status("OK", STALE_S - 0.01, STALE_S) == "OK"
