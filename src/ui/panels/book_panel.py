from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _fmt_k(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value / 1000:+,.1f}k"


class ClosePositionDialog(QDialog):
    """Confirmation dialog before closing a position."""

    def __init__(self, pos: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Close Position")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel("Close Position")
        title.setStyleSheet("font-size: 12px; font-weight: bold;")
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(4)

        side = pos.get("side", "")
        close_side = "SELL" if side == "BUY" else "BUY"
        sec_type = pos.get("sec_type", "")

        fields = [
            ("Symbol:", pos.get("symbol", "")),
            ("Action:", close_side),
            ("Quantity:", str(pos.get("qty", ""))),
            ("Type:", "MKT"),
        ]
        if sec_type == "FOP":
            fields.insert(2, ("Strike:", pos.get("strike", "")))
            fields.insert(3, ("Right:", pos.get("right", "")))
            fields.insert(4, ("Tenor:", pos.get("tenor", "")))

        pnl = pos.get("pnl")
        if pnl is not None:
            fields.append(("Unrealized PnL:", f"{pnl:+,.2f} USD"))

        for label, value in fields:
            val_label = QLabel(str(value))
            val_label.setStyleSheet("font-weight: bold;")
            if label == "Action:":
                color = "#2ecc71" if close_side == "BUY" else "#e74c3c"
                val_label.setStyleSheet(f"font-weight: bold; color: {color};")
            if label == "Unrealized PnL:" and pnl is not None:
                color = "#2ecc71" if pnl >= 0 else "#e74c3c"
                val_label.setStyleSheet(f"font-weight: bold; color: {color};")
            form.addRow(label, val_label)

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton("Close Position")
        confirm_btn.setStyleSheet(
            "background-color: #e74c3c; color: white; font-weight: bold; padding: 6px 16px;"
        )
        confirm_btn.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(confirm_btn)
        layout.addLayout(buttons)


# (#4) Currency annotations on all metrics
class BookPanel(QWidget):
    """Greeks Summary with currency-annotated metrics."""

    _ROWS = [
        ("Delta Net (USD)", "delta_net", "k"),
        ("Vega Net (USD/1%vol)", "vega_net", "k"),
        ("Gamma Net (USD/pip)", "gamma_net", "num"),
        ("Theta Net (USD/day)", "theta_net", "num"),
        ("PnL Total (USD)", "pnl_total", "num"),
        ("Net Premium (USD)", "net_premium_paid", "k"),
        ("Vega/Theta (days)", "vega_theta_ratio", "days"),
        ("PnL (% Premium)", "pnl_pct_premium", "pct"),
    ]

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Greeks Summary")
        form = QFormLayout(group)
        form.setContentsMargins(8, 8, 8, 8)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)

        self._labels: dict[str, QLabel] = {}
        for label_text, key, _fmt in self._ROWS:
            lbl = QLabel("--")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if key == "pnl_total":
                bold_font = QFont()
                bold_font.setBold(True)
                bold_font.setPointSize(bold_font.pointSize() + 1)
                lbl.setFont(bold_font)
            self._labels[key] = lbl
            form.addRow(f"{label_text}:", lbl)

        layout.addWidget(group)

    @staticmethod
    def _pnl_color(value: float | None) -> str:
        if value is None or value == 0:
            return "#aaaaaa"
        return "#2ecc71" if value > 0 else "#e74c3c"

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return
        for _label_text, key, fmt in self._ROWS:
            raw = summary.get(key)
            if fmt == "k":
                text = _fmt_k(raw)
            elif fmt == "days":
                text = f"{raw:,.0f}d" if raw is not None else "--"
            elif fmt == "pct":
                text = f"{raw:+.1f}%" if raw is not None else "--"
            elif raw is not None:
                text = f"{raw:+,.2f}"
            else:
                text = "--"
            lbl = self._labels[key]
            lbl.setText(text)
            color = self._pnl_color(raw)
            lbl.setStyleSheet(f"color: {color};")



