"""EventSource ABC + RawEvent dataclass — cf. spec §1."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Region = Literal["US", "EU", "GB"]
Impact = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class RawEvent:
    """Event as returned by a Source, before dedup and persist.

    Invariant: ``scheduled_at`` is tz-aware UTC (or convertible). Any source
    producing a naive datetime is buggy — the scheduler detects it at
    parsing time if needed.
    """

    event_type: str          # "FOMC", "NFP", "CPI", ...
    region: Region
    impact: Impact
    scheduled_at: datetime
    description: str
    source_name: str         # for debugging / cross-source dedup audit


class EventSource(ABC):
    """Contract for every economic-events source.

    A subclass should:
    - define ``name`` (unique str)
    - implement ``fetch()`` (network I/O)
    - keep the parsing in a ``_parse(payload)`` method testable without network
    """

    name: str = "abstract"
    timeout_seconds: float = 10.0
    expected_min_events: int = 1

    @abstractmethod
    async def fetch(self) -> list[RawEvent]:
        """Fetch + parse + filter. Must raise an exception on failure.

        The scheduler catches it — do not swallow here or we lose the trace
        of drifts (broken parser, source 404, etc.).
        """
        ...
