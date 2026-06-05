"""Sanity test : the P1.0 layout is in place.

Pinned in ``tests/unit/`` so pytest collects at least one test until the
downstream Waves (P1.1 → P1.6) populate the buckets with real feature
tests. Without this, pytest exits 5 (no tests collected) and CI rouges.
"""
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]


def test_top_level_buckets_exist() -> None:
    for bucket in ("unit", "integration", "old", "fixtures"):
        assert (_TESTS_ROOT / bucket).is_dir(), f"tests/{bucket}/ missing"


def test_structure_doc_present() -> None:
    assert (_TESTS_ROOT / "STRUCTURE.md").is_file()


def test_old_quarantine_has_legacy_tests() -> None:
    # 29 flat + the 2 + 4 from services/shared/integration → tests/old/
    # holds the historical test corpus until each is rewritten in unit/.
    moved = list((_TESTS_ROOT / "old").rglob("test_*.py"))
    assert len(moved) >= 20, f"tests/old/ unexpectedly thin : {len(moved)} files"
