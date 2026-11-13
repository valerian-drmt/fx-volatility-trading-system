"""EX-01 (#319): a close whose dispatch timed out must stay in flight.

Before: any httpx error stamped the closing order ``rejected``; the stacking
guard ignores rejected rows, so a retry placed a second live close while the
first was working at IB. Now only a connect-level failure (request never sent)
is ``rejected``; everything after the connection counts as in flight.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from api.routers.positions import (
    _INFLIGHT_CLOSE_WINDOW,
    classify_dispatch_failure,
    close_inflight_remaining,
)

_REQ = httpx.Request("POST", "http://execution-engine:8001/internal/positions/close-by-symbol")


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("connection refused", request=_REQ),
        httpx.ConnectTimeout("connect timed out", request=_REQ),
    ],
)
def test_connect_level_failures_mean_the_order_was_never_sent(exc: httpx.HTTPError) -> None:
    assert classify_dispatch_failure(exc) == "not_sent"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ReadTimeout("read timed out", request=_REQ),
        httpx.WriteTimeout("write timed out", request=_REQ),
        httpx.PoolTimeout("pool timed out", request=_REQ),
        httpx.RemoteProtocolError("peer closed connection", request=_REQ),
        httpx.ReadError("reset", request=_REQ),
    ],
)
def test_post_connect_failures_are_unknown_and_stay_in_flight(exc: httpx.HTTPError) -> None:
    assert classify_dispatch_failure(exc) == "unknown"


def test_timed_out_close_counts_against_the_stacking_guard() -> None:
    """The handler stamps state='submitted' + submitted_at=now on 'unknown';
    the guard's accounting must then count the full qty for the window."""
    now = datetime(2026, 11, 6, 15, 0, tzinfo=UTC)
    assert close_inflight_remaining(3, 0, now, now, now, _INFLIGHT_CLOSE_WINDOW, "submitted") == 3
    # ... and it keeps counting after the window (#486): the order may be live
    # at IB. The reaper settles it (adopts it by its orderRef, or expires it).
    later = now + _INFLIGHT_CLOSE_WINDOW + timedelta(seconds=1)
    assert close_inflight_remaining(3, 0, now, now, later, _INFLIGHT_CLOSE_WINDOW, "submitted") == 3
