"""Unit tests for persistence.payloads — R2 PR #4 mapping layer.

Pure-function tests : engine-native dict in, DB row dict out. No DB,
no Controller, no IB.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from persistence.payloads import (
    build_account_snap_row,
    build_signal_rows,
    build_vol_surface_row,
)


def _ib_row(tag: str, currency: str, value: str | float) -> SimpleNamespace:
    """Fake ib_insync AccountValue — only the 3 attributes payloads reads."""
    return SimpleNamespace(tag=tag, currency=currency, value=str(value))


# --- account_snaps ---------------------------------------------------------


@pytest.mark.unit
class TestBuildAccountSnapRow:
    def test_extracts_all_usd_scalars_from_summary(self):
        summary = [
            _ib_row("NetLiquidation", "USD", "125000.50"),
            _ib_row("BuyingPower", "USD", "250000"),
            _ib_row("AvailableFunds", "USD", "90000"),
            _ib_row("UnrealizedPnL", "USD", "-250.25"),
            _ib_row("RealizedPnL", "USD", "1200.00"),
            _ib_row("GrossPositionValue", "USD", "45000"),
        ]
        row = build_account_snap_row(
            summary=summary,
            positions=[],
            cash_balances={"USD": 75000.5},
        )
        assert row["net_liq_usd"] == Decimal("125000.50")
        assert row["buying_power_usd"] == Decimal("250000")
        assert row["available_usd"] == Decimal("90000")
        assert row["unrealized_pnl_usd"] == Decimal("-250.25")
        assert row["realized_pnl_usd"] == Decimal("1200.00")
        assert row["gross_position_value_usd"] == Decimal("45000")
        assert row["cash_usd"] == Decimal("75000.5")
        assert row["currencies"] == {"USD": 75000.5}
        assert row["open_positions_count"] == 0
        assert isinstance(row["timestamp"], datetime)

    def test_missing_tags_become_none(self):
        row = build_account_snap_row(summary=[], positions=None, cash_balances=None)
        assert row["net_liq_usd"] is None
        assert row["cash_usd"] is None
        assert row["currencies"] == {}
        assert row["open_positions_count"] == 0

    def test_non_usd_currency_rows_are_ignored(self):
        """A NetLiquidation tagged EUR must not leak into net_liq_usd."""
        row = build_account_snap_row(
            summary=[_ib_row("NetLiquidation", "EUR", "100000")],
            positions=[],
            cash_balances={},
        )
        assert row["net_liq_usd"] is None

    def test_open_positions_count_matches_positions_list(self):
        row = build_account_snap_row(
            summary=[],
            positions=[object(), object(), object()],
            cash_balances={},
        )
        assert row["open_positions_count"] == 3

    def test_explicit_timestamp_is_used(self):
        ts = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
        row = build_account_snap_row(summary=[], positions=[], cash_balances={}, timestamp=ts)
        assert row["timestamp"] == ts


# --- vol_surfaces ----------------------------------------------------------


def _vol_result(**overrides) -> dict:
    base = {
        "type": "vol_result",
        "timestamp": 1_700_000_000.0,
        "spot": 1.17850,
        "pillar_rows": [
            {
                "tenor_label": "1M",
                "dte": 30,
                "sigma_ATM_pct": 7.5,
                "sigma_fair_pct": 7.4,
                "ecart_pct": 0.1,
                "signal": "CHEAP",
                "RV_pct": 7.6,
            },
            {
                "tenor_label": "3M",
                "dte": 90,
                "sigma_ATM_pct": 8.0,
                "sigma_fair_pct": 7.9,
                "ecart_pct": 0.1,
                "signal": "FAIR",
                "RV_pct": 8.1,
            },
        ],
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestBuildVolSurfaceRow:
    def test_full_row_shape(self):
        row = build_vol_surface_row(
            vol_result=_vol_result(),
            underlying="EURUSD",
            spot=1.17652,
        )
        assert row["underlying"] == "EURUSD"
        assert row["spot"] == Decimal("1.17652")
        assert row["forward"] == Decimal("1.17850")
        assert set(row["surface_data"].keys()) == {"1M", "3M"}
        assert row["fair_vol_data"] == {"1M": 7.4, "3M": 7.9}
        assert row["rv_data"] == {"1M": 7.6, "3M": 8.1}

    def test_spot_fallback_to_forward_when_missing(self):
        row = build_vol_surface_row(_vol_result(), "EURUSD", spot=None)
        assert row["spot"] == Decimal("1.17850")  # = forward

    def test_empty_fair_vol_data_becomes_none(self):
        vol_result = _vol_result()
        for p in vol_result["pillar_rows"]:
            p["sigma_fair_pct"] = None
        row = build_vol_surface_row(vol_result, "EURUSD", spot=1.0)
        assert row["fair_vol_data"] is None


# --- signals ---------------------------------------------------------------


@pytest.mark.unit
class TestBuildSignalRows:
    def test_emits_one_row_per_complete_pillar(self):
        rows = build_signal_rows(_vol_result(), "EURUSD")
        assert len(rows) == 2
        first = rows[0]
        assert first["underlying"] == "EURUSD"
        assert first["tenor"] == "1M"
        assert first["dte"] == 30
        assert first["sigma_mid"] == Decimal("7.5")
        assert first["sigma_fair"] == Decimal("7.4")
        assert first["signal_type"] == "CHEAP"
        assert first["rv"] == Decimal("7.6")

    def test_skips_pillars_missing_sigma_or_signal(self):
        vol_result = _vol_result()
        # corrupt one pillar : sigma missing
        vol_result["pillar_rows"][0]["sigma_ATM_pct"] = None
        # corrupt the other : invalid signal
        vol_result["pillar_rows"][1]["signal"] = "UNKNOWN"
        rows = build_signal_rows(vol_result, "EURUSD")
        assert rows == []

    def test_no_pillars_returns_empty_list(self):
        assert build_signal_rows({"pillar_rows": []}, "EURUSD") == []
        assert build_signal_rows({}, "EURUSD") == []
