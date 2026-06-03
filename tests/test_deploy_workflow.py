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
    "market-data": "src/engines/market_data/Dockerfile",
    "vol-engine": "src/engines/vol/Dockerfile",
    "risk-engine": "src/engines/risk/Dockerfile",
    "db-writer": "src/engines/db_writer/Dockerfile",
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


# ── R8 PR #4 : Playwright e2e against ephemeral docker-compose ──────────

def _e2e_compose_steps(ci_wf: dict) -> list[dict]:
    return ci_wf["jobs"]["frontend-e2e-compose"]["steps"]


@pytest.mark.unit
def test_ci_has_dedicated_compose_e2e_job(ci_wf: dict):
    assert "frontend-e2e-compose" in ci_wf["jobs"]


@pytest.mark.unit
def test_ci_runs_playwright_against_localhost_through_nginx(ci_wf: dict):
    env = ci_wf["jobs"]["frontend-e2e-compose"].get("env", {})
    assert env.get("PLAYWRIGHT_BASE_URL") == "http://localhost"


@pytest.mark.unit
def test_ci_compose_e2e_boots_full_stack(ci_wf: dict):
    steps = _e2e_compose_steps(ci_wf)
    run_cmds = [s.get("run", "") for s in steps if "run" in s]
    boot = any("docker compose up -d" in r for r in run_cmds)
    teardown = any("docker compose down" in r for r in run_cmds)
    assert boot, "compose up missing from e2e job"
    assert teardown, "compose down missing — stack would leak between runs"


@pytest.mark.unit
def test_ci_compose_e2e_applies_alembic_migrations(ci_wf: dict):
    run_cmds = [s.get("run", "") for s in _e2e_compose_steps(ci_wf) if "run" in s]
    assert any("alembic" in r and "upgrade head" in r for r in run_cmds)


