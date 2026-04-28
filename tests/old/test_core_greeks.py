"""Snapshot + equivalence tests for ``core.risk.greeks``."""
from __future__ import annotations

import numpy as np
import pytest

from core.pricing import bs as bs_scalar
from core.risk.greeks import bs_price_vec


@pytest.mark.unit
def test_bs_price_vec_matches_scalar_pointwise():
    """Each vector element must equal the scalar BS price at the same F."""
    K, T, sigma, right = 1.08, 30 / 365, 0.075, "C"
    F_arr = np.array([1.05, 1.07, 1.08, 1.09, 1.11])

    vec = bs_price_vec(F_arr, K, T, sigma, right)
    scalar = np.array([bs_scalar.bs_price(F, K, T, sigma, right) for F in F_arr])

    np.testing.assert_allclose(vec, scalar, rtol=1e-12, atol=1e-12)


@pytest.mark.unit
def test_bs_price_vec_degenerate_returns_zero_vector():
    F_arr = np.linspace(1.0, 1.2, 20)
    zeros = bs_price_vec(F_arr, K=1.1, T=0.0, sigma=0.08, right="C")
    assert np.all(zeros == 0.0)
    assert zeros.shape == F_arr.shape


@pytest.mark.unit
def test_bs_price_vec_put_call_parity_vectorised():
    F_arr = np.linspace(1.05, 1.12, 10)
    K, T, sigma = 1.10, 60 / 365, 0.07
    c = bs_price_vec(F_arr, K, T, sigma, "C")
    p = bs_price_vec(F_arr, K, T, sigma, "P")
    np.testing.assert_allclose(c - p, F_arr - K, atol=1e-10)


@pytest.mark.unit
def test_bs_price_vec_snapshot():
    """Lock a deterministic output so future refactors of ``bs_price_vec``
    can't silently drift numerically."""
    F_arr = np.array([1.07, 1.08, 1.09])
    got = bs_price_vec(F_arr, K=1.08, T=30 / 365, sigma=0.075, right="C")
    expected = np.array([0.00507088, 0.00926405, 0.01514906])
    np.testing.assert_allclose(got, expected, atol=1e-7)
