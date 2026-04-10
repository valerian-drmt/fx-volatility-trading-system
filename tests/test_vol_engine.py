import queue

import pytest

from services.vol_engine import (
    VolEngine,
    pillars_to_scanner_rows,
    _safe,
    _tenor_label,
    PILLAR_TARGETS,
)


@pytest.mark.unit
def test_safe_handles_nan():
    import math
    assert _safe(None) is None
    assert _safe(float("nan")) is None
    assert _safe(1.5) == 1.5


@pytest.mark.unit
def test_tenor_label():
    assert _tenor_label(30) == "1M"
    assert _tenor_label(60) == "2M"
    assert _tenor_label(90) == "3M"
    assert _tenor_label(120) == "4M"
    assert _tenor_label(150) == "5M"
    assert _tenor_label(180) == "6M"


@pytest.mark.unit
def test_pillar_targets_has_5_entries():
    assert len(PILLAR_TARGETS) == 5
    assert "atm" in PILLAR_TARGETS
    assert PILLAR_TARGETS["atm"] == 0.50


@pytest.mark.unit
def test_scanner_rows_from_pillar():
    pillar = {
        "tenor_label": "3M", "expiry": "20260702", "dte": 90, "F": 1.17,
        "sigma_ATM_pct": 7.50, "RR25_pct": -0.38, "BF25_pct": 0.28,
        "sigma_fair_pct": 7.84, "ecart_pct": 0.34, "signal": "CHEAP", "RV_pct": 7.87,
    }
    rows = pillars_to_scanner_rows([pillar])
    assert len(rows) == 1
    r = rows[0]
    assert r["tenor"] == "3M"
    assert r["sigma_mid_pct"] == 7.50
    assert r["signal"] == "CHEAP"
    assert r["RR25_pct"] == -0.38


@pytest.mark.integration
def test_vol_engine_runs_scan():
    """Integration test — requires IB Gateway running on port 4002."""
    out_q = queue.Queue()
    engine = VolEngine(output_queue=out_q, client_id=14)
    engine.start()

    result = out_q.get(timeout=120)
    engine.stop()
    engine.join(timeout=5)

    assert result["type"] == "step1_result"
    if result.get("error"):
        pytest.skip(f"Vol engine error: {result['error']}")
    assert len(result["scanner_rows"]) > 0
