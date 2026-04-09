import queue

import pytest

from services.vol_engine import VolEngine


def _make_chain_msg(spot=1.085):
    """Build a realistic input queue message with 5 strikes, call+put."""
    strikes = [1.04, 1.06, 1.085, 1.11, 1.13]
    ivs = [0.095, 0.085, 0.080, 0.083, 0.092]
    rows = []
    for k, iv in zip(strikes, ivs):
        for right in ["C", "P"]:
            rows.append({
                "strike": k,
                "right": right,
                "iv_raw": iv + (0.001 if right == "P" else 0),
                "bid": iv * 10 * 0.95,
                "ask": iv * 10,
                "volume": 50,
                "delta_ib": 0.5 if right == "C" else -0.5,
            })
    return {
        "type": "chain_data",
        "spot": spot,
        "chains": {
            "20260620": {
                "tenor": "3M",
                "T": 0.25,
                "rows": rows,
            }
        },
    }


@pytest.mark.unit
def test_vol_engine_processes_chain_data():
    in_q = queue.Queue()
    out_q = queue.Queue()
    engine = VolEngine(in_q, out_q)
    engine.start()

    in_q.put(_make_chain_msg())
    result = out_q.get(timeout=5)
    engine.stop()
    engine.join(timeout=2)

    assert result["type"] == "step1_result"
    assert result["error"] is None
    assert result["spot"] == 1.085
    assert len(result["scanner_rows"]) == 5  # 5 delta pillars
    labels = [r["delta_label"] for r in result["scanner_rows"]]
    assert "ATM" in labels


@pytest.mark.unit
def test_vol_engine_handles_empty_chains():
    in_q = queue.Queue()
    out_q = queue.Queue()
    engine = VolEngine(in_q, out_q)
    engine.start()

    in_q.put({"type": "chain_data", "spot": 1.085, "chains": {}})
    result = out_q.get(timeout=5)
    engine.stop()
    engine.join(timeout=2)

    assert result["error"] is not None
    assert result["scanner_rows"] == []


@pytest.mark.unit
def test_vol_engine_stop_lifecycle():
    in_q = queue.Queue()
    out_q = queue.Queue()
    engine = VolEngine(in_q, out_q)
    engine.start()
    assert engine.is_alive()

    engine.stop()
    engine.join(timeout=2)
    assert not engine.is_alive()
