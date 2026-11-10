"""Deploy pipeline safety (#345, audit 08#6 / 08#7 / 08#16).

prune_images.sh runs against a fake `docker` on PATH: it keeps our images at the
current and previous tag, removes older tags and unused infra images, and
leaves running images alone. The rollback gate and the host patch policy are
checked in the scripts and the workflow."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PRUNE = ROOT / "infrastructure/ec2/prune_images.sh"
BASH = shutil.which("bash")
LF = "\n"

FAKE_DOCKER = """#!/usr/bin/env bash
# images: one ref per line in $FAKE/images ; refs in $FAKE/in_use refuse rmi
case "$1" in
  images) cat "$FAKE/images" ;;
  rmi) if grep -qxF "$2" "$FAKE/in_use"; then exit 1; fi; echo "$2" >> "$FAKE/removed" ;;
  image|builder) exit 0 ;;
esac
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline=LF)


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_prune_keeps_current_and_previous_release(tmp_path: Path) -> None:
    fake = tmp_path / "fake"
    fake.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    _write(state / "current_tag", "sha-ccc" + LF)
    _write(state / "previous_tag", "sha-bbb" + LF)
    reg = "ghcr.io/owner"
    images = [
        f"{reg}/fx-options-api:sha-ccc", f"{reg}/fx-options-vol-engine:sha-ccc",
        f"{reg}/fx-options-api:sha-bbb", f"{reg}/fx-options-vol-engine:sha-bbb",
        f"{reg}/fx-options-api:sha-aaa", f"{reg}/fx-options-vol-engine:sha-aaa",
        "postgres:16.15-alpine3.24", "postgres:16.14-alpine3.23", "redis:7.4.12-alpine3.22",
    ]
    _write(fake / "images", LF.join(images) + LF)
    _write(fake / "in_use", LF.join(["postgres:16.15-alpine3.24", "redis:7.4.12-alpine3.22"]) + LF)
    _write(fake / "removed", "")
    docker = fake / "docker"
    _write(docker, FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)

    script = PRUNE.read_text(encoding="utf-8").replace('STATE="/opt/fxvol/state"', f'STATE="{state.as_posix()}"')
    run = tmp_path / "prune.sh"
    _write(run, script)
    env = {**os.environ, "FAKE": fake.as_posix(), "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    out = subprocess.run([BASH, run.as_posix()], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr

    removed = set((fake / "removed").read_text(encoding="utf-8").split())
    assert removed == {f"{reg}/fx-options-api:sha-aaa", f"{reg}/fx-options-vol-engine:sha-aaa", "postgres:16.14-alpine3.23"}
    assert "kept 4 rollback image(s)" in out.stdout


def test_deploy_records_the_release_pair_and_uses_the_keeping_prune() -> None:
    deploy = (ROOT / "infrastructure/ec2/remote-deploy.sh").read_text(encoding="utf-8")
    assert "docker image prune -af" not in deploy, "the blanket prune removed the rollback target"
    assert deploy.index('echo "$prev_tag" > /opt/fxvol/state/previous_tag') < deploy.index("prune_images.sh ||")
    setup = (ROOT / "infrastructure/ec2/setup.sh").read_text(encoding="utf-8")
    assert 'system prune -af --filter "until=168h"' not in setup and "prune_images.sh" in setup


def test_rollback_past_a_migration_is_gated_and_runs_after_the_dump() -> None:
    deploy = (ROOT / "infrastructure/ec2/remote-deploy.sh").read_text(encoding="utf-8")
    dump = deploy.index('infrastructure/ec2/postgres_backup.sh "pre-${IMAGE_TAG}"')
    gate = deploy.index('if [ "${ALLOW_SCHEMA_DOWNGRADE:-false}" != "true" ]; then')
    downgrade = deploy.index('alembic_in "$running_image" downgrade "$target_head"')
    upgrade = deploy.index("python -m alembic -c src/persistence/alembic.ini upgrade head || mig_rc=$?")
    assert dump < gate < downgrade < upgrade
    wf = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "allow_schema_downgrade:" in wf and "default: false" in wf
    assert "ALLOW_SCHEMA_DOWNGRADE=" in wf


def test_host_takes_security_updates_without_touching_docker_or_rebooting() -> None:
    setup = (ROOT / "infrastructure/ec2/setup.sh").read_text(encoding="utf-8")
    assert "unattended-upgrades" in setup
    assert '"${distro_id}:${distro_codename}-security";' in setup
    assert 'Unattended-Upgrade::Automatic-Reboot "false";' in setup
    for pkg in ("docker-ce", "containerd.io"):
        assert f'"{pkg}";' in setup
