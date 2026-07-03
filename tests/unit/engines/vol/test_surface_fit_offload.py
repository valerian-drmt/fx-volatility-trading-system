"""Unit tests for the off-loop SVI fit helper in the vol engine.

``_fit_svi_params_by_tenor`` was factored out of ``_compute_surface`` so the
per-tenor SVI least-squares phase can run via ``asyncio.to_thread``. These tests
pin the loop/skip contract (behaviour-preserving vs the previous inline loop),
isolated from the numerics by stubbing the per-tenor fit.
"""
from __future__ import annotations

from engines.vol import engine as vol_engine


def test_fit_svi_params_by_tenor_skips_unknown_tenor_and_none_fits(monkeypatch):
    calls: list[tuple] = []

    def fake_fit(obs, *, forward, tenor_years):
        calls.append((obs, forward, tenor_years))
        # sentinel "bad" mimics a failed calibration (fit returns None)
        return None if obs == "bad" else f"params-{tenor_years}"

    monkeypatch.setattr(vol_engine, "_fit_svi_from_triples", fake_fit)

    pillars_by_tenor = {"1W": "good", "1M": "bad", "3M": "good", "6M": "good"}
    tenor_years = {"1W": 0.02, "1M": 0.08, "3M": 0.25}  # 6M deliberately absent

    out = vol_engine._fit_svi_params_by_tenor(
        pillars_by_tenor, forward=1.1, tenor_years=tenor_years
    )

    # 6M has no year fraction -> skipped before the fit is ever attempted.
    assert "6M" not in out
    assert all(obs != "good" or fwd == 1.1 for obs, fwd, _ in calls)
    assert not any(ty is None for _, _, ty in calls)
    # 1M's fit returned None -> excluded from the result.
    assert "1M" not in out
    # Successful fits are kept, keyed by tenor, with forward threaded through.
    assert out == {"1W": "params-0.02", "3M": "params-0.25"}


def test_fit_svi_params_by_tenor_empty_input_is_empty(monkeypatch):
    monkeypatch.setattr(
        vol_engine, "_fit_svi_from_triples", lambda *a, **k: "x"
    )
    assert vol_engine._fit_svi_params_by_tenor({}, forward=1.0, tenor_years={}) == {}