@pytest.mark.unit
def test_ci_compose_e2e_uploads_report_on_failure(ci_wf: dict):
    steps = _e2e_compose_steps(ci_wf)
    upload = next(
        (s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")),
        None,
    )
    assert upload is not None
    assert upload.get("if") == "failure()"
    assert upload["with"]["name"] == "playwright-report-compose"


@pytest.mark.unit
def test_ib_stub_dockerfile_exists():
    dockerfile = Path(__file__).resolve().parent.parent / "infrastructure" / "docker" / "Dockerfile.ib-stub"
    assert dockerfile.exists(), "Dockerfile.ib-stub must ship for the CI stub IB service"
    content = dockerfile.read_text(encoding="utf-8")
    assert "EXPOSE 4002" in content


@pytest.mark.unit
def test_ib_stub_server_is_shipped():
    server = Path(__file__).resolve().parent.parent / "infrastructure" / "docker" / "ib-stub" / "server.py"
    assert server.exists()
    code = server.read_text(encoding="utf-8")
    # PORT/HOST sourced from env (IB_STUB_PORT/IB_STUB_BIND) since v1.9.0
    # hardening — Dockerfile.ib-stub sets them to 4002 / 0.0.0.0 for the
    # docker network. Source no longer carries the literal 0.0.0.0 (CodeQL
    # py/bind-socket-all-network-interfaces).
    assert "IB_STUB_PORT" in code and "4002" in code
    assert "IB_STUB_BIND" in code


# ── R8 PR #5 : deploy.yml workflow + EC2 provisioning scripts ───────────

DEPLOY_WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "deploy.yml"


@pytest.fixture(scope="module")
def deploy_wf() -> dict:
    assert DEPLOY_WF.exists(), f"missing {DEPLOY_WF}"
    return yaml.safe_load(DEPLOY_WF.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_deploy_yml_requires_tag_trigger(deploy_wf: dict):
    on = deploy_wf.get(True, deploy_wf.get("on"))
    assert "push" in on
    assert on["push"]["tags"] == ["v*.*.*"]


@pytest.mark.unit
def test_deploy_yml_supports_manual_dispatch_for_rollback(deploy_wf: dict):
    on = deploy_wf.get(True, deploy_wf.get("on"))
    assert "workflow_dispatch" in on
    assert "deploy_sha" in on["workflow_dispatch"]["inputs"]


@pytest.mark.unit
def test_deploy_uses_ghcr_images(deploy_wf: dict):
    steps = deploy_wf["jobs"]["deploy"]["steps"]
    run_cmds = " ".join(s.get("run", "") for s in steps if "run" in s)
    for image in (
        "fx-options-api",
        "fx-options-frontend",
        "fx-options-market-data",
        "fx-options-vol-engine",
        "fx-options-risk-engine",
        "fx-options-db-writer",
    ):
        assert f"/{image}:" in run_cmds, f"{image} not referenced in deploy.yml"
    assert "${{ env.REGISTRY }}" in run_cmds


@pytest.mark.unit
def test_deploy_applies_alembic_after_up(deploy_wf: dict):
    run_cmds = "\n".join(
        s.get("run", "") for s in deploy_wf["jobs"]["deploy"]["steps"] if "run" in s
    )
    assert "docker compose pull" in run_cmds
    assert "docker compose up -d" in run_cmds
    assert "upgrade head" in run_cmds
    up_pos = run_cmds.find("docker compose up -d")
    alembic_pos = run_cmds.find("upgrade head")
    assert up_pos < alembic_pos, "alembic must run after docker compose up"


@pytest.mark.unit
def test_deploy_has_post_deploy_smoke(deploy_wf: dict):
    names = [s.get("name", "") for s in deploy_wf["jobs"]["deploy"]["steps"]]
    assert any("Smoke" in n for n in names)


@pytest.mark.unit
def test_deploy_permissions_scope_is_minimal(deploy_wf: dict):
    perms = deploy_wf["permissions"]
    assert perms["contents"] == "read"
    assert perms["packages"] == "read"


@pytest.mark.unit
def test_deploy_targets_production_environment(deploy_wf: dict):
    """Using the `production` GHA environment forces reviewer approval
    when branch protection rules require it."""
    assert deploy_wf["jobs"]["deploy"]["environment"] == "production"


@pytest.mark.unit
def test_ec2_setup_script_exists():
    sh = Path(__file__).resolve().parent.parent / "infrastructure" / "ec2" / "setup.sh"
    assert sh.exists()
    body = sh.read_text(encoding="utf-8")
    assert "docker compose" in body
    assert "ufw" in body
    assert "certbot" in body


@pytest.mark.unit
def test_ec2_load_secrets_script_exists():
    sh = Path(__file__).resolve().parent.parent / "infrastructure" / "ec2" / "load_secrets.sh"
    assert sh.exists()
    body = sh.read_text(encoding="utf-8")
    assert "secretsmanager" in body


@pytest.mark.unit
def test_systemd_unit_exists_for_compose():
    unit = Path(__file__).resolve().parent.parent / "infrastructure" / "ec2" / "fxvol-compose.service"
    assert unit.exists()
    body = unit.read_text(encoding="utf-8")
    assert "[Unit]" in body
    assert "docker compose up -d" in body
    assert "WorkingDirectory=/opt/fxvol" in body


@pytest.mark.unit
def test_deployment_runbook_exists():
    runbook = Path(__file__).resolve().parent.parent / "docs" / "DEPLOYMENT.md"
    assert runbook.exists()
    body = runbook.read_text(encoding="utf-8")
    assert "Rollback" in body or "rollback" in body
    assert "deploy_sha" in body


# ── R8 PR #6 : CodeQL + Trivy security scans ────────────────────────────

CODEQL_WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "codeql.yml"
TRIVY_WF = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "security-scan.yml"


@pytest.fixture(scope="module")
def codeql_wf() -> dict:
    assert CODEQL_WF.exists(), f"missing {CODEQL_WF}"
    return yaml.safe_load(CODEQL_WF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trivy_wf() -> dict:
    assert TRIVY_WF.exists(), f"missing {TRIVY_WF}"
    return yaml.safe_load(TRIVY_WF.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_codeql_scans_python_and_javascript(codeql_wf: dict):
    matrix = codeql_wf["jobs"]["analyze"]["strategy"]["matrix"]["include"]
    languages = {leg["language"] for leg in matrix}
    assert "python" in languages
    assert "javascript-typescript" in languages


@pytest.mark.unit
def test_codeql_runs_on_pr_push_and_schedule(codeql_wf: dict):
    on = codeql_wf.get(True, codeql_wf.get("on"))
    assert "pull_request" in on
    assert "push" in on
    assert "schedule" in on


@pytest.mark.unit
def test_codeql_has_security_events_write_permission(codeql_wf: dict):
    assert codeql_wf["permissions"]["security-events"] == "write"


@pytest.mark.unit
def test_codeql_uses_security_extended_queries(codeql_wf: dict):
    steps = codeql_wf["jobs"]["analyze"]["steps"]
    init = next(s for s in steps if s.get("uses", "").startswith("github/codeql-action/init"))
    assert init["with"]["queries"] == "security-extended"


@pytest.mark.unit
def test_trivy_covers_all_six_production_images(trivy_wf: dict):
    matrix = trivy_wf["jobs"]["trivy"]["strategy"]["matrix"]["image"]
    expected = {
        "fx-options-api",
        "fx-options-frontend",
        "fx-options-market-data",
        "fx-options-vol-engine",
        "fx-options-risk-engine",
        "fx-options-db-writer",
    }
    assert set(matrix) == expected


@pytest.mark.unit
def test_trivy_fails_on_high_and_critical_only(trivy_wf: dict):
    steps = trivy_wf["jobs"]["trivy"]["steps"]
    scan = next(s for s in steps if s.get("uses", "").startswith("aquasecurity/trivy-action"))
    assert scan["with"]["severity"] == "HIGH,CRITICAL"
    assert scan["with"]["exit-code"] == "1"
    assert scan["with"]["ignore-unfixed"] is True


@pytest.mark.unit
def test_trivy_runs_weekly_plus_manual_dispatch(trivy_wf: dict):
    on = trivy_wf.get(True, trivy_wf.get("on"))
    assert "schedule" in on
    assert "workflow_dispatch" in on


@pytest.mark.unit
def test_trivy_uploads_sarif_even_on_failure(trivy_wf: dict):
    steps = trivy_wf["jobs"]["trivy"]["steps"]
    upload = next(
        (s for s in steps if s.get("uses", "").startswith("github/codeql-action/upload-sarif")),
        None,
    )
    assert upload is not None
    assert upload.get("if") == "always()"


@pytest.mark.unit
def test_trivy_does_not_fail_fast_between_images(trivy_wf: dict):
    """A failing image scan must NOT cancel the others — each finding
    should be independently visible in the Security tab."""
    assert trivy_wf["jobs"]["trivy"]["strategy"].get("fail-fast") is False


@pytest.mark.unit
def test_codeql_does_not_fail_fast_between_languages(codeql_wf: dict):
    assert codeql_wf["jobs"]["analyze"]["strategy"].get("fail-fast") is False


@pytest.mark.unit
def test_security_scan_workflow_has_minimal_permissions(trivy_wf: dict):
    perms = trivy_wf["permissions"]
    assert perms["contents"] == "read"
    assert perms["packages"] == "read"
    assert perms["security-events"] == "write"


# ── R8 PR #7 : post-deploy smoke + CHANGELOG v2.0.0 ─────────────────────

@pytest.mark.unit
def test_changelog_exists_and_pins_v1_9_0():
    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    assert changelog.exists(), "CHANGELOG.md must ship with R8 PR #7"
    body = changelog.read_text(encoding="utf-8")
    assert "[1.9.0]" in body
    assert "R8 release" in body
    assert "Removed" in body
    assert "app.py" in body


@pytest.mark.unit
def test_changelog_documents_breaking_change():
    body = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    assert "breaking change" in body.lower()


@pytest.mark.unit
def test_post_deploy_smoke_tests_exist_and_are_gated():
    smoke = Path(__file__).resolve().parent.parent / "tests" / "test_post_deploy_smoke.py"
    assert smoke.exists()
    body = smoke.read_text(encoding="utf-8")
    assert 'os.environ.get("PROD_SMOKE")' in body
    assert "/api/v1/health" in body
    assert "/api/v1/health/extended" in body
    assert "/api/openapi.json" in body
    assert "/ws/ticks" in body


@pytest.mark.unit
def test_post_deploy_smoke_checks_openapi_drift_against_frontend():
    smoke = (Path(__file__).resolve().parent.parent / "tests" / "test_post_deploy_smoke.py").read_text()
    assert "schema.d.ts" in smoke
    assert "live_paths" in smoke


@pytest.mark.unit
def test_post_deploy_smoke_validates_tls_certificate():
    smoke = (Path(__file__).resolve().parent.parent / "tests" / "test_post_deploy_smoke.py").read_text()
    assert "ssl.create_default_context" in smoke


@pytest.mark.unit
def test_ib_operations_doc_has_recovery_section():
    doc = (
        Path(__file__).resolve().parent.parent / "docs" / "IB_OPERATIONS.md"
    ).read_text(encoding="utf-8")
    assert "Recovery procedures" in doc
    # Five scenarios documented (the `Sc\u00e9nario` spelling uses é —
    # assert via unicode escape so the pytest output stays encoding-safe).
    for i in range(1, 6):
        assert f"Sc\u00e9nario {i}" in doc
