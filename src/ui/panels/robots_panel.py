from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
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

    def update(self, payload=None):
        if not isinstance(payload, dict):
            return
        robots = payload.get("robots") or []

        self.table.setRowCount(len(robots))
        for row, robot in enumerate(robots):
            name = robot.get("name", "") if isinstance(robot, dict) else getattr(robot, "name", "")
            instrument = robot.get("instrument", "") if isinstance(robot, dict) else getattr(robot, "instrument", "")
            state = robot.get("state", "") if isinstance(robot, dict) else getattr(robot, "state", "")
            last_action = robot.get("last_action", "") if isinstance(robot, dict) else getattr(robot, "last_action", "")
            pnl = robot.get("pnl", "") if isinstance(robot, dict) else getattr(robot, "pnl", "")

            self.table.setItem(row, 0, QTableWidgetItem(str(name)))
            self.table.setItem(row, 1, QTableWidgetItem(str(instrument)))
            self.table.setItem(row, 2, QTableWidgetItem(str(state)))
            self.table.setItem(row, 3, QTableWidgetItem(str(last_action)))
            self.table.setItem(row, 4, QTableWidgetItem(str(pnl)))
