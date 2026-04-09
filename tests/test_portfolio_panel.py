import pytest

from ui.panels.portfolio import PortfolioPanel


def _summary_item(tag: str, currency: str, value: str):
    return type("SummaryItem", (), {"tag": tag, "currency": currency, "value": value})()


@pytest.mark.unit
def test_portfolio_panel_renders_currency_balances(qapp):
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

    assert panel.usd_balance_label.text() == "750"
    assert panel.eur_balance_label.text() == "1,000"


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

    assert panel.eur_balance_label.text() == "1,100"


@pytest.mark.unit
def test_portfolio_panel_shows_placeholder_when_no_currency_balances(qapp):
    panel = PortfolioPanel()
    panel.update({"summary": [], "positions": []})

    assert panel.usd_balance_label.text() == "--"
    assert panel.eur_balance_label.text() == "--"


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

    assert "991,331.84" in panel.eur_balance_label.text()
    assert "23,082.4" in panel.usd_balance_label.text()
