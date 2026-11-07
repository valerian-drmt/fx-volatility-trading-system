"""One Black-Scholes kernel (#331, audit 06#3 / QM-09).

Three pricers used to live side by side (``core/pricing/bs.py``,
``core/trade_preview.py``, ``engines/execution/structures.py``) and
disagreed at expiry. The preview and the structures now call the kernel;
this test pins (a) the wrappers against the kernel on a grid, (b) the
kernel against the two retired formulas (copied here verbatim) away from
the boundary, and (c) the agreed boundary: intrinsic at ``T <= 0`` /
``sigma <= 0``.
"""
from __future__ import annotations

import math

import pytest

from core import structures as S
from core import trade_preview as tp
from core.pricing import bs

GRID = [
    (F, K, T, sigma)
    for F in (1.05, 1.10, 1.17)
    for K in (1.00, 1.10, 1.25)
    for T in (7 / 365, 30 / 365, 90 / 365, 1.0)
    for sigma in (0.04, 0.07, 0.15)
]


# The retired formula from engines/execution/structures.py, verbatim.
def _legacy_structures_price(F: float, K: float, T: float, sigma: float, right: str) -> float:
    def _N(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return max(0.0, (F - K) if right == "CALL" else (K - F))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "CALL":
        return F * _N(d1) - K * _N(d2)
    return K * _N(-d2) - F * _N(-d1)


def _legacy_structures_greeks(F: float, K: float, T: float, sigma: float, right: str) -> dict[str, float]:
    def _phi(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def _N(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    pdf = _phi(d1)
    return {
        "vega": F * pdf * sqrt_t / 100.0,
        "gamma": pdf / (F * sigma * sqrt_t),
        "theta": -(F * pdf * sigma) / (2.0 * sqrt_t) / 365.0,
        "delta": _N(d1) if right == "CALL" else _N(d1) - 1.0,
    }


@pytest.mark.parametrize("F,K,T,sigma", GRID)
def test_preview_wrappers_equal_the_kernel(F: float, K: float, T: float, sigma: float) -> None:
    for right_tp, right_k in (("call", "C"), ("put", "P")):
        assert tp.bs_price(F, K, T, sigma, right_tp) == bs.bs_price(F, K, T, sigma, right_k)
        g = tp.bs_greeks(F, K, T, sigma, right_tp)
        assert g["delta"] == bs.bs_delta(F, K, T, sigma, right_k)
        assert g["gamma"] == bs.bs_gamma(F, K, T, sigma)
        assert g["vega"] == bs.bs_vega(F, K, T, sigma)
        assert g["theta"] == pytest.approx(bs.bs_theta(F, K, T, sigma, right_k) * 365.0)


@pytest.mark.parametrize("F,K,T,sigma", GRID)
def test_kernel_matches_the_retired_structures_formula(F: float, K: float, T: float, sigma: float) -> None:
    for right_s, right_k in (("CALL", "C"), ("PUT", "P")):
        assert bs.bs_price(F, K, T, sigma, right_k) == pytest.approx(
            _legacy_structures_price(F, K, T, sigma, right_s), rel=1e-9, abs=1e-12)
        legacy = _legacy_structures_greeks(F, K, T, sigma, right_s)
        assert bs.bs_delta(F, K, T, sigma, right_k) == pytest.approx(legacy["delta"], rel=1e-9)
        assert bs.bs_gamma(F, K, T, sigma) == pytest.approx(legacy["gamma"], rel=1e-9)
        assert bs.bs_vega(F, K, T, sigma) / 100.0 == pytest.approx(legacy["vega"], rel=1e-9)
        assert bs.bs_theta(F, K, T, sigma, right_k) == pytest.approx(legacy["theta"], rel=1e-9)


def test_structures_leg_premium_is_the_kernel_on_the_one_tenor_map() -> None:
    from core.vol.tenors import tenor_years

    F, K, iv = 1.10, 1.12, 0.07
    assert S.leg_premium(F, K, "3M", iv, "CALL") == bs.bs_price(F, K, tenor_years("3M"), iv, "C")
    assert S.leg_premium(F, K, "3M", iv, "PUT") == bs.bs_price(F, K, tenor_years("3M"), iv, "P")
    assert S.leg_premium(F, K, "7M", iv, "CALL") == 0.0, "unknown tenor label prices nothing"
    assert tenor_years("1M") == pytest.approx(30 / 365), "30 calendar days, not 1/12 of a year"


@pytest.mark.parametrize("right", ["C", "P"])
def test_boundary_is_intrinsic_everywhere(right: str) -> None:
    """At expiry or zero vol the three former call sites now agree."""
    for F, K in ((1.12, 1.10), (1.10, 1.12), (1.10, 1.10)):
        intrinsic = max(F - K, 0.0) if right == "C" else max(K - F, 0.0)
        for T, sigma in ((0.0, 0.07), (30 / 365, 0.0), (-1.0, 0.07)):
            assert bs.bs_price(F, K, T, sigma, right) == intrinsic
            assert tp.bs_price(F, K, T, sigma, "call" if right == "C" else "put") == intrinsic
            assert bs.bs_gamma(F, K, T, sigma) == bs.bs_vega(F, K, T, sigma) == 0.0
            assert bs.bs_theta(F, K, T, sigma, right) == 0.0
            step = (1.0 if F > K else 0.0) if right == "C" else (-1.0 if F < K else 0.0)
            assert bs.bs_delta(F, K, T, sigma, right) == step
    # Not an option at all: F or K non-positive prices 0 with zero greeks.
    assert bs.bs_price(0.0, 1.0, 0.1, 0.1, right) == 0.0 and bs.bs_delta(1.0, 0.0, 0.1, 0.1, right) == 0.0


def test_put_call_parity_holds_through_the_boundary() -> None:
    F, K = 1.10, 1.08
    for T, sigma in ((0.25, 0.07), (0.0, 0.07), (0.25, 0.0)):
        c = bs.bs_price(F, K, T, sigma, "C")
        p = bs.bs_price(F, K, T, sigma, "P")
        assert c - p == pytest.approx(F - K, abs=1e-10)
