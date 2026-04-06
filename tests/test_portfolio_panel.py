import pytest

from ui.panels.portfolio_panel import PortfolioPanel


def _summary_item(tag: str, currency: str, value: str):
    return type("SummaryItem", (), {"tag": tag, "currency": currency, "value": value})()


@pytest.mark.unit
def test_portfolio_panel_renders_currency_holdings_with_percentages(qapp):
    panel = PortfolioPanel()
    panel.update(
        {
            "summary": [
                _summary_item("TotalCashBalance", "EUR", "1000"),
                _summary_item("TotalCashBalance", "USD", "750"),
            ],
            "positions": [],
        }
    )

    text = panel.currency_holdings_label.text()
    assert "EUR" in text and "1,000 (57.1%)" in text
    assert "USD" in text and "750 (42.9%)" in text
    assert len(panel.currency_chart._segments) == 2
    assert panel.currency_chart._segments[0][0] == "EUR"
    assert panel.currency_chart._segments[1][0] == "USD"
    assert panel.currency_chart._segments[0][2].name() in text
    assert panel.currency_chart._segments[1][2].name() in text


@pytest.mark.unit
def test_portfolio_panel_prefers_total_cash_balance_over_available_funds(qapp):
    panel = PortfolioPanel()
    panel.update(
        {
            "summary": [
                _summary_item("AvailableFunds", "EUR", "300"),
                _summary_item("CashBalance", "EUR", "900"),
                _summary_item("TotalCashBalance", "EUR", "1100"),
            ],
            "positions": [],
        }
    )

    text = panel.currency_holdings_label.text()
    assert "EUR" in text
    assert "1,100 (100.0%)" in text
    assert len(panel.currency_chart._segments) == 1
    assert panel.currency_chart._segments[0][0] == "EUR"
    assert panel.currency_chart._segments[0][2].name() in text


@pytest.mark.unit
def test_portfolio_panel_shows_placeholder_when_no_currency_balances(qapp):
    panel = PortfolioPanel()
    panel.update({"summary": [], "positions": []})

    assert panel.currency_holdings_label.text() == "--"
    assert panel.currency_chart._segments == []


@pytest.mark.unit
def test_portfolio_panel_excludes_base_from_currency_breakdown(qapp):
    panel = PortfolioPanel()
    panel.update(
        {
            "summary": [
                _summary_item("TotalCashBalance", "BASE", "1011325.15"),
                _summary_item("TotalCashBalance", "EUR", "991331.84"),
                _summary_item("TotalCashBalance", "USD", "23082.4"),
            ],
            "positions": [],
        }
    )

    text = panel.currency_holdings_label.text()
    assert "BASE" not in text
    assert "Total" not in text
    assert "EUR</span>: 991,331.84 (97.7%)" in text
    assert "USD</span>: 23,082.4 (2.3%)" in text
    segment_names = [name for name, _ratio, _color in panel.currency_chart._segments]
    assert segment_names == ["EUR", "USD"]
