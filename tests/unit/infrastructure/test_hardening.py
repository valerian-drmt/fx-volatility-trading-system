"""Container and workflow hardening, the half that runs offline (#298, #302).

Dockerfiles drop privileges, cAdvisor is not privileged, promtail has no
Docker socket (it goes through the filtering proxy), every action is pinned
to a commit sha, every workflow declares permissions, workflow inputs never
reach a ``run:`` block by interpolation, Dependabot covers the actions.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit


def _root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("repo root not found")


ROOT = _root()


def test_service_images_drop_to_a_non_root_user() -> None:
    for name in ("api.Dockerfile", "execution.Dockerfile"):
        text = (ROOT / "infrastructure" / "docker" / name).read_text(encoding="utf-8")
        user = [ln for ln in text.splitlines() if ln.startswith("USER ")]
        assert user == ["USER fxvol"], name
        assert text.index("USER fxvol") < text.index("CMD ["), f"{name}: USER must precede CMD"
        assert text.index("uv sync") < text.index("USER fxvol"), f"{name}: install as root, then drop"


def test_obs_profile_runs_without_privilege_or_a_writable_socket() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    svc = compose["services"]
    assert not svc["cadvisor"].get("privileged"), "cadvisor must not be privileged"
    assert all(v.endswith(":ro") for v in svc["cadvisor"]["volumes"]), svc["cadvisor"]["volumes"]
    assert not any("docker.sock" in v for v in svc["promtail"]["volumes"]), "promtail reads through the proxy"
    proxy = svc["docker-socket-proxy"]
    assert proxy["environment"]["CONTAINERS"] == "1" and proxy["environment"]["POST"] == "0"
    assert proxy["volumes"] == ["/var/run/docker.sock:/var/run/docker.sock:ro"]
    assert proxy["profiles"] == ["obs"] and proxy["networks"] == ["fxvol-internal"]
    assert "ports" not in proxy
    promtail_cfg = (ROOT / "obs" / "promtail.yml").read_text(encoding="utf-8")
    assert "tcp://docker-socket-proxy:2375" in promtail_cfg and "unix:///var/run/docker.sock" not in promtail_cfg
    assert svc["grafana"]["ports"] == ["127.0.0.1:3000:3000"], "Grafana stays loopback-only"


def test_deploy_refuses_dev_redis_password_and_grafana_without_a_password() -> None:
    deploy = (ROOT / "infrastructure" / "ec2" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert 'if [ -z "${REDIS_PASSWORD}" ]; then' in deploy
    assert "GRAFANA_ADMIN_PASSWORD" in deploy and "GF_AUTH_ANONYMOUS_ENABLED=false" in deploy


_USES = re.compile(r"^\s*uses:\s*(\S+)@(\S+)(\s+#\s*\S+)?\s*$", re.M)


def test_every_action_is_pinned_to_a_commit_sha() -> None:
    unpinned: list[str] = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for m in _USES.finditer(wf.read_text(encoding="utf-8")):
            action, ref, comment = m.group(1), m.group(2), m.group(3)
            if action.startswith("./"):
                continue
            if not re.fullmatch(r"[0-9a-f]{40}", ref) or not comment:
                unpinned.append(f"{wf.name}: {action}@{ref}")
    assert unpinned == [], unpinned


def test_every_workflow_declares_permissions_and_inputs_stay_out_of_run_blocks() -> None:
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        doc = yaml.safe_load(text)
        assert "permissions" in doc, f"{wf.name}: top-level permissions block missing"
        for job in doc["jobs"].values():
            for step in job.get("steps", []):
                run = step.get("run")
                if not run:
                    continue
                assert "${{ inputs." not in run and "${{ github.event." not in run, (
                    f"{wf.name}: pass workflow inputs through env, not into the script"
                )


def test_dependabot_covers_github_actions() -> None:
    cfg = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
    assert "github-actions" in ecosystems


def test_deploy_role_reads_command_results_in_one_region_only() -> None:
    import json

    policy = json.loads((ROOT / "infrastructure" / "aws" / "deploy-perms.json").read_text(encoding="utf-8"))
    by_sid = {s["Sid"]: s for s in policy["Statement"]}
    assert by_sid["ReadCommandResult"]["Condition"]["StringEquals"]["aws:RequestedRegion"] == "eu-west-1"
    assert by_sid["SendDeployCommand"]["Resource"] != "*"
