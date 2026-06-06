"""PC × signal-label → structure recommandée — canonical lookup.

Previously mirrored as the ``pca_structure_recommendation`` DB table
(seeded by alembic 011 from the same 6 rows below, never recalibrated).
Migration 039 dropped the table ; this module is now the single source
of truth. vol-engine and the ``/api/v1/signals/pca/state`` endpoint
both read from here.

The mapping is **product knowledge** (which structure trades cleanly
on which factor's extreme), not ops-tunable noise. Changing it should
go through a PR with a rationale.
"""
from __future__ import annotations

from typing import TypedDict


class PcaRecommendation(TypedDict):
    recommended_structure: str   # canonical structure_type key in core.trade_preview.TEMPLATES
    default_tenor: str           # display tenor, e.g. "3M", "1M_3M" for calendars
    description: str             # short label for the UI card
    rationale: str               # 1-line trader-friendly why


# (pc_id, signal_label) → recommendation
#
# pc_id ∈ {1, 2, 3} (first three principal components)
# signal_label ∈ {"CHEAP", "EXPENSIVE"}  (FAIR is never actionable)
PCA_RECOMMENDATIONS: dict[tuple[int, str], PcaRecommendation] = {
    (1, "CHEAP"): {
        "recommended_structure": "straddle_atm",
        "default_tenor": "3M",
        "description": "Long straddle ATM",
        "rationale": "PC1 CHEAP = vol level low → buy vol via ATM straddle",
    },
    (1, "EXPENSIVE"): {
        "recommended_structure": "short_strangle",
        "default_tenor": "3M",
        "description": "Short OTM strangle",
        "rationale": "PC1 EXPENSIVE = vol level high → sell vol via OTM strangle",
    },
    (2, "CHEAP"): {
        "recommended_structure": "calendar_long",
        "default_tenor": "1M_3M",
        "description": "Calendar buying long tenor",
        "rationale": "PC2 CHEAP = term slope inverted → buy long tenor",
    },
    (2, "EXPENSIVE"): {
        "recommended_structure": "calendar_short",
        "default_tenor": "1M_3M",
        "description": "Calendar selling long tenor",
        "rationale": "PC2 EXPENSIVE = term slope steep → sell long tenor",
    },
    (3, "CHEAP"): {
        "recommended_structure": "long_butterfly_25d",
        "default_tenor": "3M",
        "description": "Long 25d butterfly",
        "rationale": "PC3 CHEAP = wings cheap relative to ATM",
    },
    (3, "EXPENSIVE"): {
        "recommended_structure": "short_butterfly_25d",
        "default_tenor": "3M",
        "description": "Short 25d butterfly",
        "rationale": "PC3 EXPENSIVE = wings rich relative to ATM",
    },
}


def lookup_recommendation(
    pc_id: int, signal_label: str,
) -> PcaRecommendation | None:
    """Return the recommendation row for ``(pc_id, signal_label)`` or
    ``None`` if no actionable recommendation exists (e.g. FAIR labels,
    or PCs beyond #3 — the loadings exist but no trade-driving
    semantics have been established for them)."""
    return PCA_RECOMMENDATIONS.get((pc_id, signal_label.upper()))


def recommendation_label(pc_id: int, signal_label: str) -> str | None:
    """``"<structure>_<tenor>"`` shorthand used by the vol-engine when
    it stamps ``pca_signal_history.recommended_structure``. Returns
    ``None`` for non-actionable labels."""
    rec = lookup_recommendation(pc_id, signal_label)
    if rec is None:
        return None
    return f"{rec['recommended_structure']}_{rec['default_tenor']}"
