from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
)
from typing import Any


class OrdersPanel(QWidget):
    # Build open-orders and fills tables.
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Open orders"))
        self.orders_table = QTableWidget(0, 6)
        self.orders_table.setHorizontalHeaderLabels(["Id", "Symbol", "Side", "Qty", "Price", "Status"])
        self.orders_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.orders_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.orders_table.setAlternatingRowColors(True)
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.orders_table.verticalHeader().setVisible(False)

        layout.addWidget(self.orders_table)
        layout.addWidget(QLabel("Recent fills"))

        self.fills_table = QTableWidget(0, 5)
        self.fills_table.setHorizontalHeaderLabels(["Time", "Symbol", "Side", "Qty", "Price"])
        self.fills_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fills_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fills_table.setAlternatingRowColors(True)
        self.fills_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fills_table.verticalHeader().setVisible(False)

        layout.addWidget(self.fills_table)

    # Refresh tables from normalized orders/fills payloads.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        open_orders = payload.get("open_orders") or []
        fills = payload.get("fills") or []

        self.orders_table.setRowCount(len(open_orders))
        for row, order in enumerate(open_orders):
            order_id = getattr(order, "orderId", None) or getattr(order, "id", "")
            symbol = getattr(order, "symbol", "")
            side = getattr(order, "action", None) or getattr(order, "side", "")
            qty = getattr(order, "totalQuantity", None) or getattr(order, "qty", "")
            price = getattr(order, "lmtPrice", None) or getattr(order, "price", "")
            status = getattr(order, "status", "")

            self.orders_table.setItem(row, 0, QTableWidgetItem(str(order_id)))
            self.orders_table.setItem(row, 1, QTableWidgetItem(str(symbol)))
            self.orders_table.setItem(row, 2, QTableWidgetItem(str(side)))
            self.orders_table.setItem(row, 3, QTableWidgetItem(str(qty)))
            self.orders_table.setItem(row, 4, QTableWidgetItem(str(price)))
            self.orders_table.setItem(row, 5, QTableWidgetItem(str(status)))

        self.fills_table.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            fill_time = getattr(fill, "time", "")
            symbol = getattr(fill, "symbol", "")
            side = getattr(fill, "side", "")
            qty = getattr(fill, "qty", "")
            price = getattr(fill, "price", "")

            self.fills_table.setItem(row, 0, QTableWidgetItem(str(fill_time)))
            self.fills_table.setItem(row, 1, QTableWidgetItem(str(symbol)))
            self.fills_table.setItem(row, 2, QTableWidgetItem(str(side)))
            self.fills_table.setItem(row, 3, QTableWidgetItem(str(qty)))
            self.fills_table.setItem(row, 4, QTableWidgetItem(str(price)))
