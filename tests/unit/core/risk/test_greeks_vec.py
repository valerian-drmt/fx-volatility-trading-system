"""Snapshot + equivalence tests for ``core.risk.greeks``.

Resurrected from ``tests/old/test_core_greeks.py`` (git 14175622~1).
"""
from __future__ import annotations

import numpy as np
import pytest

from core.pricing import bs as bs_scalar
from core.risk.greeks import bs_price_vec

pytestmark = pytest.mark.unit


def test_bs_price_vec_matches_scalar_pointwise():
    """Each vector element must equal the scalar BS price at the same F."""
    K, T, sigma, right = 1.08, 30 / 365, 0.075, "C"
    F_arr = np.array([1.05, 1.07, 1.08, 1.09, 1.11])

    vec = bs_price_vec(F_arr, K, T, sigma, right)
    scalar = np.array([bs_scalar.bs_price(F, K, T, sigma, right) for F in F_arr])

    np.testing.assert_allclose(vec, scalar, rtol=1e-12, atol=1e-12)


def test_bs_price_vec_at_expiry_is_the_intrinsic_vector():
    """QM-01 (#309): an option on expiry day is worth its intrinsic, not 0."""
    F_arr = np.linspace(1.0, 1.2, 20)
    out = bs_price_vec(F_arr, K=1.1, T=0.0, sigma=0.08, right="C")
    np.testing.assert_allclose(out, np.maximum(F_arr - 1.1, 0.0))
    assert out.shape == F_arr.shape
    scalar = np.array([bs_scalar.bs_price(F, 1.1, 0.0, 0.08, "C") for F in F_arr])
    np.testing.assert_allclose(out, scalar)


def test_bs_price_vec_guards_a_bad_forward_element():
    """QM-14 (#309): F <= 0 or NaN in the ladder prices 0 there, never NaN."""
    out = bs_price_vec(np.array([1.1, 0.0, float("nan")]), K=1.1, T=0.1, sigma=0.08, right="P")
    assert np.isfinite(out).all() and out[1] == 0.0 and out[2] == 0.0
    assert out[0] == pytest.approx(bs_scalar.bs_price(1.1, 1.1, 0.1, 0.08, "P"))


def test_bs_price_vec_put_call_parity_vectorised():
    F_arr = np.linspace(1.05, 1.12, 10)
    K, T, sigma = 1.10, 60 / 365, 0.07
    c = bs_price_vec(F_arr, K, T, sigma, "C")
    p = bs_price_vec(F_arr, K, T, sigma, "P")
    np.testing.assert_allclose(c - p, F_arr - K, atol=1e-10)


def test_bs_price_vec_snapshot():
    """Lock a deterministic output so future refactors of ``bs_price_vec``
    can't silently drift numerically."""
    F_arr = np.array([1.07, 1.08, 1.09])
    got = bs_price_vec(F_arr, K=1.08, T=30 / 365, sigma=0.075, right="C")
    expected = np.array([0.00507088, 0.00926405, 0.01514906])
    np.testing.assert_allclose(got, expected, atol=1e-7)
