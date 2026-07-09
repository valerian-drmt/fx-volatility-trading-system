"""Unit tests for the marketable-limit pricing decision.

Why it matters: a limit at the theoretical preview premium doesn't cross (a BUY
sits below the ask, a SELL above the bid), so a leg priced that way hangs
'submitted'. The order must price at the LIVE touch when a quote exists — the
reason a strangle leg lags while a standalone vanilla (warm quote) fills fast.
"""
from __future__ import annotations

from engines.execution.live_submit import marketable_from_quote

_TICK = 0.0001


def test_buy_prices_at_the_ask_snapped_up() -> None:
    # BUY crosses at the ask, rounded UP to the tick so it's marketable.
    assert marketable_from_quote("BUY", 0.0100, 0.01234, 0.011, 0.009) == 0.0124


def test_sell_prices_at_the_bid_snapped_down() -> None:
    # SELL crosses at the bid, rounded DOWN to the tick.
    assert marketable_from_quote("SELL", 0.01236, 0.0130, 0.012, 0.015) == 0.0123


def test_buy_without_ask_falls_back_to_theoretical() -> None:
    # No ask and no mkt → fall back to the preview limit (the non-marketable case
    # that hangs — the bug we warm the quote to avoid).
    assert marketable_from_quote("BUY", 0.0100, None, None, 0.0091) == 0.0091


def test_sell_without_bid_falls_back_to_theoretical() -> None:
    assert marketable_from_quote("SELL", None, 0.0130, None, 0.0125) == 0.0125


def test_marketprice_used_when_touch_side_missing() -> None:
    # BUY with no ask but a marketPrice → price off mkt, still marketable-ish.
    assert marketable_from_quote("BUY", None, None, 0.0117, 0.009) == 0.0117
    # SELL with no bid but a marketPrice → price off mkt.
    assert marketable_from_quote("SELL", None, None, 0.01151, 0.009) == 0.0115
