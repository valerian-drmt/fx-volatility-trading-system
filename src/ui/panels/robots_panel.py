from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
)


class RobotsPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self.start_all_button = QPushButton("Start all")
        self.stop_all_button = QPushButton("Stop all")
        self.refresh_button = QPushButton("Refresh")

        controls.addWidget(self.start_all_button)
        controls.addWidget(self.stop_all_button)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Instrument", "State", "Last action", "PnL"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)

        layout.addLayout(controls)
        layout.addWidget(self.table)
