"""Desk latency timers in the execution engine (#370, hot/cold T6).

Two promises the hot path makes, measured instead of assumed:

* **order submit**: from the moment the api received the operator's request
  (``X-Request-Start`` forwarded by the api, else this engine's own receive
  time) to IB's first ``Submitted`` / ``PreSubmitted`` status on the order.
  ``note_submit_start`` is called right before ``placeOrder`` with the row's
  key; ``observe_submitted`` on the first live status.
* **fill to publish**: from IB's execution time to the moment the engine
  published the change, on the ``orders:<id>`` channel (the fill event) and
  on the ``positions`` channel (the next position sync that carried the
  row). ``note_fill`` remembers the fill per IB localSymbol for a short
  window; ``observe_positions_publish`` matches the rows a sync published.

Everything here is best-effort: a missing note is simply not observed, and
the dicts are pruned on a short TTL so a status that never comes cannot leak.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from shared.trace import current_request_start

logger = logging.getLogger(__name__)

ENGINE = "execution_engine"
# A submit that has not been acknowledged after this long is forgotten (the
# stuck-order watcher owns that case); a fill not matched by a positions
# publish after this long is forgotten too (the sync would have carried it).
SUBMIT_TTL_S = 300.0
FILL_TTL_S = 120.0

SubmitKey = tuple[str, int]

_submits: dict[SubmitKey, float] = {}
_fills: dict[str, list[float]] = {}


def _hist(name: str) -> Any:
    try:
        from shared import observability

        return getattr(observability, name)
    except Exception:  # metrics are never load-bearing
        return None


def _prune(now: float) -> None:
    for k, t0 in list(_submits.items()):
        if now - t0 > SUBMIT_TTL_S:
            _submits.pop(k, None)
    for sym, times in list(_fills.items()):
        kept = [t for t in times if now - t <= FILL_TTL_S]
        if kept:
            _fills[sym] = kept
        else:
            _fills.pop(sym, None)


# ───────────────────────────────────────────── order submit ──────────────

def note_submit_start(key: SubmitKey, t0: float | None = None) -> None:
    """Remember when the request behind ``key`` started. ``t0`` defaults to the
    request start bound by the engine's middleware (the api's receive time
    when it forwarded ``X-Request-Start``), else now."""
    now = time.time()
    start = t0 if t0 is not None else (current_request_start() or now)
    _submits[key] = start
    _prune(now)


def observe_submitted(key: SubmitKey, *, path: str = "structure") -> float | None:
    """First live status for ``key``: observe and forget. Returns the seconds."""
    t0 = _submits.pop(key, None)
    if t0 is None:
        return None
    dt = max(0.0, time.time() - t0)
    h = _hist("order_submit_latency_seconds")
    if h is not None:
        h.labels(engine=ENGINE, path=path).observe(dt)
    logger.info("order_submit_latency path=%s key=%s seconds=%.3f", path, key, dt)
    return dt


# ───────────────────────────────────────────── fill to publish ───────────

def _epoch(exec_time: Any) -> float | None:
    if exec_time is None:
        return None
    if isinstance(exec_time, (int, float)):
        return float(exec_time)
    if isinstance(exec_time, datetime):
        t = exec_time if exec_time.tzinfo else exec_time.replace(tzinfo=UTC)
        return t.timestamp()
    return None


def note_fill(local_symbol: str | None, exec_time: Any) -> None:
    """A fill landed on ``local_symbol`` at IB time ``exec_time``; the next
    positions publish that carries that symbol closes the loop."""
    t = _epoch(exec_time)
    if not local_symbol or t is None:
        return
    _fills.setdefault(str(local_symbol), []).append(t)
    _prune(time.time())


def observe_orders_publish(exec_time: Any) -> float | None:
    """The fill was just published on ``orders:<structure_id>``."""
    t = _epoch(exec_time)
    if t is None:
        return None
    dt = max(0.0, time.time() - t)
    h = _hist("fill_to_publish_seconds")
    if h is not None:
        h.labels(engine=ENGINE, channel="orders").observe(dt)
    return dt


def observe_positions_publish(local_symbols: Iterable[str | None], now: float | None = None) -> int:
    """A position sync published these rows: observe every pending fill on
    them against ``now`` (the publish time). Returns the fills matched."""
    now = now if now is not None else time.time()
    h = _hist("fill_to_publish_seconds")
    matched = 0
    for sym in local_symbols:
        times = _fills.pop(str(sym), None) if sym else None
        if not times:
            continue
        for t in times:
            dt = max(0.0, now - t)
            if h is not None:
                h.labels(engine=ENGINE, channel="positions").observe(dt)
            matched += 1
        logger.info("fill_to_positions_publish symbol=%s n=%d max_seconds=%.3f",
                    sym, len(times), max(max(0.0, now - t) for t in times))
    return matched


def reset_for_tests() -> None:
    _submits.clear()
    _fills.clear()
