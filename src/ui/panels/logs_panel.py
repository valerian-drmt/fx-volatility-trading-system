import math
import re
from typing import Any

from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QComboBox,
    QLineEdit,
)


class LogsPanel(QWidget):
    _LOG_PREFIX_PATTERN = re.compile(r"^\[(?P<level>[^\]]+)\]\[(?P<source>[^\]]+)\]")
    _MAX_ENTRIES = 4000
    _PRICE_DECIMALS = 8

    # Build log controls, filters, and state storage.
    def __init__(self) -> None:
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)

        self.level_combo = QComboBox()
        self.level_combo.addItems(["ALL", "INFO", "WARN", "ERROR"])
        self.source_combo = QComboBox()
        self.source_combo.addItems(["ALL", "system", "strategy", "execution", "market_tick"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter text...")

        controls.addWidget(QLabel("Level"))
        controls.addWidget(self.level_combo)
        controls.addWidget(QLabel("Source"))
        controls.addWidget(self.source_combo)
        controls.addWidget(self.search_edit, 1)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Logs will appear here...")

        layout.addLayout(controls)
        layout.addWidget(self.log_view)

        self._entries: list[dict] = []

        self.level_combo.currentTextChanged.connect(self._apply_filters)
        self.source_combo.currentTextChanged.connect(self._apply_filters)
        self.search_edit.textChanged.connect(self._apply_filters)

    @staticmethod
    # Format tick price-like values for compact display.
    def _format_tick_value(value: Any) -> str:
        if isinstance(value, (int, float)):
            try:
                if math.isnan(value):
                    return "--"
            except TypeError:
                return str(value)
            value_float = float(value)
            return f"{value_float:.{LogsPanel._PRICE_DECIMALS}f}".rstrip("0").rstrip(".")
        return "--" if value is None else str(value)

    @staticmethod
    # Format tick size values while preserving integer readability.
    def _format_tick_size(value: Any) -> str:
        if isinstance(value, (int, float)):
            try:
                if math.isnan(value):
                    return "--"
            except TypeError:
                return str(value)
            value_float = float(value)
            if value_float.is_integer():
                return str(int(value_float))
            return f"{value_float:.2f}"
        return "--" if value is None else str(value)

    # Build a single line log entry from a market-data tick.
    def _format_tick_log_message(self, tick: dict[str, Any]) -> str:
        tick_time = str(tick.get("time", "--"))
        bid = self._format_tick_value(tick.get("bid"))
        ask = self._format_tick_value(tick.get("ask"))
        bid_size = self._format_tick_size(tick.get("bid_size"))
        ask_size = self._format_tick_size(tick.get("ask_size"))
        last = self._format_tick_value(tick.get("last"))
        return (
            f"[INFO][market_tick] t={tick_time} "
            f"bid={bid} ask={ask} bid_size={bid_size} ask_size={ask_size} last={last}"
        )

    @staticmethod
    # Normalize free-form log levels to supported bucket names.
    def _normalize_level(value: str) -> str:
        level = str(value).strip().upper()
        if level.startswith("WARN"):
            return "WARN"
        if level.startswith("ERR"):
            return "ERROR"
        if level.startswith("INFO"):
            return "INFO"
        return level

    # Parse text into a structured log entry used by filters.
    def _parse_log_entry(self, text: str) -> dict[str, str]:
        message = str(text)
        level = "INFO"
        source = "system"

        match = self._LOG_PREFIX_PATTERN.match(message.strip())
        if match:
            parsed_level = self._normalize_level(match.group("level"))
            parsed_source = str(match.group("source")).strip().lower()
            if parsed_level:
                level = parsed_level
            if parsed_source:
                source = parsed_source

        return {
            "text": message,
            "text_lower": message.lower(),
            "level": level,
            "source": source,
        }

    # Add a source value to the source filter if it is new.
    def _ensure_source_exists(self, source: str) -> None:
        if not source:
            return
        if self.source_combo.findText(source) < 0:
            self.source_combo.addItem(source)

    # Append one message to memory and enforce max history size.
    def _append_entry(self, message: str) -> tuple[dict[str, str], bool]:
        entry = self._parse_log_entry(message)
        self._entries.append(entry)
        dropped = False
        overflow = len(self._entries) - self._MAX_ENTRIES
        if overflow > 0:
            del self._entries[:overflow]
            dropped = True
        self._ensure_source_exists(entry["source"])
        return entry, dropped

    # Return True when no custom log filters are applied.
    def _has_default_filters(self) -> bool:
        return (
            self.level_combo.currentText().strip().upper() == "ALL"
            and self.source_combo.currentText().strip().lower() == "all"
            and not self.search_edit.text().strip()
        )

    # Check whether one log entry passes the current filters.
    def _matches_filters(self, entry: dict[str, str]) -> bool:
        selected_level = self.level_combo.currentText().strip().upper()
        selected_source = self.source_combo.currentText().strip().lower()
        search_text = self.search_edit.text().strip().lower()

        if selected_level and selected_level != "ALL" and entry["level"] != selected_level:
            return False
        if selected_source and selected_source != "all" and entry["source"] != selected_source:
            return False
        if search_text and search_text not in entry["text_lower"]:
            return False
        return True

    # Rebuild the log view from filtered in-memory entries.
    def _apply_filters(self, *_: Any) -> None:
        filtered_lines = [entry["text"] for entry in self._entries if self._matches_filters(entry)]
        self.log_view.setPlainText("\n".join(filtered_lines))
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_view.setTextCursor(cursor)

    # Merge new ticks/messages into the log view.
    def update(self, payload: dict[str, Any] | None = None) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("clear"):
            self._entries.clear()
            self.log_view.clear()
            return

        has_new_entries = False
        dropped_entries = False
        new_entries = []

        ticks = payload.get("ticks") or []
        for tick in ticks:
            if isinstance(tick, dict):
                entry, dropped = self._append_entry(self._format_tick_log_message(tick))
                new_entries.append(entry)
                dropped_entries = dropped_entries or dropped
                has_new_entries = True

        message = payload.get("message")
        if message:
            entry, dropped = self._append_entry(str(message))
            new_entries.append(entry)
            dropped_entries = dropped_entries or dropped
            has_new_entries = True

        messages = payload.get("messages") or []
        for item in messages:
            entry, dropped = self._append_entry(str(item))
            new_entries.append(entry)
            dropped_entries = dropped_entries or dropped
            has_new_entries = True

        if has_new_entries:
            if self._has_default_filters() and not dropped_entries:
                if new_entries:
                    cursor = self.log_view.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    if self.log_view.document().characterCount() > 1:
                        cursor.insertText("\n")
                    cursor.insertText("\n".join(entry["text"] for entry in new_entries))
                    self.log_view.setTextCursor(cursor)
                    self.log_view.ensureCursorVisible()
            else:
                self._apply_filters()
