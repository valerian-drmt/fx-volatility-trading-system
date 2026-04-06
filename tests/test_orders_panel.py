from types import SimpleNamespace

import pytest

from ui.panels.orders_panel import OrdersPanel


@pytest.mark.unit
def test_orders_panel_renders_open_order_type_column(qapp):
    panel = OrdersPanel()

    panel.update(
        {
            "open_orders": [
                {
                    "orderId": 42,
                    "symbol": "EUR",
                    "action": "BUY",
                    "orderType": "LMT",
                    "totalQuantity": 20000,
                    "lmtPrice": 1.1012,
                    "status": "Submitted",
                }
            ],
            "fills": [],
        }
    )

    assert panel.orders_table.item(0, 0).text() == "42"
    assert panel.orders_table.item(0, 3).text() == "LMT"
    assert panel.orders_table.item(0, 6).text() == "Submitted"


@pytest.mark.unit
def test_orders_panel_renders_nested_ib_fill_payload(qapp):
    panel = OrdersPanel()
    fill = SimpleNamespace(
        time=None,
        contract=SimpleNamespace(localSymbol="EUR.USD", symbol="EUR"),
        execution=SimpleNamespace(time="2026-04-06 09:45:10", side="BOT", shares=20000, price=1.10234),
    )

    panel.update({"open_orders": [], "fills": [fill]})

    assert panel.fills_table.item(0, 0).text() == "2026-04-06 09:45:10"
    assert panel.fills_table.item(0, 1).text() == "EUR.USD"
    assert panel.fills_table.item(0, 2).text() == "BUY"
    assert panel.fills_table.item(0, 3).text() == "20000"
    assert panel.fills_table.item(0, 4).text() == "1.10234"
