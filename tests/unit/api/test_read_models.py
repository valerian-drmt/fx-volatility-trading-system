"""Response models for the first read routes of #416, against payloads
recorded from prod on 2026-11-10 (tests/fixtures/api_reads/). Each payload
validates, and serialising through the model returns exactly the recorded
JSON: a model never drops or reshapes a field the desk reads."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.encoders import jsonable_encoder
from pydantic import TypeAdapter

from api.schemas.reads import (
    CycleProgress,
    DeltaHedgeSetting,
    ExitRuleConfig,
    HaltState,
    PcaSignalRow,
    RegimeSnapshotRow,
    RiskLimit,
)

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "api_reads"

CASES: list[tuple[str, Any]] = [
    ("trade_limits", dict[str, RiskLimit]),
    ("admin_halt", HaltState),
    ("exit_rules_config", list[ExitRuleConfig]),
    ("delta_hedge_config", list[DeltaHedgeSetting]),
    ("regime_history", list[RegimeSnapshotRow]),
    ("pca_history", list[PcaSignalRow]),
    ("cycle_progress", CycleProgress),
]


@pytest.mark.parametrize(("name", "model"), CASES, ids=[c[0] for c in CASES])
def test_recorded_payload_round_trips_unchanged(name: str, model: Any) -> None:
    recorded = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    adapter = TypeAdapter(model)
    parsed = adapter.validate_python(recorded)
    out = json.loads(json.dumps(jsonable_encoder(adapter.dump_python(parsed, mode="json"))))
    assert out == recorded


def test_the_routes_declare_the_models() -> None:
    from api.main import create_app

    spec = create_app().openapi()
    for path in ("/api/v1/trade/limits", "/api/v1/admin/halt", "/api/v1/positions/exit-rules-config",
                 "/api/v1/positions/delta-hedge-config", "/api/v1/regime/history",
                 "/api/v1/signals/pca/history", "/api/v1/dev/cycle-progress"):
        schema = json.dumps(spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"])
        assert "$ref" in schema, path
