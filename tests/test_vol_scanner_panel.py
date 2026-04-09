import pytest

from ui.panels.vol_scanner import VolScannerPanel


@pytest.mark.unit
def test_vol_scanner_panel_renders_rows(qapp):
    panel = VolScannerPanel()

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50},
            {"tenor": "3M", "delta_label": "25Δp", "strike": 1.060, "iv_market_pct": 8.22},
        ],
        "error": None,
    })

    assert panel.table.rowCount() == 2
    # Sorted by tenor then delta: 25Δp first, ATM second
    assert panel.table.item(0, 1).text() == "25Δp"
    assert panel.table.item(1, 1).text() == "ATM"
    assert panel.table.item(1, 3).text() == "8.50"


@pytest.mark.unit
def test_vol_scanner_panel_sorts_by_tenor_and_delta(qapp):
    panel = VolScannerPanel()

    panel.update({
        "spot": 1.085,
        "scanner_rows": [
            {"tenor": "6M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 9.00},
            {"tenor": "1M", "delta_label": "10Δc", "strike": 1.10, "iv_market_pct": 8.40},
            {"tenor": "1M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 7.80},
        ],
        "error": None,
    })

    assert panel.table.rowCount() == 3
    assert panel.table.item(0, 0).text() == "1M"
    assert panel.table.item(0, 1).text() == "ATM"
    assert panel.table.item(1, 0).text() == "1M"
    assert panel.table.item(1, 1).text() == "10Δc"
    assert panel.table.item(2, 0).text() == "6M"


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
            {"tenor": "3M", "delta_label": "ATM", "strike": 1.085, "iv_market_pct": 8.50},
        ],
        "error": None,
    })

    panel._on_cell_clicked(0, 0)
    assert len(clicked) == 1
    assert clicked[0]["tenor"] == "3M"
    assert clicked[0]["strike"] == 1.085
