from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QCheckBox,
    QGroupBox,
)


class RiskPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        limits_group = QGroupBox("Limits")
        limits_layout = QFormLayout(limits_group)
        limits_layout.setContentsMargins(6, 6, 6, 6)
        limits_layout.setHorizontalSpacing(10)
        limits_layout.setVerticalSpacing(4)

        self.max_dd_label = QLabel("--")
        self.max_pos_label = QLabel("--")
        self.max_loss_label = QLabel("--")

        limits_layout.addRow("Max DD:", self.max_dd_label)
        limits_layout.addRow("Max position:", self.max_pos_label)
        limits_layout.addRow("Max loss/day:", self.max_loss_label)

        usage_group = QGroupBox("Usage")
        usage_layout = QFormLayout(usage_group)
        usage_layout.setContentsMargins(6, 6, 6, 6)
        usage_layout.setHorizontalSpacing(10)
        usage_layout.setVerticalSpacing(4)

        self.risk_used_label = QLabel("--")
        self.margin_used_label = QLabel("--")

        usage_layout.addRow("Risk used:", self.risk_used_label)
        usage_layout.addRow("Margin used:", self.margin_used_label)

        controls_group = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(6, 6, 6, 6)
        controls_layout.setSpacing(6)

        self.kill_switch = QCheckBox("Kill switch")
        self.kill_reason = QLineEdit()
        self.kill_reason.setPlaceholderText("Reason...")
        self.risk_slider = QSlider(Qt.Horizontal)
        self.risk_slider.setMinimum(0)
        self.risk_slider.setMaximum(100)
        self.risk_slider.setValue(50)

        controls_layout.addWidget(self.kill_switch)
        controls_layout.addWidget(self.kill_reason)
        controls_layout.addWidget(QLabel("Risk budget %"))
        controls_layout.addWidget(self.risk_slider)

        layout.addWidget(limits_group)
        layout.addWidget(usage_group)
        layout.addWidget(controls_group)
        layout.addStretch(1)
