"""Settings mixin — load, validate, save, and migrate app configuration."""
import json
import logging
from typing import Any

logger = logging.getLogger("controller")


class SettingsMixin:
    """Mixin for Controller: settings load/save/validate/migrate."""

    DEFAULT_STATUS_SETTINGS = {
        "host": "127.0.0.1",
        "port": 4002,
        "client_id": 1,
        "client_roles": {"market_data": 1, "vol_engine": 2, "risk_engine": 3},
        "readonly": False,
        "market_symbol": "EURUSD",
    }
    DEFAULT_RUNTIME_SETTINGS = {
        "tick_interval_ms": 100,
        "snapshot_interval_ms": 2000,
    }

    def _read_status_settings_from_panel(self) -> dict[str, Any]:
        """Read status settings from UI controls (or current state fallback)."""
        roles = dict(getattr(self, "client_roles", {"market_data": 1, "vol_engine": 2, "risk_engine": 3}))
        if self.window is None:
            return {
                "host": self.host, "port": self.port, "client_id": self.client_id,
                "client_roles": roles, "readonly": False, "market_symbol": self.market_symbol,
            }
        return {
            "host": self.host, "port": self.port, "client_id": self.client_id,
            "client_roles": roles, "readonly": False,
            "market_symbol": self.window.chart_panel.market_symbol_input.currentText().strip().upper(),
        }

    def _apply_status_settings(self, settings: dict[str, Any]) -> None:
        """Apply validated status settings to controller and IB client."""
        self.host = str(settings["host"])
        self.port = int(settings["port"])
        self.client_id = int(settings["client_id"])
        self.client_roles = dict(settings.get("client_roles", {"market_data": 1, "vol_engine": 2, "risk_engine": 3}))
        self.readonly = False
        self.market_symbol = str(settings["market_symbol"]).upper()
        self.ib_client.host = self.host
        self.ib_client.port = self.port
        self.ib_client.client_id = self.client_id
        self.ib_client.readonly = False

    def _load_app_settings(self) -> dict[str, Any]:
        """Load and validate persisted app settings with fallback defaults."""
        defaults = self._default_app_settings()
        if not self._settings_path.exists():
            logger.info("Settings file missing, creating defaults at %s", self._settings_path)
            self._write_full_app_settings(defaults)
            return defaults
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
            return self._validate_app_settings(raw)
        except Exception as exc:
            logger.warning("Invalid settings (%s): %s — resetting to defaults", self._settings_path, exc)
            self._write_full_app_settings(defaults)
            return defaults

    @staticmethod
    def _validate_app_settings(raw: dict[str, Any]) -> dict[str, Any]:
        """Validate whole app settings and normalize legacy payloads."""
        if not isinstance(raw, dict):
            raise ValueError("Settings payload must be a JSON object")
        status_payload = raw.get("status")
        normalized_status = dict(status_payload) if isinstance(status_payload, dict) else dict(raw)
        if "market_symbol" not in normalized_status:
            legacy_streaming = raw.get("live_streaming")
            if isinstance(legacy_streaming, dict):
                normalized_status["market_symbol"] = legacy_streaming.get("market_symbol", "EURUSD")
        runtime_payload = raw.get("runtime") or {}
        return {
            "status": SettingsMixin._validate_status_settings(normalized_status),
            "runtime": SettingsMixin._validate_runtime_settings(runtime_payload),
        }

    @staticmethod
    def _validate_runtime_settings(raw: dict[str, Any]) -> dict[str, int]:
        """Validate runtime timing settings and enforce safe bounds."""
        if not isinstance(raw, dict):
            raise ValueError("Runtime settings payload must be a JSON object")
        tick = int(raw.get("tick_interval_ms", SettingsMixin.DEFAULT_RUNTIME_SETTINGS["tick_interval_ms"]))
        snap = int(raw.get("snapshot_interval_ms", SettingsMixin.DEFAULT_RUNTIME_SETTINGS["snapshot_interval_ms"]))
        if tick < 25:
            raise ValueError("Runtime setting 'tick_interval_ms' must be >= 25")
        if snap < 250:
            raise ValueError("Runtime setting 'snapshot_interval_ms' must be >= 250")
        if snap < tick:
            raise ValueError("Runtime setting 'snapshot_interval_ms' must be >= tick_interval_ms")
        return {"tick_interval_ms": tick, "snapshot_interval_ms": snap}

    @staticmethod
    def _validate_status_settings(raw: dict[str, Any]) -> dict[str, Any]:
        """Validate status/connection settings and normalize fields."""
        if not isinstance(raw, dict):
            raise ValueError("Settings payload must be a JSON object")
        required = ("host", "port")
        missing = [k for k in required if k not in raw]
        if missing:
            raise ValueError(f"Missing settings keys: {', '.join(missing)}")
        host = str(raw["host"]).strip()
        if not host:
            raise ValueError("Settings 'host' cannot be empty")
        symbol = str(raw.get("market_symbol", "EURUSD")).strip().upper()
        if not symbol:
            raise ValueError("Settings 'market_symbol' cannot be empty")
        raw_roles = raw.get("client_roles")
        default_roles = {"market_data": 1, "vol_engine": 2, "risk_engine": 3}
        if isinstance(raw_roles, dict):
            roles = {k: int(raw_roles.get(k, default_roles[k])) for k in default_roles}
        else:
            roles = dict(default_roles)
        ids = list(roles.values())
        if len(set(ids)) != len(ids):
            raise ValueError("Client role ids must be distinct (market_data, vol_engine, risk_engine).")
        return {
            "host": host, "port": int(raw["port"]), "client_id": int(roles["market_data"]),
            "client_roles": roles, "readonly": False, "market_symbol": symbol,
        }

    def _save_app_settings(self) -> None:
        """Persist current status and runtime settings to disk."""
        status_settings = self._validate_status_settings(self._read_status_settings_from_panel())
        self._apply_status_settings(status_settings)
        runtime_settings = self._validate_runtime_settings({
            "tick_interval_ms": self.tick_interval_ms,
            "snapshot_interval_ms": self.snapshot_interval_ms,
        })
        self._write_app_settings(status_settings, runtime_settings)

    @staticmethod
    def _default_app_settings() -> dict[str, dict[str, Any]]:
        """Return default app settings payload."""
        status_defaults = dict(SettingsMixin.DEFAULT_STATUS_SETTINGS)
        status_defaults["client_roles"] = dict({"market_data": 1, "vol_engine": 2, "risk_engine": 3})
        return {"status": status_defaults, "runtime": dict(SettingsMixin.DEFAULT_RUNTIME_SETTINGS)}

    def _write_full_app_settings(self, app_settings: dict[str, Any]) -> None:
        """Validate and write a full app settings payload."""
        validated = self._validate_app_settings(app_settings)
        self._write_app_settings(validated["status"], validated["runtime"])

    def _write_app_settings(self, status_settings: dict[str, Any], runtime_settings: dict[str, Any]) -> None:
        """Write split status/runtime settings payload to disk."""
        app_settings = {"status": status_settings, "runtime": runtime_settings}
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(app_settings, indent=2), encoding="utf-8")
            logger.info("Saved settings to %s", self._settings_path)
        except Exception as exc:
            logger.error("Failed to save settings: %s", exc)

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        from ui.panels.settings_panel import SettingsPanel
        vol_path = self._project_root / "config" / "vol_config.json"
        status_path = self._project_root / "config" / "status_panel_settings.json"
        dialog = SettingsPanel(vol_config_path=vol_path, status_config_path=status_path, parent=None)
        self._settings_dialog = dialog
        dialog.accepted.connect(self._on_settings_saved)
        dialog.finished.connect(lambda _: setattr(self, "_settings_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_settings_saved(self) -> None:
        """Reload connection settings from disk and update status panel labels."""
        app_settings = self._load_app_settings()
        s = app_settings.get("status", {})
        self.host = str(s.get("host", self.host))
        self.port = int(s.get("port", self.port))
        self.client_id = int(s.get("client_id", self.client_id))
        if self.window is not None:
            panel = self.window.status_panel
            panel.host_input.setText(self.host)
            panel.port_input.setText(str(self.port))
            panel.client_id_input.setText(str(self.client_id))
        self._log("[INFO][settings] Settings saved — changes apply on next scan cycle")
