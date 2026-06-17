"""Per-cell rich/cheap colour for the IV-surface heatmap (R11).

The desk colours each surface cell by how the market-implied vol compares to
our independent **fair vol** (σ_fair^Q = Yang-Zhang realised vol + variance risk
premium, see ``core/vol/fair_term``) :

    z = (IV_ATM(tenor) − σ_fair^Q(tenor)) / scale          ( + = rich, − = cheap )

This is a *value* signal (sell rich / buy cheap), not a vs-own-history z — it
needs no surface history, so the heatmap colours on the very first cycle. The
fair anchor is an ATM-level (per-tenor) estimate : we have no independent fair
*skew* model yet, so the richness is computed at the ATM and broadcast across
the row's deltas rather than fabricating a per-wing value signal (a fair-skew
model is the stated next step). Pure (stdlib only)."""
from __future__ import annotations

from typing import Any

# Vol points of IV-vs-fair gap that map to one z unit of colour. ~1.5 vp ≈ a
# typical VRP dislocation, so a 3.75 vp gap saturates the divergent scale.
DEFAULT_SCALE_VP = 1.5


def _atm_iv_pct(surface: dict[str, Any], tenor: str) -> float | None:
    """ATM IV of ``tenor`` in percent (surface cells store iv as a fraction)."""
    pillar = surface.get(tenor)
    if not isinstance(pillar, dict):
        return None
    atm = pillar.get("atm")
    iv = atm.get("iv") if isinstance(atm, dict) else None
    return float(iv) * 100.0 if isinstance(iv, (int, float)) else None


def build_fair_richness(
    surface: dict[str, Any],
    fair_q: dict[str, Any],
    deltas: list[str],
    scale_vp: float = DEFAULT_SCALE_VP,
) -> dict[str, dict[str, float]]:
    """Per-cell richness z = (IV_ATM − σ_fair^Q)/scale, broadcast across deltas.

    ``fair_q`` is the ``_fair_q`` sub-dict (``{tenor: {sigma_fair_q_pct, ...}}``).
    Returns ``{tenor: {delta: z}}`` ; a tenor is omitted when its ATM IV or fair
    vol is missing. A cell near fair → z≈0 → neutral (no fabricated signal)."""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(fair_q, dict) or scale_vp <= 0:
        return out
    for tenor, fq in fair_q.items():
        sigma_q = fq.get("sigma_fair_q_pct") if isinstance(fq, dict) else None
        atm_iv = _atm_iv_pct(surface, tenor)
        if not isinstance(sigma_q, (int, float)) or atm_iv is None:
            continue
        z = round((atm_iv - float(sigma_q)) / scale_vp, 4)
        out[tenor] = {d: z for d in deltas}
    return out
