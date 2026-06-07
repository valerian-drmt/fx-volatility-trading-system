"""Regime-conditioned multipliers on hot-reloadable risk_limits.

Pure helper. The seed migration (013) loads a flat set of limits identical
across regimes. Spec STEP3 references regime modulation of the *size* (via
``compute_sizing.regime_mult``) — we extend it here to *limits* themselves :
in stressed regimes we tighten max_book_vega + max_loss_per_trade, in
pre_event we drive both to zero (compatible with the existing pre-submit
``regime_not_pre_event`` gate which blocks anyway).

Multipliers are intentionally kept as a constant table : they are part of
the trading policy and should change via code review, not via DB hot-reload.
"""
from __future__ import annotations

from collections.abc import Mapping

# Per-regime multipliers applied to *limits* (not sizes). Mirrors the
# size-multiplier table in compute_sizing : a stressed regime allows ~70 %
# of the calm-regime risk envelope ; pre_event collapses it.
LIMIT_MULTIPLIERS: dict[str, float] = {
    "calm": 1.0,
    "stressed": 0.7,
    "pre_event": 0.0,
}

# Limit names that scale with the regime. Limits not in this set (e.g.
# preview_validity_seconds, min_liquidity_quoted_size, max_iv_data_age_seconds)
# are operational thresholds, not risk envelopes — they pass through unchanged.
SCALABLE_LIMITS: frozenset[str] = frozenset({
    "max_loss_per_trade_pct",
    "max_book_vega_usd",
    "max_book_vega_per_tenor_usd",
    "max_n_open_structures",
})


def regime_label(regime: Mapping[str, object] | None) -> str:
    """Normalise regime payload to a lower-case label. ``None`` → ``calm``."""
    if regime is None:
        return "calm"
    raw = regime.get("regime") or regime.get("label") or "calm"
    return str(raw).lower()


def apply_regime_to_limits(
    limits: Mapping[str, float], regime: Mapping[str, object] | None,
) -> dict[str, float]:
    """Return a new dict where SCALABLE_LIMITS are multiplied by the regime
    multiplier. Unknown regime label → multiplier 1.0 (calm-equivalent)."""
    label = regime_label(regime)
    mult = LIMIT_MULTIPLIERS.get(label, 1.0)
    out = dict(limits)
    if mult == 1.0:
        return out
    for name in SCALABLE_LIMITS:
        if name in out:
            out[name] = out[name] * mult
    return out