# (#4, #5, #6, #7) Updated columns with currencies, mark price, DTE, break-even
OPEN_COLUMNS = [
    "Symbol", "Side", "Qty", "Tenor", "DTE", "Strike", "Right",
    "Fill Price", "Mark Price", "IV %",
    "Delta (USD)", "Vega (USD)", "Gamma (USD)", "Theta (USD)",
    "Break-Even", "PnL (USD)", "",
]



class OpenPositionsPanel(QWidget):
    """Open positions table — placed independently in main_window."""

    close_position_requested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        group = QGroupBox("Open Positions")
        inner = QVBoxLayout(group)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        self.table = QTableWidget(0, len(OPEN_COLUMNS))
        self.table.setHorizontalHeaderLabels(OPEN_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        for col in range(len(OPEN_COLUMNS) - 1):
            header.setSectionResizeMode(col, QHeaderView.Stretch)
        header.setSectionResizeMode(len(OPEN_COLUMNS) - 1, QHeaderView.Fixed)
        self.table.setColumnWidth(len(OPEN_COLUMNS) - 1, 36)
        self.table.verticalHeader().setVisible(False)
        inner.addWidget(self.table)
        layout.addWidget(group)

        self._rows_data: list[dict] = []
        self._close_dialog: ClosePositionDialog | None = None

    @staticmethod
    def _fmt(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:+,.2f}"

    @staticmethod
    def _fmt_iv(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.2f}"

    @staticmethod
    def _fmt_price(value: float | None) -> str:
        if value is None:
            return "--"
        return f"{value:.6f}"

    @staticmethod
    def _pnl_color(value: float | None) -> str:
        if value is None or value == 0:
            return "#aaaaaa"
        return "#2ecc71" if value > 0 else "#e74c3c"

    def _on_close_clicked(self, row: int) -> None:
        if row < 0 or row >= len(self._rows_data):
            return
        pos = self._rows_data[row]

        dialog = ClosePositionDialog(pos, parent=self)
        self._close_dialog = dialog
        dialog.accepted.connect(lambda: self.close_position_requested.emit(pos))
        dialog.finished.connect(lambda _: setattr(self, "_close_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        positions = payload.get("open_positions", [])
        self._rows_data = positions
        self.table.setRowCount(len(positions))
        close_col = len(OPEN_COLUMNS) - 1
        for row, pos in enumerate(positions):
            using_fallback = pos.get("using_fallback_iv", False)
            iv_text = self._fmt_iv(pos.get("iv_now_pct"))
            if using_fallback and pos.get("iv_now_pct") is not None:
                iv_text = f"! {iv_text}"

            fields = [
                (str(pos.get("symbol", "")), None),
                (str(pos.get("side", "")), None),
                (str(pos.get("qty", "")), None),
                (str(pos.get("tenor", "")), None),
                (str(pos.get("dte", "") if pos.get("dte") is not None else "--"), None),
                (str(pos.get("strike", "")), None),
                (str(pos.get("right", "")), None),
                (self._fmt_price(pos.get("fill_price")), None),
                (self._fmt_price(pos.get("mark_price")), None),
                (iv_text, None),
                (_fmt_k(pos.get("delta")), pos.get("delta")),
                (_fmt_k(pos.get("vega")), pos.get("vega")),
                (self._fmt(pos.get("gamma")), pos.get("gamma")),
                (self._fmt(pos.get("theta")), pos.get("theta")),
                (self._fmt_price(pos.get("break_even")), None),
                (self._fmt(pos.get("pnl")), pos.get("pnl")),
            ]
            for col, (text, raw) in enumerate(fields):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if raw is not None:
                    item.setForeground(QColor(self._pnl_color(raw)))
                self.table.setItem(row, col, item)

            btn = QPushButton("X")
            btn.setFixedSize(28, 22)
            btn.setStyleSheet(
                "QPushButton { color: #e74c3c; font-weight: bold; border: 1px solid #ccc; border-radius: 3px; }"
                "QPushButton:hover { background-color: #e74c3c; color: white; }"
            )
            btn.clicked.connect(lambda checked, r=row: self._on_close_clicked(r))
            container = QWidget()
            btn_layout = QHBoxLayout(container)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.setAlignment(Qt.AlignCenter)
            btn_layout.addWidget(btn)
            self.table.setCellWidget(row, close_col, container)
