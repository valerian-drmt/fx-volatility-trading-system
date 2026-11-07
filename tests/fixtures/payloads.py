"""Canonical payloads: one per Redis channel and per IB event (#338, 07#10).

``payload("ticks")`` returns a fresh dict loaded from
``tests/fixtures/payloads/<name>.json``. The files are the SHAPES the
publishers emit (``bus.publisher``) and the engines consume; a test that
changes a shape must change the fixture, which is the point: the file is the
contract between producer and consumer tests.

Redis channels  : ticks, account, vol_update, risk_update, positions,
                  orders (orders:<structure_id>), exit_alerts, db_events
IB events       : ib_order_status, ib_execution, ib_commission_report, ib_error
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent / "payloads"
NAMES: tuple[str, ...] = (
    "ticks", "account", "vol_update", "risk_update", "positions", "orders", "exit_alerts", "db_events",
    "ib_order_status", "ib_execution", "ib_commission_report", "ib_error",
)
_CACHE: dict[str, dict[str, Any]] = {}


def payload(name: str) -> dict[str, Any]:
    if name not in NAMES:
        raise KeyError(f"no canonical payload {name!r}; known: {', '.join(NAMES)}")
    if name not in _CACHE:
        _CACHE[name] = json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(_CACHE[name])
