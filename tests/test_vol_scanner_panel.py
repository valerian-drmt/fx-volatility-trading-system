import pytest

from ui.panels.vol_scanner import VolScannerPanel


@pytest.mark.unit
def test_vol_scanner_panel_renders_rows(qapp):
    panel = VolScannerPanel()

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50, "sigma_fair_pct": 7.81},
            {"tenor": "3M", "delta_label": "25Dp", "strike": 1.060, "iv_market_pct": 8.22, "sigma_fair_pct": 8.45},
        ],
        "error": None,
    })

    assert panel.table.rowCount() == 2
    # Sorted by |ecart| desc: ATM ecart=+0.69 > 25Dp ecart=-0.23
    assert panel.table.item(0, 0).text() == "3M"
    assert panel.table.item(0, 1).text() == "ATM"
    assert panel.table.item(0, 3).text() == "8.50"
    assert panel.table.item(0, 4).text() == "7.81"
    assert panel.table.item(0, 6).text() == "EXPENSIVE"


@pytest.mark.unit
def test_vol_scanner_panel_signal_coloring(qapp):
    panel = VolScannerPanel()

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50, "sigma_fair_pct": 7.81},
            {"tenor": "3M", "delta_label": "25Dp", "strike": 1.060, "iv_market_pct": 8.22, "sigma_fair_pct": 8.60},
            {"tenor": "6M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 9.00, "sigma_fair_pct": 9.05},
        ],
        "error": None,
    })

    # Sorted by |ecart| desc
    assert panel.table.item(0, 6).text() == "EXPENSIVE"  # +0.69
    assert panel.table.item(1, 6).text() == "CHEAP"       # -0.38
    assert panel.table.item(2, 6).text() == "FAIR"         # -0.05


@pytest.mark.unit
def test_vol_scanner_panel_without_fair_vol(qapp):
    """When sigma_fair is not available, show IV but dash for fair/ecart/signal."""
    panel = VolScannerPanel()

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50},
        ],
        "error": None,
    })

    assert panel.table.rowCount() == 1
    assert panel.table.item(0, 3).text() == "8.50"
    assert panel.table.item(0, 4).text() == "—"
    assert panel.table.item(0, 6).text() == "—"


@pytest.mark.unit
def test_vol_scanner_panel_shows_error(qapp):
    panel = VolScannerPanel()

    panel.update({"error": "No liquid strikes", "scanner_rows": [], "spot": 0})

    assert panel.table.rowCount() == 0
    assert "error" in panel._title.text().lower()


@pytest.mark.unit
def test_vol_scanner_panel_handles_empty_payload(qapp):
    panel = VolScannerPanel()
    panel.update(None)
    assert panel.table.rowCount() == 0


@pytest.mark.unit
def test_vol_scanner_panel_row_clicked_signal(qapp):
    panel = VolScannerPanel()
    clicked = []
    panel.row_clicked.connect(lambda d: clicked.append(d))

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50, "sigma_fair_pct": 7.81},
        ],
        "error": None,
    })

    panel._on_cell_clicked(0, 0)
    assert len(clicked) == 1
    assert clicked[0]["tenor"] == "3M"
    assert clicked[0]["strike"] == 1.085
