"""event_hash + EventDeduplicator unit tests."""
from __future__ import annotations

from datetime import UTC, datetime

from api.orchestration.events.deduplicator import EventDeduplicator
from api.orchestration.events.hashing import event_hash
from api.orchestration.events.sources.base import RawEvent


def _ev(event_type="NFP", region="US", ts=None, secs=0) -> RawEvent:
    return RawEvent(
        event_type=event_type, region=region, impact="high",
        scheduled_at=(ts or datetime(2026, 5, 2, 12, 30, secs, tzinfo=UTC)),
        description="test", source_name="fake",
    )


def test_hash_is_stable():
    h1 = event_hash(_ev())
    h2 = event_hash(_ev())
    assert h1 == h2 and len(h1) == 16


def test_hash_truncates_seconds_to_minute():
    """14:30:00 and 14:30:15 should hash to the same value."""
    a = event_hash(_ev(secs=0))
    b = event_hash(_ev(secs=15))
    c = event_hash(_ev(secs=59))
    assert a == b == c


def test_hash_differs_on_event_type():
    assert event_hash(_ev(event_type="NFP")) != event_hash(_ev(event_type="CPI"))


def test_hash_differs_on_region():
    assert event_hash(_ev(region="US")) != event_hash(_ev(region="EU"))


def test_hash_differs_on_minute():
    a = _ev(ts=datetime(2026, 5, 2, 12, 30, tzinfo=UTC))
    b = _ev(ts=datetime(2026, 5, 2, 12, 31, tzinfo=UTC))
    assert event_hash(a) != event_hash(b)


def test_dedup_keeps_first_seen():
    e1 = _ev(event_type="NFP")
    e2 = _ev(event_type="NFP")  # same identity
    out = EventDeduplicator().dedupe([e1, e2])
    assert len(out) == 1
    assert out[0][1] is e1  # first wins


def test_dedup_preserves_distinct_events():
    out = EventDeduplicator().dedupe([
        _ev(event_type="NFP"),
        _ev(event_type="CPI"),
        _ev(event_type="FOMC", ts=datetime(2026, 5, 14, 18, 0, tzinfo=UTC)),
    ])
    assert len(out) == 3


def test_dedup_handles_empty():
    assert EventDeduplicator().dedupe([]) == []


def test_dedup_intra_cycle_two_sources_same_release():
    """FRED and BLS could both publish the NFP — dedup must keep 1."""
    fred = _ev(event_type="NFP")
    bls_same_minute = _ev(
        event_type="NFP",
        ts=datetime(2026, 5, 2, 12, 30, 47, tzinfo=UTC),  # different seconds
    )
    out = EventDeduplicator().dedupe([fred, bls_same_minute])
    assert len(out) == 1


def test_dedup_keeps_when_minute_differs():
    a = _ev(event_type="NFP", ts=datetime(2026, 5, 2, 12, 30, tzinfo=UTC))
    b = _ev(event_type="NFP", ts=datetime(2026, 5, 2, 12, 31, tzinfo=UTC))
    assert len(EventDeduplicator().dedupe([a, b])) == 2
