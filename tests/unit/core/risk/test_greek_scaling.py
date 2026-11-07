"""core.risk.greek_scaling — one scaling path for persisted and published greeks (#313).

The bug: the published aggregate had no multiplier (delta 5.05 vs 630 718 in
the table for the same 10 lots, R3-07) and delta_usd was EUR notional (R3-13).
"""
from __future__ import annotations

import pytest

from core.pricing.bs import bs_delta, bs_gamma, bs_theta, bs_vanna, bs_vega, bs_volga
from core.risk.greek_scaling import future_greeks, option_greeks, position_greeks, sum_greeks
from core.units import EUR_FOP_MULTIPLIER, PIP_SIZE, VOLPT

F, K, T, IV = 1.12, 1.12, 30 / 365, 0.08


def test_option_greeks_follow_the_documented_formulas() -> None:
    g = option_greeks(qty=10, mult=EUR_FOP_MULTIPLIER, F=F, K=K, T=T, iv=IV, right="C")
    qm = 10 * EUR_FOP_MULTIPLIER
    assert g.delta_eur == pytest.approx(qm * bs_delta(F, K, T, IV, "C"))
    assert g.delta_usd == pytest.approx(g.delta_eur * F)              # USD = EUR exposure × spot
    assert g.gamma_usd_per_pip == pytest.approx(qm * bs_gamma(F, K, T, IV) * PIP_SIZE)
    assert g.vega_usd_per_volpt == pytest.approx(qm * bs_vega(F, K, T, IV) * VOLPT)
    assert g.theta_usd_per_day == pytest.approx(qm * bs_theta(F, K, T, IV, "C"))
    assert g.vanna_usd_per_volpt == pytest.approx(qm * bs_vanna(F, K, T, IV) * VOLPT)
    assert g.volga_usd_per_volpt2 == pytest.approx(qm * bs_volga(F, K, T, IV) * VOLPT ** 2)
    # orders of magnitude a trader recognises for 10 ATM 1M lots at 8 %
    assert 600_000 < g.delta_eur < 700_000
    assert 1_000 < g.vega_usd_per_volpt < 2_000


def test_short_position_flips_every_sign() -> None:
    long = option_greeks(qty=10, mult=EUR_FOP_MULTIPLIER, F=F, K=K, T=T, iv=IV, right="P")
    short = option_greeks(qty=-10, mult=EUR_FOP_MULTIPLIER, F=F, K=K, T=T, iv=IV, right="P")
    for k, v in long.as_dict().items():
        assert short.as_dict()[k] == pytest.approx(-v), k


def test_future_is_delta_one_and_micro_is_a_tenth() -> None:
    full = future_greeks(3, 125_000, F)
    micro = future_greeks(3, 12_500, F)
    assert full.delta_eur == 375_000 and full.delta_usd == pytest.approx(375_000 * F)
    assert micro.delta_eur == pytest.approx(full.delta_eur / 10)   # R3-07: was counted the same
    assert full.gamma_usd_per_pip == full.vega_usd_per_volpt == full.theta_usd_per_day == 0.0


def test_dispatch_returns_none_when_an_option_cannot_be_priced() -> None:
    assert position_greeks(instrument_type="OPTION", right="C", qty=1, mult=125_000, F=F, K=K, T=T, iv=None) is None
    assert position_greeks(instrument_type="OPTION", right="C", qty=1, mult=125_000, F=F, K=0, T=T, iv=IV) is None
    assert position_greeks(instrument_type="FUTURE", right=None, qty=2, mult=125_000, F=F, K=None, T=None, iv=None).delta_eur == 250_000


def test_sum_is_linear_so_published_equals_sum_of_persisted() -> None:
    a = option_greeks(qty=10, mult=125_000, F=F, K=1.10, T=T, iv=0.07, right="C")
    b = option_greeks(qty=-5, mult=125_000, F=F, K=1.14, T=0.25, iv=0.065, right="P")
    c = future_greeks(-4, 125_000, F)
    s = sum_greeks([a, b, c])
    for k in s.as_dict():
        assert s.as_dict()[k] == pytest.approx(a.as_dict()[k] + b.as_dict()[k] + c.as_dict()[k]), k
    assert sum_greeks([]).delta_usd == 0.0
