"""Release mechanics (#353): one version source and a release workflow that
re-tags, never rebuilds."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "scripts" / "ci" / "release_version.py"


def _run(*args: str) -> str:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, check=True, timeout=30).stdout.strip()


def test_one_version_everywhere() -> None:
    pep = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    semver = _run("semver")
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    assert pkg == semver, (pep, semver, pkg)
    from api.main import create_app

    assert create_app().version == pep
    main = (ROOT / "src" / "api" / "main.py").read_text(encoding="utf-8")
    assert 'version="0.4.0"' not in main


def test_changelog_has_the_current_version() -> None:
    notes = _run("notes", _run("semver"))
    assert "### " in notes


def test_release_workflow_retags_and_never_rebuilds() -> None:
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in wf
    assert "docker buildx imagetools create" in wf and "build-push-action" not in wf
    assert "git merge-base --is-ancestor" in wf
    build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    assert build.count("org.opencontainers.image.version=${{ steps.ver.outputs.semver }}") == 3
