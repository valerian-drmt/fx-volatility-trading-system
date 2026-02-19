from __future__ import annotations

from functools import partial
import json
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QLineEdit,
    QFormLayout,
    QLabel,
    QTableWidget,
    QAbstractItemView,
    QHeaderView,
    QTableWidgetItem,
)


def _normalize_robot_payload(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "name": str(raw.get("name", "")).strip(),
        "instrument": str(raw.get("instrument", "")).strip().upper(),
        "state": str(raw.get("state", "stopped")).strip() or "stopped",
        "last_action": str(raw.get("last_action", "--")).strip() or "--",
        "pnl": str(raw.get("pnl", "--")).strip() or "--",
    }


def _read_robots_json(settings_path: Path) -> list[dict]:
    if not settings_path.exists():
        return []
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    if isinstance(payload, dict):
        robots_raw = payload.get("robots", [])
    elif isinstance(payload, list):
        robots_raw = payload
    else:
        robots_raw = []
    if not isinstance(robots_raw, list):
        return []
    return [_normalize_robot_payload(item) for item in robots_raw]


def _write_robots_json(settings_path: Path, robots: list[dict]):
    payload = {"robots": [_normalize_robot_payload(robot) for robot in robots]}
    settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class RobotEditorTab(QWidget):
    def __init__(self, robot=None):
        super().__init__()
        self._robot_data = _normalize_robot_payload(robot or {})

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)

        self.name_input = QLineEdit()

        form.addRow("Name:", self.name_input)

        layout.addLayout(form)
        layout.addStretch(1)

        self.set_robot(robot or {})

    def set_robot(self, robot: dict):
        self._robot_data = _normalize_robot_payload(robot)
        self.name_input.setText(self._robot_data["name"])

    def get_robot(self) -> dict:
        updated = dict(self._robot_data)
        updated["name"] = self.name_input.text().strip()
        return _normalize_robot_payload(updated)


