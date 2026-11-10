"""Per-package coverage floors for the backend (#340, audit 07#13).

coverage.py only knows one global ``fail_under``; the floors here are per
package, read from ``[tool.fxvol.coverage-floors]`` in pyproject.toml, and
checked against the JSON report pytest-cov writes (``--cov-report=json``).

A package is a ``src/`` sub-path (``core``, ``engines/vol``, ``api/routers``):
its number is the line coverage of every file under it. Floors only ratchet
up: when a package beats its floor by more than ``RATCHET_HINT`` points the
script says so, and the next PR raises the floor.

    python scripts/ci/check_coverage.py coverage.json
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RATCHET_HINT = 3.0


def floors() -> dict[str, float]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data["tool"]["fxvol"]["coverage-floors"].items()}


def measured(report: dict, package: str) -> tuple[float, int]:
    covered = total = 0
    prefix = f"src/{package}/"
    for name, info in report["files"].items():
        path = name.replace("\\", "/")
        path = path[path.index("src/"):] if "src/" in path else path
        if path.startswith(prefix):
            covered += info["summary"]["covered_lines"]
            total += info["summary"]["num_statements"]
    return (100.0 * covered / total if total else 100.0), total


def main(argv: list[str]) -> int:
    report = json.loads(Path(argv[1] if len(argv) > 1 else "coverage.json").read_text(encoding="utf-8"))
    failures, hints = [], []
    print(f"{'package':<22} {'floor':>6} {'now':>7} {'lines':>6}")
    for package, floor in sorted(floors().items()):
        pct, lines = measured(report, package)
        if lines == 0:
            failures.append(f"{package}: no measured file under src/{package}/ (renamed? drop or move the floor)")
            continue
        mark = "FAIL" if pct < floor else ""
        print(f"{package:<22} {floor:6.1f} {pct:6.1f}% {lines:6d} {mark}")
        if pct < floor:
            failures.append(f"{package}: {pct:.1f}% < floor {floor:.1f}%")
        elif pct - floor > RATCHET_HINT:
            hints.append(f"{package}: {pct:.1f}% is {pct - floor:.1f} points over its floor; raise it")
    total = report["totals"]["percent_covered"]
    print(f"{'TOTAL':<22} {'':>6} {total:6.1f}%")
    for h in hints:
        print("  ratchet:", h)
    for f in failures:
        print("  BELOW FLOOR:", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
