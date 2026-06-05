"""Unit tests for bus.keys and bus.channels — R3 PR #2.

Pure string/constant checks. No Redis, no network.
"""

from __future__ import annotations

import pytest

from bus import channels, keys


@pytest.mark.unit
class TestKeyFormat:
    def test_latest_spot_formats_with_symbol(self):
        assert keys.LATEST_SPOT.format(symbol="EURUSD") == "latest_spot:EURUSD"

    def test_latest_bid_ask_format(self):
        assert keys.LATEST_BID.format(symbol="GBPUSD") == "latest_bid:GBPUSD"
        assert keys.LATEST_ASK.format(symbol="USDJPY") == "latest_ask:USDJPY"

    def test_latest_vol_surface_formats_with_symbol(self):
        assert (
            keys.LATEST_VOL_SURFACE.format(symbol="EURUSD")
            == "latest_vol_surface:EURUSD"
        )

    def test_latest_signals_formats_with_symbol(self):
        assert keys.LATEST_SIGNALS.format(symbol="EURUSD") == "latest_signals:EURUSD"

    def test_market_status_formats_with_symbol(self):
        assert keys.MARKET_STATUS.format(symbol="EURUSD") == "market_status:EURUSD"

    def test_heartbeat_formats_with_engine_name(self):
        assert (
            keys.HEARTBEAT.format(engine_name=keys.ENGINE_MARKET_DATA)
            == "heartbeat:market_data"
        )
        assert (
            keys.HEARTBEAT.format(engine_name=keys.ENGINE_VOL) == "heartbeat:vol_engine"
        )
        assert (
            keys.HEARTBEAT.format(engine_name=keys.ENGINE_RISK)
            == "heartbeat:risk_engine"
        )

    def test_portfolio_keys_are_final_strings(self):
        """Portfolio-level keys are not templated."""
        assert keys.LATEST_GREEKS_PORTFOLIO == "latest_greeks:portfolio"
        assert keys.LATEST_PNL_CURVE == "latest_pnl_curve"
        assert keys.ACCOUNT_SNAPSHOT == "account_snapshot"


@pytest.mark.unit
class TestTtlPolicy:
    def test_short_ttl_matches_spec_30s(self):
        assert keys.TTL_SPOT == 30
        assert keys.TTL_BID_ASK == 30
        assert keys.TTL_GREEKS == 30
        assert keys.TTL_HEARTBEAT == 30
        assert keys.TTL_PNL_CURVE == 30

    def test_account_ttl_is_60s(self):
        assert keys.TTL_ACCOUNT == 60

    def test_long_ttl_surfaces_and_signals_are_10min(self):
        assert keys.TTL_VOL_SURFACE == 600
        assert keys.TTL_SIGNALS == 600

    def test_market_status_ttl_is_5min(self):
        assert keys.TTL_MARKET_STATUS == 300


@pytest.mark.unit
class TestChannels:
    def test_channel_names_are_flat_strings(self):
        assert channels.CH_TICKS == "ticks"
        assert channels.CH_ACCOUNT == "account"
        assert channels.CH_VOL_UPDATE == "vol_update"
        assert channels.CH_RISK_UPDATE == "risk_update"
        assert channels.CH_SYSTEM_ALERTS == "system_alerts"

    def test_channel_names_are_unique(self):
        """Two channels must never share a name — subscribers filter on it."""
        all_channels = [
            channels.CH_TICKS, channels.CH_ACCOUNT, channels.CH_VOL_UPDATE,
            channels.CH_RISK_UPDATE, channels.CH_SYSTEM_ALERTS,
        ]
        assert len(set(all_channels)) == len(all_channels)
