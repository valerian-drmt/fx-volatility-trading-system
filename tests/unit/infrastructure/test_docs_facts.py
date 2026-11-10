"""The counts the README and the architecture docs state match the code (#350,
audit 11#1 / 06#14): ORM classes, engines, built images, routers, migrations.
A new engine or table that forgets the docs fails here."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _facts() -> dict[str, int]:
    from persistence.models import Base

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    engines = [k for k, v in services.items() if "engines" in (v.get("profiles") or [])]
    core = [k for k, v in services.items() if not v.get("profiles")]
    ib = [k for k, v in services.items() if "ib" in (v.get("profiles") or [])]
    obs = [k for k, v in services.items() if "obs" in (v.get("profiles") or [])]
    build = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
    images = set(re.findall(r"fx-options-([a-z-]+):sha-", build)) | set(re.findall(r"image: ([a-z-]+)\n\s+dockerfile:", build))
    routers = [p for p in (ROOT / "src/api/routers").glob("*.py") if p.name != "__init__.py"]
    migrations = [p for p in (ROOT / "src/persistence/migrations/versions").glob("*.py") if p.name != "__init__.py"]
    return {
        "orm": len(Base.registry.mappers),
        "engines": len(engines),
        "core": len(core) + len(engines) + len(ib),
        "obs": len(obs),
        "images": len(images - {"${{"}),
        "routers": len(routers),
        "migrations": len(migrations),
    }


def test_readme_counts_match_the_code() -> None:
    f = _facts()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"**{f['core']}-container core stack**" in readme, f
    assert f"optional {f['obs']}-container" in readme, f
    assert f"The {f['engines']} Python engines live behind" in readme, f
    assert f"({f['images']} images → GHCR" in readme, f
    assert f"SQLAlchemy 2 ORM ({f['orm']} classes)" in readme, f
    assert f"main, {f['routers']} routers" in readme, f
    assert f"({f['migrations']} revisions)" in readme, f


def test_architecture_docs_agree() -> None:
    f = _facts()
    overview = (ROOT / "docs/architecture/overview.md").read_text(encoding="utf-8")
    assert f"**core stack is {f['core']} containers**" in overview, f
    assert f"**{f['obs']}-container observability stack**" in overview, f
    backend = (ROOT / "docs/architecture/backend.md").read_text(encoding="utf-8")
    assert f"`models.py` ({f['orm']} ORM classes)" in backend and f"{f['routers']} routers" in backend, f
    for doc in ("README.md", "docs/README.md", "docs/architecture/backend.md", "docs/architecture/overview.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        for stale in ("scripts/ops/", "scripts/up.ps1", "vol_trading_pca", "ib-gateway:latest"):
            assert stale not in text, (doc, stale)


def test_docs_index_has_no_dead_links() -> None:
    index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    dead = [link for link in re.findall(r"\]\(([^)#]+\.md)\)", index) if not (ROOT / "docs" / link).exists()]
    assert dead == [], dead