class ManageRobotsWindow(QWidget):
    robots_changed = pyqtSignal(list)

    def __init__(self, settings_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle("Manage Robots")
        self.resize(760, 460)

        self._settings_path = settings_path
        self._mutating_tabs = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_robot_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        buttons_layout = QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(8)

        self.add_robot_button = QPushButton("New Robot")
        self.add_robot_button.clicked.connect(self._add_new_robot_tab)
        self.save_robot_button = QPushButton("Save Robot")
        self.save_robot_button.clicked.connect(self._save_robots)
        self.delete_robot_button = QPushButton("Delete Robot")
        self.delete_robot_button.clicked.connect(self._delete_current_robot_tab)

        buttons_layout.addWidget(self.add_robot_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.save_robot_button)
        buttons_layout.addWidget(self.delete_robot_button)
        layout.addLayout(buttons_layout)

        self.reload_from_disk()

    def _robot_tab_indexes(self) -> list[int]:
        return list(range(self.tabs.count()))

    def _rebuild_tabs(self, robots: list[dict]):
        self._mutating_tabs = True
        while self.tabs.count() > 0:
            tab = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if tab is not None:
                tab.deleteLater()

        for robot in robots:
            self._insert_robot_tab(_normalize_robot_payload(robot))

        if self.tabs.count() > 0:
            self.tabs.setCurrentIndex(0)
        self._mutating_tabs = False
        self._update_action_buttons()

    def _insert_robot_tab(self, robot: dict):
        editor = RobotEditorTab(robot)
        insert_index = self.tabs.count()
        fallback = insert_index + 1
        tab_name = robot["name"] or f"Robot {fallback}"
        self.tabs.addTab(editor, tab_name)
        return insert_index

    def _read_robots_from_tabs(self) -> list[dict]:
        robots = []
        for index in self._robot_tab_indexes():
            widget = self.tabs.widget(index)
            if isinstance(widget, RobotEditorTab):
                robots.append(widget.get_robot())
        return robots

    def _refresh_tab_titles(self):
        ordinal = 1
        for index in self._robot_tab_indexes():
            widget = self.tabs.widget(index)
            if isinstance(widget, RobotEditorTab):
                robot = widget.get_robot()
                title = robot["name"] or f"Robot {ordinal}"
                self.tabs.setTabText(index, title)
            ordinal += 1

    def _add_new_robot_tab(self):
        new_index = self._insert_robot_tab(
            _normalize_robot_payload(
                {
                    "name": "",
                    "instrument": "",
                    "state": "stopped",
                    "last_action": "--",
                    "pnl": "--",
                }
            )
        )
        self.tabs.setCurrentIndex(new_index)
        self._update_action_buttons()

    def _on_tab_changed(self, index: int):
        if self._mutating_tabs:
            return
        self._update_action_buttons()

    def _close_robot_tab(self, index: int):
        if index < 0:
            return

        self._mutating_tabs = True
        tab_widget = self.tabs.widget(index)
        self.tabs.removeTab(index)
        if tab_widget is not None:
            tab_widget.deleteLater()

        if self.tabs.count() > 0:
            next_index = min(index, self.tabs.count() - 1)
            self.tabs.setCurrentIndex(max(0, next_index))
        self._mutating_tabs = False

        self._save_robots()

    def _delete_current_robot_tab(self):
        self._close_robot_tab(self.tabs.currentIndex())

    def _update_action_buttons(self):
        current_index = self.tabs.currentIndex()
        can_edit = current_index >= 0
        self.save_robot_button.setEnabled(can_edit)
        self.delete_robot_button.setEnabled(can_edit)

    def _save_robots(self):
        robots = self._read_robots_from_tabs()
        _write_robots_json(self._settings_path, robots)
        self._refresh_tab_titles()
        self.robots_changed.emit(robots)
        self._update_action_buttons()

    def reload_from_disk(self):
        robots = _read_robots_json(self._settings_path)
        self._rebuild_tabs(robots)
        self.robots_changed.emit(robots)


class RobotsPanel(QWidget):
    def __init__(self):
        super().__init__()
        self._robots_settings_path = Path(__file__).resolve().parents[3] / "robots_settings.json"
        self._robots = _read_robots_json(self._robots_settings_path)
        self._ensure_robots_settings_file()
        self._manage_robots_window: ManageRobotsWindow | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self.manage_robots_button = QPushButton("Manage Robots")
        self.manage_robots_button.clicked.connect(self._open_manage_robots_window)

        controls.addWidget(self.manage_robots_button)
        controls.addStretch(1)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Name", "Instrument", "State", "Last action", "PnL", "Action"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)

        layout.addLayout(controls)
        layout.addWidget(self.table)
        self._set_robots_table(self._robots)

    def _ensure_robots_settings_file(self):
        if self._robots_settings_path.exists():
            return
        _write_robots_json(self._robots_settings_path, self._robots)

    def _set_robots_table(self, robots: list[dict]):
        self.table.setRowCount(len(robots))
        for row, robot in enumerate(robots):
            normalized = _normalize_robot_payload(robot)
            self.table.setItem(row, 0, QTableWidgetItem(normalized["name"]))
            self.table.setItem(row, 1, QTableWidgetItem(normalized["instrument"]))
            self.table.setCellWidget(row, 2, self._create_state_widget(normalized["state"]))
            self.table.setItem(row, 3, QTableWidgetItem(normalized["last_action"]))
            self.table.setItem(row, 4, QTableWidgetItem(normalized["pnl"]))
            button_text = "Stop" if normalized["state"].lower() == "running" else "Start"
            action_button = QPushButton(button_text)
            action_button.clicked.connect(partial(self._toggle_robot_state, row))
            self.table.setCellWidget(row, 5, action_button)

    @staticmethod
    def _state_color(state: str) -> str:
        state_key = state.strip().lower()
        if state_key == "running":
            return "#2ecc71"
        if state_key == "paused":
            return "#f1c40f"
        if state_key == "stopped":
            return "#e74c3c"
        return "#95a5a6"

    def _create_state_widget(self, state: str) -> QWidget:
        state_text = state.strip().lower() or "stopped"
        color = self._state_color(state_text)

        container = QWidget()
        state_layout = QHBoxLayout(container)
        state_layout.setContentsMargins(6, 0, 6, 0)
        state_layout.setSpacing(8)

        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background-color: {color}; border-radius: 5px;")
        dot.setToolTip(state_text.capitalize())

        text_label = QLabel(state_text.capitalize())

        state_layout.addWidget(dot)
        state_layout.addWidget(text_label)
        state_layout.addStretch(1)
        return container

    def _on_robots_changed(self, robots: list):
        self._robots = [_normalize_robot_payload(item) for item in robots]
        self._set_robots_table(self._robots)

    def _reload_robots_from_disk(self):
        self._robots = _read_robots_json(self._robots_settings_path)
        self._set_robots_table(self._robots)

    def _toggle_robot_state(self, row_index: int):
        if row_index < 0 or row_index >= len(self._robots):
            return

        robot = _normalize_robot_payload(self._robots[row_index])
        running = robot["state"].lower() == "running"
        robot["state"] = "stopped" if running else "running"
        robot["last_action"] = "Stopped" if running else "Started"

        self._robots[row_index] = robot
        _write_robots_json(self._robots_settings_path, self._robots)
        self._set_robots_table(self._robots)

    def _open_manage_robots_window(self):
        if self._manage_robots_window is None:
            self._manage_robots_window = ManageRobotsWindow(self._robots_settings_path, None)
            self._manage_robots_window.robots_changed.connect(self._on_robots_changed)
        else:
            self._manage_robots_window.reload_from_disk()
        self._manage_robots_window.show()
        self._manage_robots_window.raise_()
        self._manage_robots_window.activateWindow()

    def update(self, payload=None):
        if isinstance(payload, dict) and "robots" in payload:
            robots = payload.get("robots") or []
            self._robots = [_normalize_robot_payload(item) for item in robots]
        self._set_robots_table(self._robots)
