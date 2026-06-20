"""Unit tests for core.risk.vega_pca — book vega → PCA-mode projection."""
from __future__ import annotations

from core.risk.vega_pca import cell_index, delta_index, project_vega, tenor_index


def test_tenor_index_buckets():
    assert tenor_index(30) == 0  # 1M
    assert tenor_index(60) == 1  # 2M
    assert tenor_index(180) == 5  # 6M
    assert tenor_index(400) == 5  # clamp to 6M


def test_delta_index_buckets():
    assert delta_index(-0.10) == 0  # 10dp
    assert delta_index(-0.25) == 1  # 25dp
    assert delta_index(0.50) == 2  # atm (call)
    assert delta_index(-0.50) == 2  # atm (put)
    assert delta_index(0.25) == 3  # 25dc
    assert delta_index(0.10) == 4  # 10dc


def test_cell_index_flat_layout():
    assert cell_index(30, -0.10) == 0  # 1M / 10dp
    assert cell_index(60, 0.50) == 7  # 2M / atm = 1*5 + 2


def test_project_vega_aligns_with_loading():
    n = 30
    vega = [0.0] * n
    vega[0] = 100.0
    loadings = [[0.0] * n for _ in range(3)]
    loadings[0][0] = 1.0  # PC1 loads only cell 0
    stds = [1.0] * n
    proj = project_vega(vega, loadings, stds)
    assert len(proj) == 3
    assert proj[0] == 100.0  # vega aligns fully with PC1
    assert proj[1] == 0.0


def test_project_vega_scales_by_stds():
    n = 30
    vega = [0.0] * n
    vega[3] = 10.0
    loadings = [[0.0] * n]
    loadings[0][3] = 2.0
    stds = [1.0] * n
    stds[3] = 0.5
    # proj = (vega * stds) · loading = 10 * 0.5 * 2 = 10
    assert project_vega(vega, loadings, stds) == [10.0]


def test_project_vega_dim_mismatch_returns_empty():
    assert project_vega([1.0, 2.0], [[1.0, 2.0, 3.0]], [1.0, 2.0]) == []
