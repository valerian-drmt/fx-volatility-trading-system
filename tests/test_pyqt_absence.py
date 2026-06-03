"""R8 PR #1 guard : no Python file in the repo imports PyQt5 / pyqtgraph.

The desktop stack was removed with R8 PR #1. A reintroduction — intentional
or from a bad rebase — would silently pull the PyQt5 wheel back on dev
machines and CI. This test walks every ``.py`` file under the source
directories, parses it with ``ast``, and fails on the first forbidden
import.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Scan these roots — everything else (notebooks, release docs, build
# artefacts) is out of scope.
SCAN_ROOTS = ("src", "tests")

FORBIDDEN_TOP_LEVEL_NAMES = {"PyQt5", "pyqtgraph"}


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        out.extend(base.rglob("*.py"))
    return out


def _forbidden_import(node: ast.AST) -> str | None:
    """Return the offending module name if ``node`` imports a banned package."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in FORBIDDEN_TOP_LEVEL_NAMES:
                return alias.name
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        top = module.split(".")[0]
        if top in FORBIDDEN_TOP_LEVEL_NAMES:
            return module
    return None


@pytest.mark.unit
def test_no_pyqt_or_pyqtgraph_imports_in_source_tree():
    offenders: list[tuple[Path, int, str]] = []
    for path in _iter_py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue  # fixture files / broken examples left by tests
        for node in ast.walk(tree):
            found = _forbidden_import(node)
            if found:
                offenders.append((path.relative_to(REPO_ROOT), getattr(node, "lineno", 0), found))

    assert not offenders, (
        "PyQt/pyqtgraph were removed in R8 PR #1 — forbidden imports re-appeared :\n"
        + "\n".join(f"  {p}:{line}  → {mod}" for p, line, mod in offenders)
    )


@pytest.mark.unit
def test_requirements_does_not_ship_pyqt_or_pyqtgraph():
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    for banned in ("PyQt5", "pyqt5", "pyqtgraph"):
        # Accept occurrences inside comments (the README/context lines
        # explaining the removal) but not as actual pip dependencies.
        for line in req.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert banned not in stripped, f"{banned} still listed as a dep: {line!r}"


@pytest.mark.unit
def test_app_py_has_been_removed():
    assert not (REPO_ROOT / "app.py").exists(), (
        "app.py must not come back — R8 replaced it with the FastAPI + frontend stack"
    )


@pytest.mark.unit
def test_controller_modules_have_been_removed():
    for gone in ("src/controller.py", "src/controller_settings.py"):
        assert not (REPO_ROOT / gone).exists(), (
            f"{gone} was removed in R8 PR #1 — engines live in their own containers now"
        )


@pytest.mark.unit
def test_ui_package_has_been_removed():
    assert not (REPO_ROOT / "src" / "ui").exists(), (
        "src/ui/ was removed in R8 PR #1 — the React frontend is the only UI layer"
    )
