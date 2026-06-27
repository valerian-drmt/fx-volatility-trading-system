"""Unit tests for core.config_catalog (the editable Settings catalog)."""
from __future__ import annotations

from core import config_catalog as cc


def test_domains_cover_the_four_tabs():
    assert set(cc.DOMAINS) == {"trade", "signal", "risk", "portfolio"}
    for params in cc.DOMAINS.values():
        assert params, "every domain has at least one knob"
        names = [p.name for p in params]
        assert len(names) == len(set(names)), "no duplicate knobs within a domain"


def test_risk_domain_mirrors_greek_limits_defaults():
    from core.risk import greek_limits as gl
    risk = {p.name: p for p in cc.DOMAINS["risk"]}
    assert set(risk) == set(gl.CONFIG_DEFAULTS)
    for name, default in gl.CONFIG_DEFAULTS.items():
        assert risk[name].namespace == "greek_limits"
        assert risk[name].default == default


def test_param_lookup():
    assert cc.param("trade", "base_qty") is not None
    assert cc.param("trade", "nope") is None
    assert cc.param("nodomain", "base_qty") is None


def test_validate_bounds_by_unit():
    # weight ∈ [0,1]
    w = cc.param("trade", "book_alpha")
    assert w is not None
    assert cc.validate(w, 0.3) is None
    assert cc.validate(w, 1.5) is not None
    # frac_capital ∈ (0,1]
    a = cc.param("risk", "alpha")
    assert a is not None
    assert cc.validate(a, 0.05) is None
    assert cc.validate(a, 0.0) is not None
    # days ≥ 1
    d = cc.param("portfolio", "var_max_gap_days")
    assert d is not None
    assert cc.validate(d, 3) is None
    assert cc.validate(d, 0) is not None
    # usd ≥ 0
    u = cc.param("trade", "max_book_vega_usd")
    assert u is not None
    assert cc.validate(u, 5000) is None
    assert cc.validate(u, -1) is not None
