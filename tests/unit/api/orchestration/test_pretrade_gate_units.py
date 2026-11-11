"""The pre-trade gate reads the armed preview's greeks in the units the preview
writes them (core.trade_preview._leg_greeks). Delta is already a USD exposure
(bs_delta × spot × multiplier); scaling it again by 125 000 × spot made the
delta cap refuse any trade that was not delta-neutral."""
from __future__ import annotations


def test_delta_reaches_the_gate_as_the_preview_wrote_it() -> None:
    from api.orchestration.pretrade_gate import trade_greeks_from_preview

    payload = {
        "greeks_net": {"vega_usd_per_volpt": 250.0, "gamma_usd_per_pip2": 0.5, "delta_unhedged": 73_000.0},
        "structure": {"legs": [{"qty": 1}], "future_multiplier_eur": 125_000},
    }
    g = trade_greeks_from_preview(payload, spot=1.16)
    assert g is not None
    assert g.delta_usd == 73_000.0
    assert g.notional_eur == 125_000
