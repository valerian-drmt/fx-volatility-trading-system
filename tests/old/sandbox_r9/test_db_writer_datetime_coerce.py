"""Tests for engines.db_writer.service._coerce_datetime_fields.

publish_db_event serialises datetime objects to ISO strings via
json.dumps(default=str). asyncpg then refuses them on
DateTime(timezone=True) columns. The helper converts the known-named
datetime fields back to real datetime objects before the writer's
queue.
"""
from __future__ import annotations

from datetime import UTC, datetime


def test_coerce_timestamp_iso_with_plus_offset() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({"timestamp": "2026-04-22T14:00:00+00:00", "underlying": "EURUSD"})
    assert isinstance(out["timestamp"], datetime)
    assert out["timestamp"].tzinfo is not None
    assert out["underlying"] == "EURUSD"


def test_coerce_timestamp_iso_with_z_suffix() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({"timestamp": "2026-04-22T14:00:00Z"})
    assert isinstance(out["timestamp"], datetime)
    assert out["timestamp"].tzinfo == UTC


def test_coerce_skips_unknown_keys() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({
        "timestamp": "2026-04-22T14:00:00Z",
        "surface_data": {"1M": {"atm": {"iv": 0.06}}},
        "spot": 1.17,
    })
    assert isinstance(out["timestamp"], datetime)
    # Nested strings inside surface_data must NOT be touched.
    assert out["surface_data"] == {"1M": {"atm": {"iv": 0.06}}}
    assert out["spot"] == 1.17


def test_coerce_multiple_datetime_fields() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({
        "timestamp": "2026-04-22T14:00:00Z",
        "opened_at": "2026-04-21T09:15:00+00:00",
        "closed_at": "2026-04-22T12:00:00+00:00",
    })
    assert all(isinstance(out[k], datetime) for k in ("timestamp", "opened_at", "closed_at"))


def test_coerce_garbage_string_is_left_untouched() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({"timestamp": "not-a-date", "underlying": "EURUSD"})
    # Garbage stays as-is ; the INSERT will fail downstream but the warning
    # is logged via the helper. We don't want to silently drop the frame.
    assert out["timestamp"] == "not-a-date"


def test_coerce_missing_fields_no_op() -> None:
    from engines.db_writer.service import _coerce_datetime_fields

    out = _coerce_datetime_fields({"underlying": "EURUSD", "spot": 1.17})
    assert out == {"underlying": "EURUSD", "spot": 1.17}
