"""Supply-chain pins (#303, #305, audit 10#5 / 10#6): every base image is an
exact tag with a digest and the same everywhere; every runtime dependency has
a ceiling and uv.lock is committed; CI installs from the lock; Node is 22."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PY_DOCKERFILES = [
    ROOT / "infrastructure/docker/api.Dockerfile",
    ROOT / "infrastructure/docker/execution.Dockerfile",
    *(ROOT / "src/engines" / e / "Dockerfile" for e in ("analytics", "db_writer", "market_data", "risk", "vol")),
]
PINNED = re.compile(r"^[\w./-]+(?::[\w.-]+)?@sha256:[0-9a-f]{64}$")
FLOATING = ("latest", "alpine", "slim")


def _args(text: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+_IMAGE)=(\S+)$", text, re.M))


def test_python_dockerfiles_share_one_pinned_python_and_uv_image() -> None:
    seen = {}
    for path in PY_DOCKERFILES:
        text = path.read_text(encoding="utf-8")
        args = _args(text)
        assert PINNED.match(args["PYTHON_IMAGE"]) and args["PYTHON_IMAGE"].startswith("python:3.11."), path.name
        assert PINNED.match(args["UV_IMAGE"]) and args["UV_IMAGE"].startswith("ghcr.io/astral-sh/uv:"), path.name
        seen.setdefault("py", set()).add(args["PYTHON_IMAGE"])
        seen.setdefault("uv", set()).add(args["UV_IMAGE"])
        assert "uv sync --locked --no-dev" in text and "pip install" not in text, path.name
        assert "COPY pyproject.toml uv.lock README.md ./" in text, path.name
    assert len(seen["py"]) == 1 and len(seen["uv"]) == 1, "one Python / uv build everywhere"


def test_web_dockerfile_pins_node_22_and_nginx() -> None:
    args = _args((ROOT / "infrastructure/docker/web.Dockerfile").read_text(encoding="utf-8"))
    assert PINNED.match(args["NODE_IMAGE"]) and args["NODE_IMAGE"].startswith("node:22.")
    assert PINNED.match(args["NGINX_IMAGE"]) and args["NGINX_IMAGE"].startswith("nginx:1.")


def test_compose_infra_images_are_exact_and_match_the_dockerfiles() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    images = re.findall(r"^\s+image: (\S+)$", compose, re.M)
    infra = [i for i in images if not i.startswith("${")]
    for image in infra:
        name, _, tag = image.partition(":")
        assert tag, f"{image}: no tag"
        assert tag.split("@")[0] not in FLOATING, f"{image}: floating tag"
    for prefix in ("postgres:", "redis:", "nginx:"):
        pinned = [i for i in infra if i.startswith(prefix)]
        assert pinned and all(PINNED.match(i) for i in pinned), f"{prefix} must carry a digest"
    web_nginx = _args((ROOT / "infrastructure/docker/web.Dockerfile").read_text(encoding="utf-8"))["NGINX_IMAGE"]
    assert web_nginx in infra, "the edge nginx and the frontend's nginx are the same build"
    gateway = re.search(r"IB_GATEWAY_IMAGE:-(\S+)\}", compose).group(1)
    assert PINNED.match(gateway) and "10.49.1c" in gateway
    deploy = (ROOT / "infrastructure/ec2/remote-deploy.sh").read_text(encoding="utf-8")
    assert f"IB_GATEWAY_IMAGE={gateway}" in deploy, "compose default and remote-deploy pin the same gateway build"


def test_every_runtime_dependency_has_a_ceiling_and_the_lock_is_committed() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    specs = list(data["project"]["dependencies"])
    for extra, deps in data["project"]["optional-dependencies"].items():
        if extra != "dev":
            specs += deps
    # tzdata is calendar-versioned data, not code: a ceiling would only block the next year.
    unbounded = [s for s in specs if not s.startswith(("fxvol[", "tzdata")) and not re.search(r"<|==|~=", s)]
    assert unbounded == [], f"runtime dependencies without a ceiling: {unbounded}"
    assert (ROOT / "uv.lock").is_file()
    assert "ib-insync==0.9.86" in specs, "exact pin until the ib_async migration (#306)"


def test_ci_installs_from_the_lock_on_node_22() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip install -e" not in ci
    assert ci.count("uv sync --locked") >= 4 and "uv lock --check" in ci
    assert "hashFiles('uv.lock')" in ci and "hashFiles('pyproject.toml')" not in ci
    for wf in ("ci.yml", "build.yml", "codeql.yml"):
        text = (ROOT / ".github/workflows" / wf).read_text(encoding="utf-8")
        assert 'node-version: "20"' not in text, wf
    pkg = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
    assert '"license": "MIT"' in pkg and '"node": ">=22' in pkg


def test_dependabot_covers_every_pin_source() -> None:
    cfg = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    for eco in ("github-actions", "docker", "docker-compose", "uv", "npm"):
        assert f"package-ecosystem: {eco}" in cfg, eco
    for d in ("/infrastructure/docker", "/src/engines/vol", "/frontend"):
        assert d in cfg, d


def test_dependabot_never_proposes_a_datastore_major() -> None:
    """A Postgres major bump cannot reuse the data volume (#408 was one)."""
    import yaml

    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
    compose = next(u for u in cfg["updates"] if u["package-ecosystem"] == "docker-compose")
    ignored = {(i["dependency-name"], tuple(i.get("update-types", []))) for i in compose["ignore"]}
    for name in ("postgres", "redis"):
        assert (name, ("version-update:semver-major",)) in ignored, name
