"""Cross-sectional z-score of the current IV surface (R11).

Standardises each ``(tenor, delta)`` cell against the WHOLE current surface :

    z = (iv_cell − mean(all cells)) / std(all cells)

This is a **shape map**, not a temporal signal — it needs no history, so the
heatmap colours on the first cycle. Wings (10Δp / 10Δc) read high (positive z),
ATM low (negative z), and the 10Δp vs 10Δc gap shows the put/call skew. Note it
is dominated by the (structurally stable) smile + term shape, so it visualises
structure rather than rich/cheap-vs-fair. Pure (stdlib only)."""
from __future__ import annotations

import statistics
from typing import Any


def _cell_iv(surface: dict[str, Any], tenor: str, delta: str) -> float | None:
    pillar = surface.get(tenor)
    if not isinstance(pillar, dict):
        return None
    node = pillar.get(delta)
    iv = node.get("iv") if isinstance(node, dict) else None
    return float(iv) if isinstance(iv, (int, float)) else None


def cross_sectional_z(
    surface: dict[str, Any],
    tenors: list[str],
    deltas: list[str],
    min_cells: int = 6,
) -> dict[str, dict[str, float]]:
    """Per-cell z of each IV vs the whole current surface (mean/std over all
    cells). Returns ``{tenor: {delta: z}}`` ; z is scale-invariant (fraction vs
    % IV doesn't matter). Empty when < ``min_cells`` valid cells or a flat
    surface (std ≈ 0)."""
    cells = [
        iv
        for t in tenors
        for d in deltas
        if (iv := _cell_iv(surface, t, d)) is not None
    ]
    if len(cells) < min_cells:
        return {}
    sd = statistics.pstdev(cells)
    if sd <= 1e-12:
        return {}
    mean = statistics.fmean(cells)
    out: dict[str, dict[str, float]] = {}
    for t in tenors:
        for d in deltas:
            iv = _cell_iv(surface, t, d)
            if iv is None:
                continue
            out.setdefault(t, {})[d] = round((iv - mean) / sd, 4)
    return out
