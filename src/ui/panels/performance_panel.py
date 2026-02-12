from PyQt5.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QLabel
import pyqtgraph as pg


class PerformancePanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.plot = pg.PlotWidget()
        self.plot.setTitle("Performance")
        self.plot.setLabel("bottom", "Time")
        self.plot.setLabel("left", "Equity (normalized)")
        self.plot.addLegend()

        stats_widget = QWidget()
        stats_layout = QFormLayout(stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setHorizontalSpacing(10)
        stats_layout.setVerticalSpacing(4)

        self.total_pnl_label = QLabel("--")
        self.max_dd_label = QLabel("--")
        self.trades_label = QLabel("--")
        self.win_rate_label = QLabel("--")

        stats_layout.addRow("Total PnL %:", self.total_pnl_label)
        stats_layout.addRow("Max DD %:", self.max_dd_label)
        stats_layout.addRow("Trades:", self.trades_label)
        stats_layout.addRow("Win rate:", self.win_rate_label)

        layout.addWidget(self.plot)
        layout.addWidget(stats_widget)
        layout.addStretch(1)
