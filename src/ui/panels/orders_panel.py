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
        self.orders_table = QTableWidget(0, 7)
        self.orders_table.setHorizontalHeaderLabels(["Id", "Symbol", "Side", "Type", "Qty", "Price", "Status"])
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

    @staticmethod
    # Return the first non-empty value from a list of candidates.
    def _coalesce(*values: Any) -> Any:
        for value in values:
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
        return ""

    @staticmethod
    # Normalize execution side labels to common BUY/SELL names.
    def _normalize_side(value: Any) -> str:
        side = str(value or "").strip().upper()
        if side == "BOT":
            return "BUY"
        if side == "SLD":
            return "SELL"
        return side

    # Extract display fields from supported fill payload shapes.
    def _extract_fill_row(self, fill: Any) -> tuple[str, str, str, str, str]:
        if isinstance(fill, dict):
            fill_time = self._coalesce(fill.get("time"), fill.get("timestamp"))
            symbol = self._coalesce(fill.get("symbol"), fill.get("local_symbol"))
            side = self._normalize_side(fill.get("side"))
            qty = self._coalesce(fill.get("qty"), fill.get("quantity"), fill.get("shares"))
            price = self._coalesce(fill.get("price"), fill.get("avg_price"))
            return str(fill_time), str(symbol), str(side), str(qty), str(price)

        contract = getattr(fill, "contract", None)
        execution = getattr(fill, "execution", None)
        fill_time = self._coalesce(
            getattr(fill, "time", None),
            getattr(execution, "time", None),
        )
        symbol = self._coalesce(
            getattr(fill, "symbol", None),
            getattr(contract, "localSymbol", None),
            getattr(contract, "symbol", None),
            getattr(execution, "symbol", None),
        )
        side = self._normalize_side(
            self._coalesce(
                getattr(fill, "side", None),
                getattr(execution, "side", None),
            )
        )
        qty = self._coalesce(
            getattr(fill, "qty", None),
            getattr(fill, "shares", None),
            getattr(execution, "shares", None),
            getattr(execution, "qty", None),
        )
        price = self._coalesce(
            getattr(fill, "price", None),
            getattr(fill, "avgPrice", None),
            getattr(execution, "price", None),
            getattr(execution, "avgPrice", None),
        )
        return str(fill_time), str(symbol), str(side), str(qty), str(price)

    # Refresh tables from normalized orders/fills payloads.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        open_orders = payload.get("open_orders") or []
        fills = payload.get("fills") or []

        self.orders_table.setRowCount(len(open_orders))
        for row, order in enumerate(open_orders):
            if isinstance(order, dict):
                order_id = self._coalesce(order.get("orderId"), order.get("id"))
                symbol = self._coalesce(order.get("symbol"), order.get("localSymbol"))
                side = self._normalize_side(self._coalesce(order.get("action"), order.get("side")))
                order_type = self._coalesce(order.get("orderType"), order.get("type"))
                qty = self._coalesce(order.get("totalQuantity"), order.get("qty"), order.get("quantity"))
                price = self._coalesce(order.get("lmtPrice"), order.get("price"), order.get("limit_price"))
                status = self._coalesce(order.get("status"), order.get("state"))
            else:
                order_id = self._coalesce(getattr(order, "orderId", None), getattr(order, "id", ""))
                symbol = self._coalesce(getattr(order, "symbol", None), getattr(order, "localSymbol", ""))
                side = self._normalize_side(self._coalesce(getattr(order, "action", None), getattr(order, "side", "")))
                order_type = self._coalesce(getattr(order, "orderType", None), getattr(order, "type", ""))
                qty = self._coalesce(getattr(order, "totalQuantity", None), getattr(order, "qty", ""))
                price = self._coalesce(getattr(order, "lmtPrice", None), getattr(order, "price", ""))
                status = self._coalesce(getattr(order, "status", None), "")

            self.orders_table.setItem(row, 0, QTableWidgetItem(str(order_id)))
            self.orders_table.setItem(row, 1, QTableWidgetItem(str(symbol)))
            self.orders_table.setItem(row, 2, QTableWidgetItem(str(side)))
            self.orders_table.setItem(row, 3, QTableWidgetItem(str(order_type)))
            self.orders_table.setItem(row, 4, QTableWidgetItem(str(qty)))
            self.orders_table.setItem(row, 5, QTableWidgetItem(str(price)))
            self.orders_table.setItem(row, 6, QTableWidgetItem(str(status)))

        self.fills_table.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            fill_time, symbol, side, qty, price = self._extract_fill_row(fill)

            self.fills_table.setItem(row, 0, QTableWidgetItem(str(fill_time)))
            self.fills_table.setItem(row, 1, QTableWidgetItem(str(symbol)))
            self.fills_table.setItem(row, 2, QTableWidgetItem(str(side)))
            self.fills_table.setItem(row, 3, QTableWidgetItem(str(qty)))
            self.fills_table.setItem(row, 4, QTableWidgetItem(str(price)))
