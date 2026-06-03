"""Unit tests for ``core.products.product_label_from_symbol``."""
from __future__ import annotations

import pytest

from core.products import PRODUCT_LABELS, product_label_from_symbol


class TestStructureTypeMapping:
    @pytest.mark.parametrize("st", ["vanilla_call", "short_vanilla_call"])
    def test_vanilla_call_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Vanilla Call"

    @pytest.mark.parametrize("st", ["vanilla_put", "short_vanilla_put"])
    def test_vanilla_put_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Vanilla Put"

    @pytest.mark.parametrize("st", ["straddle_atm", "short_straddle_atm"])
    def test_straddle_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Straddle"

    @pytest.mark.parametrize("st", ["long_strangle_25d", "short_strangle"])
    def test_strangle_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Strangle"

    @pytest.mark.parametrize("st", ["long_butterfly_25d", "short_butterfly_25d"])
    def test_butterfly_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Butterfly"

    @pytest.mark.parametrize("st", ["calendar_long", "calendar_short"])
    def test_calendar_both_sides(self, st: str) -> None:
        assert product_label_from_symbol(None, st) == "Calendar"


class TestFutureSplit:
    @pytest.mark.parametrize("sym", ["6EM6", "6EU6", "6EZ7"])
    def test_full_size_future(self, sym: str) -> None:
        assert product_label_from_symbol(sym, "future_buy") == "Future - 6E"
        assert product_label_from_symbol(sym, "future_sell") == "Future - 6E"

    @pytest.mark.parametrize("sym", ["M6EM6", "M6EU6"])
    def test_micro_future(self, sym: str) -> None:
        assert product_label_from_symbol(sym, "future_buy") == "Future - M6E"
        assert product_label_from_symbol(sym, "future_sell") == "Future - M6E"

    def test_future_structure_type_without_symbol_falls_back_to_full_size(self) -> None:
        # structure_type says it's a future ; no symbol to disambiguate -> default to full-size 6E.
        assert product_label_from_symbol(None, "future_buy") == "Future - 6E"


class TestSymbolOnlyFallback:
    """Path taken by IB-live positions that bypass trade_structure
    (no structure_type recorded in DB)."""

    def test_call_option_symbol(self) -> None:
        assert product_label_from_symbol("EUUQ6 C1130", None) == "Vanilla Call"

    def test_put_option_symbol(self) -> None:
        assert product_label_from_symbol("EUUN6 P1170", None) == "Vanilla Put"

    def test_full_size_future_symbol(self) -> None:
        assert product_label_from_symbol("6EM6", None) == "Future - 6E"

    def test_micro_future_symbol(self) -> None:
        assert product_label_from_symbol("M6EU6", None) == "Future - M6E"


class TestEdgeCases:
    def test_both_none_returns_none(self) -> None:
        assert product_label_from_symbol(None, None) is None

    def test_empty_strings_return_none(self) -> None:
        assert product_label_from_symbol("", None) is None
        assert product_label_from_symbol("   ", None) is None

    def test_unknown_structure_type_with_no_symbol_returns_none(self) -> None:
        assert product_label_from_symbol(None, "unknown_thing") is None

    def test_unknown_structure_type_falls_back_to_symbol(self) -> None:
        # If the unrecognised structure_type is paired with a parseable symbol,
        # the symbol path still produces a label.
        assert product_label_from_symbol("EUUQ6 C1130", "weird_code") == "Vanilla Call"

    def test_helper_never_raises_on_garbage(self) -> None:
        assert product_label_from_symbol("???", None) == "Future - 6E"  # symbol fallback
        # The symbol fallback's last branch is _future_label, which is
        # deliberately permissive — better a default than an exception.

    def test_label_is_in_canonical_set(self) -> None:
        for st in ["vanilla_call", "vanilla_put", "straddle_atm", "long_strangle_25d",
                   "long_butterfly_25d", "calendar_long"]:
            assert product_label_from_symbol(None, st) in PRODUCT_LABELS
        assert product_label_from_symbol("6EM6", "future_buy") in PRODUCT_LABELS
        assert product_label_from_symbol("M6EU6", "future_sell") in PRODUCT_LABELS


class TestParentStructureWinsOverSymbolForMultiLegProducts:
    """When a Position is a leg of a multi-leg booking, sync_positions
    passes parent.structure_type to the helper so the per-leg call/put
    name doesn't leak into the UI label. Lock that behaviour for every
    multi-leg product."""

    # (parent.structure_type, leg IB symbol, expected leg label).
    # Each row simulates one ``Position`` row inserted by
    # ``sync_positions_from_ib`` for a leg of the parent structure.
    @pytest.mark.parametrize("parent_st,leg_sym,expected", [
        # ── Straddle (2 legs : ATM call + ATM put on the same expiry).
        ("straddle_atm",       "EUUU6 C1165", "Straddle"),
        ("straddle_atm",       "EUUU6 P1165", "Straddle"),
        ("short_straddle_atm", "EUUU6 C1165", "Straddle"),
        ("short_straddle_atm", "EUUU6 P1165", "Straddle"),
        # ── Strangle (2 legs : OTM call + OTM put, different strikes).
        ("long_strangle_25d",  "EUUU6 C1185", "Strangle"),
        ("long_strangle_25d",  "EUUU6 P1145", "Strangle"),
        ("short_strangle",     "EUUU6 C1185", "Strangle"),
        ("short_strangle",     "EUUU6 P1145", "Strangle"),
        # ── Butterfly (3 legs : 2 wings + 1 body).
        ("long_butterfly_25d",  "EUUU6 C1185", "Butterfly"),
        ("long_butterfly_25d",  "EUUU6 P1145", "Butterfly"),
        ("long_butterfly_25d",  "EUUU6 C1165", "Butterfly"),
        ("short_butterfly_25d", "EUUU6 P1165", "Butterfly"),
        # ── Calendar (2 legs : near + far tenor, same strike).
        ("calendar_long",  "EUUN6 C1170", "Calendar"),
        ("calendar_long",  "EUUU6 C1170", "Calendar"),
        ("calendar_short", "EUUN6 P1170", "Calendar"),
        ("calendar_short", "EUUU6 P1170", "Calendar"),
        # ── Vanilla call / put (single-leg ; parent matches symbol).
        ("vanilla_call",       "EUUQ6 C1130", "Vanilla Call"),
        ("short_vanilla_call", "EUUQ6 C1130", "Vanilla Call"),
        ("vanilla_put",        "EUUQ6 P1130", "Vanilla Put"),
        ("short_vanilla_put",  "EUUQ6 P1130", "Vanilla Put"),
        # ── Futures (single leg ; 6E full / M6E micro split honoured).
        ("future_buy",  "6EM6",  "Future - 6E"),
        ("future_buy",  "M6EM6", "Future - M6E"),
        ("future_sell", "6EU6",  "Future - 6E"),
        ("future_sell", "M6EU6", "Future - M6E"),
    ])
    def test_leg_label_inherits_parent_product(
        self, parent_st: str, leg_sym: str, expected: str,
    ) -> None:
        assert product_label_from_symbol(leg_sym, parent_st) == expected
