"""Parse-level gates for ``.github/workflows/build.yml``.

Running the workflow in CI requires actually pushing to ``main``. This
test captures the invariants we care about — image names, GHCR registry,
sha + latest tags, cache configuration — so a refactor can't silently
break the registry publication contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yml"
)


@pytest.fixture(scope="module")
def wf() -> dict:
    assert WORKFLOW.exists(), f"missing {WORKFLOW}"
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_workflow_fires_on_main_push(wf: dict):
    # YAML `on:` is parsed as boolean True by PyYAML — the key becomes True.
    on = wf.get(True, wf.get("on"))
    assert "push" in on
    assert on["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in on


@pytest.mark.unit
def test_packages_write_permission(wf: dict):
    assert wf["permissions"]["packages"] == "write", (
        "GHCR push needs packages: write permission"
    )


@pytest.mark.unit
def test_both_images_are_built(wf: dict):
    jobs = wf["jobs"]
    assert "build-api" in jobs
    assert "build-frontend" in jobs


@pytest.mark.unit
def test_api_job_pushes_sha_and_latest_tags(wf: dict):
    steps = wf["jobs"]["build-api"]["steps"]
    push_step = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    tags = push_step["with"]["tags"]
    assert "fx-options-api:sha-${{ github.sha }}" in tags
    assert "fx-options-api:latest" in tags
    assert push_step["with"]["file"] == "infrastructure/docker/Dockerfile.api"
    # Push is gated on a manual run via env.PUSH (see
    # test_push_is_gated_on_manual_dispatch) so a flaky ghcr login never reds
    # a plain push-to-main; the tags contract above still holds.
    assert push_step["with"]["push"] == "${{ env.PUSH }}"


@pytest.mark.unit
def test_frontend_job_pushes_sha_and_latest_tags(wf: dict):
    steps = wf["jobs"]["build-frontend"]["steps"]
    push_step = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    tags = push_step["with"]["tags"]
    assert "fx-options-frontend:sha-${{ github.sha }}" in tags
    assert "fx-options-frontend:latest" in tags
    assert push_step["with"]["file"] == "infrastructure/docker/Dockerfile.web"


@pytest.mark.unit
def test_frontend_job_primes_npm_cache(wf: dict):
    """The setup-node step with `cache: npm` halves the cold build time."""
    steps = wf["jobs"]["build-frontend"]["steps"]
    node_step = next(s for s in steps if s.get("uses", "").startswith("actions/setup-node"))
    assert node_step["with"]["cache"] == "npm"
    assert node_step["with"]["cache-dependency-path"] == "frontend/package-lock.json"


@pytest.mark.unit
def test_both_jobs_log_in_to_ghcr(wf: dict):
    # Exact-match against the small set of accepted registry literals.
    # A substring check (e.g. `"ghcr.io" in registry`) would accept malicious
    # values like `evil.example.com/ghcr.io/foo` — flagged by CodeQL
    # py/incomplete-url-substring-sanitization.
    allowed_registries = {"ghcr.io", "${{ env.REGISTRY }}"}
    for job_name in ("build-api", "build-frontend"):
        steps = wf["jobs"][job_name]["steps"]
        login = next((s for s in steps if s.get("uses", "").startswith("docker/login-action")), None)
        assert login is not None, f"{job_name} must log in to GHCR before pushing"
        registry = login["with"]["registry"]
        assert registry in allowed_registries, (
            f"{job_name} must log in to GHCR (got {registry!r})"
        )


@pytest.mark.unit
def test_push_is_gated_on_manual_dispatch(wf: dict):
    """ghcr.io's token endpoint intermittently times out from the runner
    ("context deadline exceeded"), and the images aren't consumed by a live
    deploy — so login + push are gated on workflow_dispatch. A plain push to
    main builds every image (real signal) without touching the registry."""
    assert wf["env"]["PUSH"] == "${{ github.event_name == 'workflow_dispatch' }}"
    for job_name in ("build-api", "build-frontend", "build-engines"):
        steps = wf["jobs"][job_name]["steps"]
        login = next(s for s in steps if s.get("uses", "").startswith("docker/login-action"))
        assert login["if"] == "${{ github.event_name == 'workflow_dispatch' }}", (
            f"{job_name} must only log in to GHCR on a manual run"
        )
        push_step = next(
            s for s in steps if s.get("uses", "").startswith("docker/build-push-action")
        )
        assert push_step["with"]["push"] == "${{ env.PUSH }}"


@pytest.mark.unit
def test_buildx_cache_uses_distinct_scopes(wf: dict):
    """api and frontend must not share a buildx cache scope — their layer
    shapes differ and mixing the caches causes thrash."""
    api = next(
        s for s in wf["jobs"]["build-api"]["steps"]
        if s.get("uses", "").startswith("docker/build-push-action")
    )
    fe = next(
        s for s in wf["jobs"]["build-frontend"]["steps"]
        if s.get("uses", "").startswith("docker/build-push-action")
    )
    assert "scope=api" in api["with"]["cache-from"]
    assert "scope=frontend" in fe["with"]["cache-from"]
