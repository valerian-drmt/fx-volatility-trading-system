from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
)


class OrdersPanel(QWidget):
    def __init__(self):
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
