"""Unit tests for the reservation ledger math (I5, scenario T4)."""
from __future__ import annotations

import pytest

from core.execution.reservation import OverReserveError, available, try_reserve


def test_available_is_open_minus_reserved() -> None:
    assert available(10, 0) == 10
    assert available(10, 4) == 6
    assert available(-10, 4) == 6   # |open|, so a short reserves the same way


def test_reserve_up_to_available() -> None:
    assert try_reserve(10, 0, 10) == 10        # exactly full is allowed
    assert try_reserve(10, 3, 7) == 10


def test_t4_double_click_second_close_rejected() -> None:
    # T4: first close reserves the whole leg; the second (racing) close sees no
    # available and is refused — over-close eliminated by the invariant.
    reserved = try_reserve(5, 0, 5)
    assert reserved == 5
    with pytest.raises(OverReserveError):
        try_reserve(5, reserved, 1)


def test_negative_request_rejected() -> None:
    with pytest.raises(ValueError):
        try_reserve(5, 0, -1)
