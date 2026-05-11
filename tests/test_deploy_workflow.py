"""Parse-level gates for the GHCR build workflow extensions introduced
in R8 PR #2.

R6 PR #6 shipped the initial ``build.yml`` that publishes
``fx-options-api`` and ``fx-options-frontend``. R8 adds four more
images — one per R7 engine container — via a matrix strategy. These
tests lock in the invariants we need for the later deploy.yml job
(PR #5) to pull and deploy reliable image names.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
)
CI_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
)

# Each entry : (matrix image name, expected Dockerfile path).
EXPECTED_ENGINE_IMAGES = {
    "market-data": "services/market_data/Dockerfile",
    "vol-engine": "services/vol/Dockerfile",
    "risk-engine": "services/risk/Dockerfile",
    "db-writer": "services/db_writer/Dockerfile",
}


@pytest.fixture(scope="module")
def wf() -> dict:
    assert WORKFLOW.exists(), f"missing {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_build_engines_job_exists(wf: dict):
    assert "build-engines" in wf["jobs"], (
        "R8 PR #2 must add a matrix job named build-engines"
    )


@pytest.mark.unit
def test_build_engines_matrix_has_four_services(wf: dict):
    matrix = wf["jobs"]["build-engines"]["strategy"]["matrix"]["service"]
    images = {s["image"] for s in matrix}
    assert images == set(EXPECTED_ENGINE_IMAGES), (
        f"matrix images must match {set(EXPECTED_ENGINE_IMAGES)}, got {images}"
    )


@pytest.mark.unit
def test_build_engines_matrix_points_at_the_right_dockerfiles(wf: dict):
    matrix = wf["jobs"]["build-engines"]["strategy"]["matrix"]["service"]
    by_image = {s["image"]: s["dockerfile"] for s in matrix}
    assert by_image == EXPECTED_ENGINE_IMAGES


@pytest.mark.unit
def test_build_engines_publishes_sha_and_latest_tags(wf: dict):
    """Every engine must ship both a sha-<commit> tag (immutable, for prod
    rollbacks) and a latest tag (convenience for dev pulls)."""
    steps = wf["jobs"]["build-engines"]["steps"]
    push = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    tags = push["with"]["tags"]
    assert "fx-options-${{ matrix.service.image }}:sha-${{ github.sha }}" in tags
    assert "fx-options-${{ matrix.service.image }}:latest" in tags


@pytest.mark.unit
def test_build_engines_uses_distinct_cache_scopes(wf: dict):
    """Each matrix leg must have its own buildx gha cache scope so the four
    parallel builds never overwrite each other."""
    steps = wf["jobs"]["build-engines"]["steps"]
    push = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    cache_from = push["with"]["cache-from"]
    cache_to = push["with"]["cache-to"]
    assert "scope=engines-${{ matrix.service.image }}" in cache_from
    assert "scope=engines-${{ matrix.service.image }}" in cache_to


@pytest.mark.unit
def test_build_engines_pushes_to_ghcr(wf: dict):
    steps = wf["jobs"]["build-engines"]["steps"]
    login = next((s for s in steps if s.get("uses", "").startswith("docker/login-action")), None)
    assert login is not None, "matrix job must log in to GHCR before pushing"
    assert login["with"]["registry"] == "${{ env.REGISTRY }}"
    push = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    assert push["with"]["push"] is True


@pytest.mark.unit
def test_build_engines_does_not_fail_fast(wf: dict):
    """A failure on one engine must NOT cancel the other three matrix legs —
    operators still need partial image publications to diagnose."""
    strategy = wf["jobs"]["build-engines"]["strategy"]
    assert strategy.get("fail-fast") is False


@pytest.mark.unit
def test_original_api_and_frontend_jobs_still_present(wf: dict):
    """R6 PR #6 invariants — the matrix addition must not break the two
    original jobs."""
    assert "build-api" in wf["jobs"]
    assert "build-frontend" in wf["jobs"]


@pytest.mark.unit
def test_workflow_permissions_allow_ghcr_push(wf: dict):
    assert wf["permissions"]["packages"] == "write"
    assert wf["permissions"]["contents"] == "read"


# ── R8 PR #3 : frontend pipeline extensions on ci.yml ────────────────────

@pytest.fixture(scope="module")
def ci_wf() -> dict:
    assert CI_WORKFLOW.exists(), f"missing {CI_WORKFLOW}"
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _frontend_steps(ci_wf: dict) -> list[dict]:
    return ci_wf["jobs"]["frontend"]["steps"]


def _step_names(steps: list[dict]) -> list[str]:
    return [s.get("name", "") for s in steps]


@pytest.mark.unit
def test_ci_runs_openapi_typescript_check(ci_wf: dict):
    """Frontend job must dump the live FastAPI schema and diff it against
    the committed schema.d.ts so any drift fails CI immediately."""
    names = _step_names(_frontend_steps(ci_wf))
    assert any("Dump live OpenAPI schema" in n for n in names)
    assert any("OpenAPI drift check" in n for n in names)


@pytest.mark.unit
def test_ci_runs_frontend_lint_and_typecheck(ci_wf: dict):
    names = _step_names(_frontend_steps(ci_wf))
    assert any("Lint" in n for n in names)
    assert any("Typecheck" in n for n in names)


@pytest.mark.unit
def test_ci_runs_vitest_with_coverage(ci_wf: dict):
    names = _step_names(_frontend_steps(ci_wf))
    assert any("coverage" in n.lower() for n in names), (
        "frontend job must run vitest with the coverage threshold"
    )


@pytest.mark.unit
def test_ci_uploads_frontend_dist_artifact(ci_wf: dict):
    steps = _frontend_steps(ci_wf)
    upload = next(
        (s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")),
        None,
    )
    assert upload is not None, "frontend job must upload frontend/dist as artefact"
    assert upload["with"]["path"] == "frontend/dist"
    assert "frontend" in upload["with"]["name"]


@pytest.mark.unit
def test_vitest_config_declares_coverage_threshold():
    """The 70% line threshold lives in vitest.config.ts so local
    ``npm run test:coverage`` enforces the same gate as CI."""
    vitest_cfg = (
        Path(__file__).resolve().parent.parent / "frontend" / "vitest.config.ts"
    ).read_text(encoding="utf-8")
    assert "thresholds" in vitest_cfg
    assert "lines: 70" in vitest_cfg or "lines:70" in vitest_cfg.replace(" ", "")


@pytest.mark.unit
def test_ci_openapi_contract_job_was_folded_into_frontend(ci_wf: dict):
    """R8 PR #3 merges openapi-contract into the frontend job — the
    standalone job must no longer exist to avoid duplicate work."""
    assert "openapi-contract" not in ci_wf["jobs"]
