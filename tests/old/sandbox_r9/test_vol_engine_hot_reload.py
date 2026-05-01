"""Unit tests for VolEngine.apply_config : hot reload of signal params."""
from __future__ import annotations

from unittest.mock import MagicMock

from core.config import VolTradingConfig
from engines.vol.engine import VolEngine


def _make_engine() -> VolEngine:
    return VolEngine(
        ib=MagicMock(),
        redis=MagicMock(),
        symbol="EURUSD",
        ib_host="localhost",
        ib_port=4002,
        client_id=99,
        fetch_fop_chain=MagicMock(),
        fetch_ohlc=MagicMock(),
        signal_threshold_vol_pts=1.0,
        signal_model_p="har",
    )


def test_apply_config_updates_threshold_and_model():
    engine = _make_engine()
    assert engine._signal_threshold == 1.0
    assert engine._signal_model_p == "har"

    cfg = VolTradingConfig()
    cfg = cfg.model_copy(update={
        "signal": cfg.signal.model_copy(update={
            "threshold_vol_pts": 2.5,
            "model_p": "garch",
        }),
    })
    engine.apply_config(cfg)

    assert engine._signal_threshold == 2.5
    assert engine._signal_model_p == "garch"


def test_apply_config_coerces_types():
    """apply_config must survive JSONB round-trip where floats arrive as int/str."""
    engine = _make_engine()

    class _SigStub:
        threshold_vol_pts = "2.75"  # simulate stringified JSON
        model_p = "ewma"

    class _CfgStub:
        signal = _SigStub()

    engine.apply_config(_CfgStub())
    assert engine._signal_threshold == 2.75
    assert engine._signal_model_p == "ewma"
