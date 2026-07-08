"""Unit tests for scripts.db.seed_regime_lookup.expand_patterns()."""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ is not on PYTHONPATH ; add the repo root manually.
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.db.seed_regime_lookup import (  # noqa: E402
    BASE_PATTERNS,
    expand_patterns,
)


def test_15_base_patterns_present():
    assert len(BASE_PATTERNS) == 15
    ids = sorted({p[1] for p in BASE_PATTERNS})
    assert ids == list(range(1, 16))


def test_expand_patterns_contains_all_5bucket_variants():
    rows = expand_patterns()
    patterns = {r["pattern"] for r in rows}
    # (-,0,+) base → 4 variants : ("--","-") × ("0",) × ("+","++")
    expected_from_first = {"(--,0,+)", "(--,0,++)", "(-,0,+)", "(-,0,++)"}
    assert expected_from_first <= patterns
    # ++ on a "+" bucket : (+,+,+) → also (++,++,++)
    assert "(++,++,++)" in patterns
    # Pure-zero base (0,0,0) → exactly one variant
    assert "(0,0,0)" in patterns
    # Fallback row always present
    assert "unmapped_extreme" in patterns


def test_intensity_count_is_number_of_extreme_buckets():
    rows = {r["pattern"]: r for r in expand_patterns()}
    assert rows["(0,0,0)"]["intensity_count"] == 0
    assert rows["(--,0,0)"]["intensity_count"] == 1
    assert rows["(++,++,0)"]["intensity_count"] == 2
    assert rows["(++,++,++)"]["intensity_count"] == 3


def test_no_duplicate_patterns():
    rows = expand_patterns()
    patterns = [r["pattern"] for r in rows]
    assert len(patterns) == len(set(patterns))


def test_calm_baseline_maps_correctly():
    rows = {r["pattern"]: r for r in expand_patterns()}
    assert rows["(-,0,0)"]["regime_id"] == 2
    assert rows["(-,0,0)"]["regime_name"] == "calm_baseline"
    # Same regime id propagates to the intensified variant
    assert rows["(--,0,0)"]["regime_id"] == 2
    assert rows["(--,0,0)"]["intensity_count"] == 1
