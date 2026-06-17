"""Per-cell IV z-score (R11) — rich/cheap of each (tenor, delta) surface cell
vs its own recent history.

    z = (iv_now − mean(history)) / std(history)

Pure (numpy-free, stdlib only). The vol-engine feeds it the last N persisted
surfaces + the current one ; the result drives the Signals IV-heatmap background
(replacing the synthetic z). None per cell when there's too little history or a
flat series — the UI then shows a neutral cell rather than a fabricated signal.
"""
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


def compute_iv_z(
    history: list[dict[str, Any]],
    current: dict[str, Any],
    tenors: list[str],
    deltas: list[str],
    min_obs: int = 8,
) -> dict[str, dict[str, float]]:
    """Per-cell z of ``current`` vs the per-cell distribution in ``history``.

    ``history``/``current`` are surface payloads (``surface[tenor][delta].iv``).
    Returns ``{tenor: {delta: z}}`` — a cell is omitted when its current IV is
    missing, history has < ``min_obs`` points, or the historical std is ~0
    (z scale-invariant → fraction vs % IV doesn't matter).
    """
    out: dict[str, dict[str, float]] = {}
    for t in tenors:
        for d in deltas:
            cur = _cell_iv(current, t, d)
            if cur is None:
                continue
            past = [v for h in history if (v := _cell_iv(h, t, d)) is not None]
            if len(past) < min_obs:
                continue
            sd = statistics.pstdev(past)
            if sd <= 1e-12:
                continue
            z = (cur - statistics.fmean(past)) / sd
            out.setdefault(t, {})[d] = round(z, 4)
    return out
