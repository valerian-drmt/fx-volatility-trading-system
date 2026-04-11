import pytest

from ui.panels.vol_scanner_panel import VolScannerPanel


@pytest.mark.unit
def test_vol_scanner_renders_6_rows(qapp):
    panel = VolScannerPanel()
    panel.update({
        "scanner_rows": [
            {"tenor": "1M", "dte": 27, "sigma_mid_pct": 6.66, "sigma_fair_pct": 7.78,
             "ecart_pct": 1.12, "signal": "CHEAP", "RV_pct": 7.75, "RR25_pct": -0.73, "BF25_pct": 0.26},
            {"tenor": "3M", "dte": 82, "sigma_mid_pct": 6.48, "sigma_fair_pct": 7.84,
             "ecart_pct": 1.37, "signal": "CHEAP", "RV_pct": 7.87, "RR25_pct": -0.38, "BF25_pct": 0.28},
        ],
        "error": None,
    })
    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 0).text() == "1M"
    assert panel.table.item(0, 5).text() == "CHEAP"


@pytest.mark.unit
def test_vol_scanner_shows_error(qapp):
    panel = VolScannerPanel()
    panel.update({"error": "No data", "scanner_rows": []})
    assert panel.table.rowCount() == 0


@pytest.mark.unit
def test_vol_scanner_handles_none(qapp):
    panel = VolScannerPanel()
    panel.update(None)
    assert panel.table.rowCount() == 0


@pytest.mark.unit
def test_vol_scanner_row_click(qapp):
    panel = VolScannerPanel()
    clicked = []
    panel.row_clicked.connect(lambda d: clicked.append(d))
    panel.update({
        "scanner_rows": [
            {"tenor": "3M", "dte": 82, "sigma_mid_pct": 6.48,
             "sigma_fair_pct": None, "ecart_pct": None, "signal": None,
             "RV_pct": None, "RR25_pct": None, "BF25_pct": None},
        ],
        "error": None,
    })
    panel._on_cell_clicked(0, 0)
    assert len(clicked) == 1
    assert clicked[0]["tenor"] == "3M"
