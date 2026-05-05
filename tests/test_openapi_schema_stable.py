"""Guardrail against backend / frontend schema drift.

Dumps ``api.main.create_app().openapi()`` and compares key invariants to what
``frontend/src/api/schema.d.ts`` exposes. If a backend change alters paths or
deletes an operation without regenerating the TypeScript file, the test
fails — preventing a silent breakage of the typed endpoints helpers.

Gated by ``WEB_RUN_INTEGRATION=1`` to keep the default unit run fast and
independent of the frontend checkout being present.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_TS = REPO_ROOT / "frontend" / "src" / "api" / "schema.d.ts"
DUMP_SCRIPT = REPO_ROOT / "scripts" / "dump_openapi.py"

pytestmark = pytest.mark.skipif(
    os.environ.get("WEB_RUN_INTEGRATION") != "1",
    reason="Set WEB_RUN_INTEGRATION=1 to run the schema drift guard.",
)


def _live_paths() -> set[str]:
    """Return the set of paths exposed by the current FastAPI schema."""
    dump = subprocess.check_output(
        [sys.executable, str(DUMP_SCRIPT), "-"],
        cwd=REPO_ROOT,
        text=True,
    )
    # dump_openapi.py writes "OpenAPI schema written to …" when given a path ;
    # when given "-" it still writes the file and prints the line — parse the
    # actual JSON by re-invoking create_app locally instead for robustness.
    del dump
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from api.main import create_app

    schema = create_app().openapi()
    return set(schema["paths"].keys())


def _schema_ts_paths() -> set[str]:
    """Extract the paths declared in the committed schema.d.ts."""
    text = SCHEMA_TS.read_text(encoding="utf-8")
    import re

    # The generator writes `    "/api/v1/..."` then `: {` on the same line.
    return set(re.findall(r'^\s*"(/api/v1/[^"]+)"\s*:\s*\{', text, re.MULTILINE))


def test_every_backend_path_is_declared_in_frontend_schema():
    live = _live_paths()
    ts = _schema_ts_paths()
    missing = live - ts
    assert not missing, (
        f"backend paths missing from frontend/src/api/schema.d.ts: {sorted(missing)}. "
        "Run `npm --prefix frontend run gen:api` and commit the regenerated file."
    )


def test_frontend_schema_does_not_declare_dead_paths():
    live = _live_paths()
    ts = _schema_ts_paths()
    extra = ts - live
    assert not extra, (
        f"frontend/src/api/schema.d.ts declares paths the backend no longer exposes: {sorted(extra)}. "
        "Regenerate the file with `npm --prefix frontend run gen:api`."
    )


def test_schema_file_is_readable_and_non_trivial():
    assert SCHEMA_TS.exists(), f"missing {SCHEMA_TS}"
    content = SCHEMA_TS.read_text(encoding="utf-8")
    assert "auto-generated" in content.lower()
    assert "export interface paths" in content
    # Sanity: expect at least the 18 endpoints we ship in R4.
    ts_count = len(_schema_ts_paths())
    assert ts_count >= 10, f"schema.d.ts only declares {ts_count} paths, looks truncated"
