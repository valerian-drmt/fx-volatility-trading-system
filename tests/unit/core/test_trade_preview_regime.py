"""Unit tests for core.trade_preview_regime.

Pure-helper coverage : regime label normalisation + multiplier application
on the scalable risk-limit subset.
"""
from __future__ import annotations

import pytest

from core.trade_preview_regime import (
    LIMIT_MULTIPLIERS,
    SCALABLE_LIMITS,
    apply_regime_to_limits,
    regime_label,
)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, "calm"),
        ({}, "calm"),
        ({"label": "stressed"}, "stressed"),
        ({"regime": "PRE_EVENT"}, "pre_event"),
        ({"label": "Calm", "event_dampener": True}, "calm"),
    ],
)
def test_regime_label_normalises(payload, expected):
    assert regime_label(payload) == expected


def test_calm_passes_limits_through_unchanged():
    limits = {
        "max_book_vega_usd": 5000.0,
        "max_loss_per_trade_pct": 2.0,
        "preview_validity_seconds": 120.0,
    }
    out = apply_regime_to_limits(limits, {"label": "calm"})
    assert out == limits
    # Returns a fresh dict (caller can mutate safely).
    out["max_book_vega_usd"] = 0
    assert limits["max_book_vega_usd"] == 5000.0


def test_stressed_scales_only_scalable_subset():
    limits = {
        "max_book_vega_usd": 5000.0,
        "max_book_vega_per_tenor_usd": 2000.0,
        "max_n_open_structures": 8,
        "max_loss_per_trade_pct": 2.0,
        "preview_validity_seconds": 120.0,
        "min_liquidity_quoted_size": 10,
    }
    out = apply_regime_to_limits(limits, {"label": "stressed"})
    expected_mult = LIMIT_MULTIPLIERS["stressed"]
    for name in SCALABLE_LIMITS & limits.keys():
        assert out[name] == pytest.approx(limits[name] * expected_mult)
    # Non-scalable limits untouched
    assert out["preview_validity_seconds"] == 120.0
    assert out["min_liquidity_quoted_size"] == 10


def test_pre_event_collapses_envelope_to_zero():
    limits = {"max_book_vega_usd": 5000.0, "max_loss_per_trade_pct": 2.0}
    out = apply_regime_to_limits(limits, {"label": "pre_event"})
    assert out["max_book_vega_usd"] == 0.0
    assert out["max_loss_per_trade_pct"] == 0.0


def test_unknown_regime_falls_back_to_passthrough():
    limits = {"max_book_vega_usd": 5000.0}
    out = apply_regime_to_limits(limits, {"label": "weirdmode"})
    assert out == limits


def test_none_regime_passthrough():
    limits = {"max_book_vega_usd": 5000.0}
    out = apply_regime_to_limits(limits, None)
    assert out == limits
