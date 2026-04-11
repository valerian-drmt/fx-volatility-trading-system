"""Settings Dialog — connection + vol scanner config."""
import json
from pathlib import Path
from typing import Any

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def _badge(level: str) -> QLabel:
    colors = {
        "CRITICAL": "background:#FCEBEB; color:#A32D2D;",
        "MEDIUM": "background:#FAEEDA; color:#854F0B;",
        "LOW": "background:#E1F5EE; color:#0F6E56;",
    }
    lbl = QLabel(f"  {level}  ")
    lbl.setStyleSheet(f"{colors.get(level, '')} font-weight:bold; padding:2px 4px; border-radius:3px;")
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedWidth(90)
    return lbl


class SettingsPanel(QDialog):
    DEFAULTS_VOL = {
        "step1": {
            "WAIT_GREEKS": 8, "LOOP_INTERVAL_S": 180,
            "n_side_short": 20, "n_side_long": 30,
            "rr25_max_short": 10.0, "rr25_max_long": 6.0,
            "bf25_min_short": -6.0, "bf25_min_long": -4.0,
            "IV_ARB_THRESHOLD": 0.005,
            "TARGET_DTES": [30, 60, 90, 120, 150, 180],
        },
        "step2": {
            "W1": 0.65, "W2": 0.35,
            "SIGNAL_THRESHOLD": 0.20, "ALPHA_BOOK": 0.20,
            "GARCH_DURATION": "1 Y",
            "RP_FLOOR": 0.20, "VRP_SHIFT": 0.50,
            "W1_RATIO_THRESHOLD": 1.15, "W1_RATIO_SENSITIVITY": 0.10,
            "W1_FLOOR": 0.40, "GARCH_EMPIRICAL_BLEND": 0.50, "EMPIRICAL_KAPPA": 2.0,
            "RISK_PREMIUM": {"1M": 1.20, "2M": 1.35, "3M": 1.50,
                             "4M": 1.55, "5M": 1.58, "6M": 1.60},
            "VEGA_LIMITS": {"1M": 150000, "2M": 200000, "3M": 300000,
                            "4M": 350000, "5M": 375000, "6M": 400000},
        },
    }

    def __init__(self, vol_config_path: Path, status_config_path: Path,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vol_path = vol_config_path
        self._status_path = status_config_path
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        self.setMinimumHeight(650)
        self._w: dict[str, Any] = {}
        self._build_ui()
        self._load()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)

        # ── Connection ──
        conn_group = QGroupBox("Connection")
        conn_form = QFormLayout(conn_group)
        conn_form.setHorizontalSpacing(12)
        conn_form.setVerticalSpacing(6)

        self._w["host"] = QLineEdit("127.0.0.1")
        self._w["port"] = QSpinBox()
        self._w["port"].setRange(1, 65535)
        self._w["client_id"] = QSpinBox()
        self._w["client_id"].setRange(0, 999999)
        self._w["client_id"].setEnabled(False)
        conn_form.addRow("Host:", self._w["host"])
        conn_form.addRow("Port:", self._w["port"])
        conn_form.addRow("Client ID:", self._w["client_id"])
        layout.addWidget(conn_group)

        # ── Model (expanded) ──
        model_group = QGroupBox("Model")
        model_form = QFormLayout(model_group)
        model_form.setHorizontalSpacing(12)
        model_form.setVerticalSpacing(6)

        self._add_slider(model_form, "W1", 0.0, 1.0, 0.05, "CRITICAL",
                         on_change=self._on_w1_changed)
        self._w["W2_label"] = QLabel("0.35")
        model_form.addRow("W2 (auto):", self._w["W2_label"])

        self._add_double_spin(model_form, "SIGNAL_THRESHOLD", 0.05, 1.0, 0.05, "CRITICAL")
        self._add_double_spin(model_form, "ALPHA_BOOK", 0.0, 1.0, 0.05, "MEDIUM")

        for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
            self._add_double_spin(model_form, f"RP_{tenor}", 0.0, 5.0, 0.05, "CRITICAL",
                                  label=f"Risk Premium {tenor}")
        layout.addWidget(model_group)

        # ── Scan (collapsed) ──
        scan_group = QGroupBox("Scan")
        scan_form = QFormLayout(scan_group)
        scan_form.setHorizontalSpacing(12)
        scan_form.setVerticalSpacing(6)

        self._add_spin(scan_form, "WAIT_GREEKS", 3, 15, "MEDIUM")
        self._add_spin(scan_form, "LOOP_INTERVAL_S", 30, 600, "MEDIUM", step=30)
        self._add_spin(scan_form, "n_side_short", 8, 30, "MEDIUM")
        self._add_spin(scan_form, "n_side_long", 12, 40, "MEDIUM")

        combo = QComboBox()
        combo.addItems(["6 M", "1 Y", "2 Y"])
        self._w["GARCH_DURATION"] = combo
        scan_form.addRow("GARCH Duration:", combo)

        self._w["TARGET_DTES"] = QLineEdit()
        scan_form.addRow("Target DTEs:", self._w["TARGET_DTES"])
        layout.addWidget(scan_group)

        # ── Filters (collapsed) ──
        filter_group = QGroupBox("Filters")
        filter_form = QFormLayout(filter_group)
        filter_form.setHorizontalSpacing(12)
        filter_form.setVerticalSpacing(6)

        self._add_double_spin(filter_form, "rr25_max_short", 5.0, 20.0, 0.5, "LOW")
        self._add_double_spin(filter_form, "rr25_max_long", 3.0, 15.0, 0.5, "LOW")
        self._add_double_spin(filter_form, "bf25_min_short", -10.0, 0.0, 0.5, "LOW")
        self._add_double_spin(filter_form, "bf25_min_long", -10.0, 0.0, 0.5, "LOW")
        self._add_double_spin(filter_form, "IV_ARB_THRESHOLD", 0.001, 0.02, 0.001, "LOW")

        for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
            spin = QSpinBox()
            spin.setRange(50_000, 1_000_000)
            spin.setSingleStep(50_000)
            self._w[f"VEGA_{tenor}"] = spin
            filter_form.addRow(f"Vega Limit {tenor}:", spin)
        layout.addWidget(filter_group)

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        btn_row.addStretch(1)
        reset_btn = QPushButton("Reset Defaults")
        reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(reset_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet("background:#2ecc71; color:white; font-weight:bold; padding:6px 16px;")
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)
        outer.addLayout(btn_row)

    # ── Widget helpers ──

    def _add_slider(self, form: QFormLayout, key: str, lo: float, hi: float,
                    step: float, level: str, on_change: Any = None, label: str = "") -> None:
        slider = QSlider(Qt.Horizontal)
        n_steps = int((hi - lo) / step)
        slider.setRange(0, n_steps)
        val_label = QLabel()
        self._w[key] = slider
        self._w[f"{key}_val"] = val_label

        def _update(pos: int) -> None:
            v = lo + pos * step
            val_label.setText(f"{v:.2f}")
            if on_change:
                on_change(v)

        slider.valueChanged.connect(_update)
        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(val_label)
        row.addWidget(_badge(level))
        form.addRow(f"{label or key}:", row)

    def _add_double_spin(self, form: QFormLayout, key: str, lo: float, hi: float,
                         step: float, level: str, label: str = "") -> None:
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(3)
        self._w[key] = spin
        row = QHBoxLayout()
        row.addWidget(spin)
        row.addWidget(_badge(level))
        form.addRow(f"{label or key}:", row)

    def _add_spin(self, form: QFormLayout, key: str, lo: int, hi: int,
                  level: str, step: int = 1) -> None:
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        self._w[key] = spin
        row = QHBoxLayout()
        row.addWidget(spin)
        row.addWidget(_badge(level))
        form.addRow(f"{key}:", row)

    def _on_w1_changed(self, w1: float) -> None:
        self._w["W2_label"].setText(f"{1.0 - w1:.2f}")

    # ── Load / Save ──

    def _load(self) -> None:
        # Connection
        try:
            status = json.loads(self._status_path.read_text(encoding="utf-8"))
            s = status.get("status", {})
        except (FileNotFoundError, json.JSONDecodeError):
            s = {}
        self._w["host"].setText(s.get("host", "127.0.0.1"))
        self._w["port"].setValue(int(s.get("port", 4002)))
        self._w["client_id"].setValue(int(s.get("client_id", 1)))

        # Vol config
        try:
            data = json.loads(self._vol_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = self.DEFAULTS_VOL
        self._populate_vol(data)

    def _populate_vol(self, data: dict) -> None:
        s1 = data.get("step1", {})
        s2 = data.get("step2", {})
        d1 = self.DEFAULTS_VOL["step1"]
        d2 = self.DEFAULTS_VOL["step2"]

        w1 = s2.get("W1", d2["W1"])
        self._w["W1"].setValue(int(w1 / 0.05))
        self._w["SIGNAL_THRESHOLD"].setValue(s2.get("SIGNAL_THRESHOLD", d2["SIGNAL_THRESHOLD"]))
        self._w["ALPHA_BOOK"].setValue(s2.get("ALPHA_BOOK", d2["ALPHA_BOOK"]))
        rp = s2.get("RISK_PREMIUM", d2["RISK_PREMIUM"])
        for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
            self._w[f"RP_{tenor}"].setValue(rp.get(tenor, 1.5))

        self._w["WAIT_GREEKS"].setValue(s1.get("WAIT_GREEKS", d1["WAIT_GREEKS"]))
        self._w["LOOP_INTERVAL_S"].setValue(s1.get("LOOP_INTERVAL_S", d1["LOOP_INTERVAL_S"]))
        self._w["n_side_short"].setValue(s1.get("n_side_short", d1["n_side_short"]))
        self._w["n_side_long"].setValue(s1.get("n_side_long", d1["n_side_long"]))
        self._w["GARCH_DURATION"].setCurrentText(s2.get("GARCH_DURATION", d2["GARCH_DURATION"]))
        dtes = s1.get("TARGET_DTES", d1["TARGET_DTES"])
        self._w["TARGET_DTES"].setText(",".join(str(d) for d in dtes))

        self._w["rr25_max_short"].setValue(s1.get("rr25_max_short", d1["rr25_max_short"]))
        self._w["rr25_max_long"].setValue(s1.get("rr25_max_long", d1["rr25_max_long"]))
        self._w["bf25_min_short"].setValue(s1.get("bf25_min_short", d1["bf25_min_short"]))
        self._w["bf25_min_long"].setValue(s1.get("bf25_min_long", d1["bf25_min_long"]))
        self._w["IV_ARB_THRESHOLD"].setValue(s1.get("IV_ARB_THRESHOLD", d1["IV_ARB_THRESHOLD"]))
        vl = s2.get("VEGA_LIMITS", d2["VEGA_LIMITS"])
        for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
            self._w[f"VEGA_{tenor}"].setValue(int(vl.get(tenor, 300000)))

    def _save(self) -> None:
        # Save connection to status config
        try:
            status = json.loads(self._status_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            status = {}
        if "status" not in status:
            status["status"] = {}
        status["status"]["host"] = self._w["host"].text().strip()
        status["status"]["port"] = self._w["port"].value()
        status["status"]["client_id"] = self._w["client_id"].value()
        self._status_path.parent.mkdir(parents=True, exist_ok=True)
        self._status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

        # Save vol config
        w1 = round(self._w["W1"].value() * 0.05, 2)
        try:
            target_dtes = [int(d.strip()) for d in self._w["TARGET_DTES"].text().split(",") if d.strip()]
        except ValueError:
            target_dtes = self.DEFAULTS_VOL["step1"]["TARGET_DTES"]

        vol_config = {
            "step1": {
                "WAIT_GREEKS": self._w["WAIT_GREEKS"].value(),
                "LOOP_INTERVAL_S": self._w["LOOP_INTERVAL_S"].value(),
                "n_side_short": self._w["n_side_short"].value(),
                "n_side_long": self._w["n_side_long"].value(),
                "rr25_max_short": self._w["rr25_max_short"].value(),
                "rr25_max_long": self._w["rr25_max_long"].value(),
                "bf25_min_short": self._w["bf25_min_short"].value(),
                "bf25_min_long": self._w["bf25_min_long"].value(),
                "IV_ARB_THRESHOLD": self._w["IV_ARB_THRESHOLD"].value(),
                "TARGET_DTES": target_dtes,
            },
            "step2": {
                "W1": w1, "W2": round(1.0 - w1, 2),
                "SIGNAL_THRESHOLD": self._w["SIGNAL_THRESHOLD"].value(),
                "ALPHA_BOOK": self._w["ALPHA_BOOK"].value(),
                "GARCH_DURATION": self._w["GARCH_DURATION"].currentText(),
                "RP_FLOOR": 0.20, "VRP_SHIFT": 0.50,
                "W1_RATIO_THRESHOLD": 1.15, "W1_RATIO_SENSITIVITY": 0.10,
                "W1_FLOOR": 0.40, "GARCH_EMPIRICAL_BLEND": 0.50, "EMPIRICAL_KAPPA": 2.0,
                "RISK_PREMIUM": {t: self._w[f"RP_{t}"].value()
                                 for t in ["1M", "2M", "3M", "4M", "5M", "6M"]},
                "VEGA_LIMITS": {t: self._w[f"VEGA_{t}"].value()
                                for t in ["1M", "2M", "3M", "4M", "5M", "6M"]},
            },
        }
        self._vol_path.parent.mkdir(parents=True, exist_ok=True)
        self._vol_path.write_text(json.dumps(vol_config, indent=2), encoding="utf-8")
        self.accept()

    def _reset_defaults(self) -> None:
        self._populate_vol(self.DEFAULTS_VOL)
