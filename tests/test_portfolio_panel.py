import pytest

from ui.panels.account_summary_panel import PortfolioPanel


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

    assert panel.usd_balance_label.text() == "0.8k"
    assert panel.eur_balance_label.text() == "1.0k"


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

    assert panel.eur_balance_label.text() == "1.1k"


@pytest.mark.unit
def test_portfolio_panel_shows_placeholder_when_no_currency_balances(qapp):
    panel = PortfolioPanel()
    panel.update({"summary": [], "positions": []})

    assert panel.usd_balance_label.text() == "--"
    assert panel.eur_balance_label.text() == "--"


@pytest.mark.unit
def test_portfolio_panel_reset_clears_all_fields(qapp):
    panel = PortfolioPanel()
    panel.update(
        {
            "summary": [_summary_item("TotalCashBalance", "USD", "500")],
            "positions": [],
        }
    )
    panel.reset()

    assert panel.usd_balance_label.text() == "--"
    for label in panel.fields.values():
        assert label.text() == "--"
