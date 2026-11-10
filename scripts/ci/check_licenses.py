"""License gate for the Python set (#304, audit 10#11).

Reads the metadata of every distribution installed in the current
environment (CI: ``uv sync --locked --all-extras``) and fails when one is
copyleft or unidentified, unless it is listed in ``EXCEPTIONS`` with the
reason. No third-party dependency: ``importlib.metadata`` only. The policy
and the triage log live in ``docs/ops/dependency-policy.md``.

    python scripts/ci/check_licenses.py            # table + exit 1 on a violation
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys

ALLOWED = (
    "MIT", "BSD", "Apache", "PSF", "Python Software Foundation", "ISC", "Zlib",
    "0BSD", "CC0", "NCSA", "Unlicense", "MPL", "Mozilla Public License",
)
COPYLEFT = re.compile(r"\b(A?GPL|LGPL|GNU)\b", re.I)
EXCEPTIONS = {
    # LGPL-3.0 with the OpenSSL / linking exception; imported, never modified
    # or vendored; db-writer only (the async path is asyncpg, BSD).
    "psycopg2-binary": "LGPL with linking exception, dynamically linked, db-writer only",
}
# Tooling that is not shipped in any image or wheel of ours.
DEV_ONLY = {"pip-licenses", "pip-audit"}


def license_of(dist: md.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers)
    raw = meta.get("License") or ""
    return raw.splitlines()[0][:60] if raw else ""


def main() -> int:
    rows: list[tuple[str, str, str, str]] = []
    violations: list[str] = []
    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        lic = license_of(dist)
        if name in EXCEPTIONS:
            verdict = "exception"
        elif COPYLEFT.search(lic):
            verdict = "COPYLEFT"
        elif any(tok.lower() in lic.lower() for tok in ALLOWED):
            verdict = "ok"
        elif not lic:
            verdict = "UNKNOWN"
        else:
            verdict = "UNLISTED"
        if verdict in ("COPYLEFT", "UNKNOWN", "UNLISTED"):
            violations.append(f"{name} {dist.version}: {lic or '(no license metadata)'} [{verdict}]")
        rows.append((name, dist.version, lic or "-", verdict + (f" ({EXCEPTIONS[name]})" if name in EXCEPTIONS else "")))
    width = max(len(r[0]) for r in rows)
    for name, version, lic, verdict in rows:
        print(f"{name:<{width}}  {version:<14} {verdict:<10} {lic}")
    print(f"\n{len(rows)} distributions, {len(violations)} violation(s)")
    for v in violations:
        print("  VIOLATION:", v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
