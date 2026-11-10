"""One version source (#353): pyproject's PEP 440 version, rendered as SemVer
for tags, images and npm, and the matching CHANGELOG section for the release.

    python scripts/ci/release_version.py semver          # 1.0.0rc1 -> 1.0.0-rc.1
    python scripts/ci/release_version.py notes 1.0.0-rc.1
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def pep440() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def to_semver(version: str) -> str:
    m = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", version)
    if not m:
        raise SystemExit(f"unsupported version {version!r}: use X.Y.Z or X.Y.Z(a|b|rc)N")
    base, kind, n = m.groups()
    if not kind:
        return base
    return f"{base}-{ {'a': 'alpha', 'b': 'beta', 'rc': 'rc'}[kind] }.{n}"


def notes(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for [{version}]")
    return m.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "semver":
        print(to_semver(pep440()))
    elif len(argv) >= 3 and argv[1] == "notes":
        print(notes(argv[2]), end="")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
