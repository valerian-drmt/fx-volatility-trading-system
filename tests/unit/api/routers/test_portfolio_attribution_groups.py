"""Unit tests for `_attribution_groups` — the pure pivot behind
`/portfolio/pnl-attribution?group_by=`.

The contract under test: a Taylor term nobody could measure over the window stays
`None` all the way to the payload. Summing those to `0.0` used to turn "no t-1
snapshot for any leg" into a table of "+$0" rows, which reads as a genuinely flat
P&L — the exact fabrication the desk must never show.
"""
from __future__ import annotations

from typing import Any

from api.routers.portfolio_panel import _attribution_groups

_TERMS = ["delta_pnl_usd", "gamma_pnl_usd", "vega_pnl_usd", "theta_pnl_usd"]


def _pos(tenor: str, wing: str = "Put wing", **terms: float | None) -> dict[str, Any]:
    """One per-position row; every Taylor term defaults to None (unmeasurable)."""
    row: dict[str, Any] = {
        "tenor": tenor, "wing": wing, "structure_type": "long call",
        "actual_pnl_usd": None, **dict.fromkeys(_TERMS, None),
    }
    row.update(terms)
    return row


def _measured(tenor: str, actual: float, d: float, g: float, v: float, th: float) -> dict[str, Any]:
    return _pos(
        tenor, actual_pnl_usd=actual,
        delta_pnl_usd=d, gamma_pnl_usd=g, vega_pnl_usd=v, theta_pnl_usd=th,
    )


def test_unmeasurable_bucket_stays_null_never_zero():
    groups, totals = _attribution_groups([_pos("1M"), _pos("2M")], "tenor")
    assert [g["label"] for g in groups] == ["1M", "2M"]
    for g in groups:
        assert g["actual_pnl_usd"] is None
        assert all(g[c] is None for c in _TERMS)
        assert g["residual_usd"] is None
    assert all(v is None for k, v in totals.items())


def test_only_tenors_the_book_holds_are_emitted():
    # No 4M/5M position → no 4M/5M row. An empty row reads "flat there", not
    # "nothing there".
    groups, _ = _attribution_groups([_pos("3M"), _pos("2W"), _pos("1M")], "tenor")
    assert [g["label"] for g in groups] == ["1M", "3M", "2W"]  # ladder first, extras after


def test_measured_bucket_sums_and_the_residual_foots():
    rows = [
        _measured("1M", actual=1_000.0, d=600.0, g=100.0, v=250.0, th=-50.0),
        _measured("1M", actual=500.0, d=200.0, g=50.0, v=300.0, th=-25.0),
    ]
    [g], totals = _attribution_groups(rows, "tenor")
    assert g["actual_pnl_usd"] == 1_500.0
    assert g["delta_pnl_usd"] == 800.0 and g["gamma_pnl_usd"] == 150.0
    assert g["vega_pnl_usd"] == 550.0 and g["theta_pnl_usd"] == -75.0
    # residual = actual − Σ terms → 1500 − 1425
    assert g["residual_usd"] == 75.0
    assert totals["actual_pnl_usd"] == 1_500.0 and totals["residual_usd"] == 75.0


def test_a_mixed_bucket_sums_only_the_legs_that_carry_the_term():
    # One measurable leg + one opened inside the window: the bucket reports the
    # measurable leg rather than dropping to None or padding the other with 0.
    rows = [_measured("1M", actual=400.0, d=300.0, g=50.0, v=80.0, th=-30.0), _pos("1M")]
    [g], _ = _attribution_groups(rows, "tenor")
    assert g["actual_pnl_usd"] == 400.0 and g["delta_pnl_usd"] == 300.0
    assert g["residual_usd"] == 0.0                      # 400 − 400, foots exactly


def test_residual_is_null_when_one_term_is_unknown():
    # actual + 3 of 4 terms known: the residual would silently absorb the 4th.
    rows = [_pos("1M", actual_pnl_usd=100.0, delta_pnl_usd=60.0, gamma_pnl_usd=10.0, vega_pnl_usd=20.0)]
    [g], totals = _attribution_groups(rows, "tenor")
    assert g["theta_pnl_usd"] is None and g["residual_usd"] is None
    assert totals["residual_usd"] is None


def test_missing_group_key_falls_into_other():
    groups, _ = _attribution_groups([_pos("1M"), _pos("")], "tenor")
    assert [g["label"] for g in groups] == ["1M", "other"]


def test_wing_pivot_keeps_smile_order():
    rows = [_pos("1M", wing="Call wing"), _pos("1M", wing="Put wing"), _pos("1M", wing="Body (ATM)")]
    groups, _ = _attribution_groups(rows, "wing")
    assert [g["label"] for g in groups] == ["Put wing", "Body (ATM)", "Call wing"]


def test_structure_pivot_ranks_by_absolute_pnl():
    a = _measured("1M", actual=-900.0, d=0.0, g=0.0, v=0.0, th=0.0)
    b = _measured("1M", actual=200.0, d=0.0, g=0.0, v=0.0, th=0.0)
    a["structure_type"], b["structure_type"] = "straddle", "long call"
    groups, _ = _attribution_groups([b, a], "structure")
    assert [g["label"] for g in groups] == ["straddle", "long call"]


def test_empty_book_yields_no_groups_and_null_totals():
    groups, totals = _attribution_groups([], "tenor")
    assert groups == []
    assert all(v is None for v in totals.values())
