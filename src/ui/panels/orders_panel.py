from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
    QPushButton,
)
from typing import Any


COLUMNS = ["Id", "Symbol", "Side", "Type", "Qty", "Price", "Status", "Time"]
_COL_ID = 0
_COL_SYMBOL = 1
_COL_SIDE = 2
_COL_TYPE = 3
_COL_QTY = 4
_COL_PRICE = 5
_COL_STATUS = 6
_COL_TIME = 7
_COL_CANCEL = 8  # only for open orders table


class OrdersPanel(QWidget):
    cancel_order_requested = pyqtSignal(object)

    # Build open-orders and fills tables with shared columns.
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Open orders"))
        self.orders_table = QTableWidget(0, len(COLUMNS) + 1)
        self.orders_table.setHorizontalHeaderLabels([*COLUMNS, ""])
        self._configure_table(self.orders_table)
        layout.addWidget(self.orders_table)

        layout.addWidget(QLabel("Recent fills"))
        self.fills_table = QTableWidget(0, len(COLUMNS))
        self.fills_table.setHorizontalHeaderLabels(COLUMNS)
        self._configure_table(self.fills_table)
        layout.addWidget(self.fills_table)

        self._open_orders_raw: list[Any] = []

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.verticalHeader().setVisible(False)

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

    # Extract shared row fields from an order object.
    def _extract_order_row(self, order: Any) -> tuple[str, str, str, str, str, str, str, str]:
        if isinstance(order, dict):
            order_id = self._coalesce(order.get("orderId"), order.get("id"))
            symbol = self._coalesce(order.get("symbol"), order.get("localSymbol"))
            side = self._normalize_side(self._coalesce(order.get("action"), order.get("side")))
            order_type = self._coalesce(order.get("orderType"), order.get("type"))
            qty = self._coalesce(order.get("totalQuantity"), order.get("qty"), order.get("quantity"))
            price = self._coalesce(order.get("lmtPrice"), order.get("price"), order.get("limit_price"))
            status = self._coalesce(order.get("status"), order.get("state"))
            t = self._coalesce(order.get("time"), order.get("timestamp"))
        else:
            contract = getattr(order, "contract", None)
            execution = getattr(order, "execution", None)
            order_obj = getattr(order, "order", None)
            order_status = getattr(order, "orderStatus", None)
            order_id = self._coalesce(
                getattr(order_obj, "orderId", None),
                getattr(execution, "orderId", None),
                getattr(order, "orderId", None),
            )
            symbol = self._coalesce(
                getattr(contract, "localSymbol", None),
                getattr(contract, "symbol", None),
                getattr(order, "symbol", None),
            )
            side = self._normalize_side(self._coalesce(
                getattr(order_obj, "action", None),
                getattr(order, "action", None),
                getattr(execution, "side", None),
                getattr(order, "side", None),
            ))
            order_type = self._coalesce(
                getattr(order_obj, "orderType", None),
                getattr(order, "orderType", None),
                getattr(order, "type", None),
            )
            qty = self._coalesce(
                getattr(order_obj, "totalQuantity", None),
                getattr(order, "totalQuantity", None),
                getattr(execution, "shares", None),
                getattr(order, "qty", None),
            )
            price = self._coalesce(
                getattr(order_obj, "lmtPrice", None),
                getattr(order, "lmtPrice", None),
                getattr(execution, "price", None),
                getattr(execution, "avgPrice", None),
                getattr(order, "price", None),
            )
            status = self._coalesce(
                getattr(order_status, "status", None),
                getattr(order, "status", None),
            )
            t = self._coalesce(
                getattr(execution, "time", None),
                getattr(order, "time", None),
            )
        return str(order_id), str(symbol), str(side), str(order_type), str(qty), str(price), str(status), str(t)

    def _set_row(self, table: QTableWidget, row: int, fields: tuple[str, ...]) -> None:
        for col, value in enumerate(fields):
            table.setItem(row, col, QTableWidgetItem(value))

    def _on_cancel_clicked(self, row: int) -> None:
        if 0 <= row < len(self._open_orders_raw):
            self.cancel_order_requested.emit(self._open_orders_raw[row])

    # Refresh tables from normalized orders/fills payloads.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        open_orders = payload.get("open_orders") or []
        fills = payload.get("fills") or []

        self._open_orders_raw = list(open_orders)
        self.orders_table.setRowCount(len(open_orders))
        for row, order in enumerate(open_orders):
            fields = self._extract_order_row(order)
            self._set_row(self.orders_table, row, fields)
            cancel_btn = QPushButton("Cancel")
            cancel_btn.setFixedHeight(28)
            cancel_btn.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold; padding: 2px 8px;")
            self.orders_table.setRowHeight(row, 32)
            r = row  # capture for lambda
            cancel_btn.clicked.connect(lambda _checked, r=r: self._on_cancel_clicked(r))
            self.orders_table.setCellWidget(row, _COL_CANCEL, cancel_btn)

        self.fills_table.setRowCount(len(fills))
        for row, fill in enumerate(fills):
            fields = self._extract_order_row(fill)
            self._set_row(self.fills_table, row, fields)
