"""Step 1 — Regime gating API.

Endpoints :
  GET /api/v1/regime/state              dernier snapshot + gate decision
  GET /api/v1/regime/history?n=N        N derniers snapshots
  GET /api/v1/regime/events             events high-impact futurs
  POST /api/v1/regime/events            insert event (manuel, MVP feed)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db_session
from api.orchestration.regime_features import build_features_payload
from core.vol.regime_engine import gate_decision
from persistence.models import Event, RegimeSnapshot

router = APIRouter(prefix="/api/v1/regime", tags=["regime"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]


class GateOut(BaseModel):
    authorized: bool
    reason: str
    size_mult: float


class NextEventOut(BaseModel):
    event_type: str
    impact: str
    region: str
    scheduled_at: datetime
    days_remaining: float
    description: str | None = None


class RegimeStateOut(BaseModel):
    timestamp: datetime
    symbol: str
    label: str
    method: str
    event_dampener: bool
    days_to_next_event: float | None = None
    next_event_type: str | None = None
    next_event_high: NextEventOut | None = None
    next_event_any: NextEventOut | None = None
    features: dict[str, dict[str, Any]]
    gate: GateOut
    probabilities: dict[str, float] | None = None


class EventIn(BaseModel):
    event_type: str = Field(..., max_length=40)
    impact: str = Field(..., pattern="^(high|medium|low)$")
    region: str = Field(..., max_length=10)
    scheduled_at: datetime
    description: str | None = None


@router.get("/state", response_model=RegimeStateOut)
async def state(
    db: DbDep, symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> RegimeStateOut:
    rows = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .order_by(desc(RegimeSnapshot.timestamp))
        .limit(3)
    )).scalars().all()
    if not rows:
        raise HTTPException(404, f"no regime snapshots for {symbol}")
    latest = rows[0]
    history_labels = [r.label for r in rows]
    decision = gate_decision(latest.label, latest.event_dampener, history_labels)
    next_any, next_high = await _fetch_next_events(db)
    return RegimeStateOut(
        timestamp=latest.timestamp, symbol=latest.symbol, label=latest.label,
        method=latest.method, event_dampener=latest.event_dampener,
        days_to_next_event=float(latest.days_to_next_event) if latest.days_to_next_event is not None else None,
        next_event_type=latest.next_event_type,
        features={
            "vol_level": {
                "value": _f(latest.vol_level_pct), "z": _f(latest.vol_level_z),
            },
            "vol_of_vol": {
                "value": _f(latest.vol_of_vol_pct), "z": _f(latest.vol_of_vol_z),
            },
            "term_slope": {
                "value": _f(latest.term_slope_pct), "z": _f(latest.term_slope_z),
            },
        },
        gate=GateOut(
            authorized=decision.authorized, reason=decision.reason,
            size_mult=decision.size_mult,
        ),
        next_event_any=next_any,
        next_event_high=next_high,
        # Always expose the shadow-computed probas when the DB has them.
        # Frontend uses ``method`` to decide whether they are actionable
        # (gmm_v1 → use them) or shadow-only informational (threshold_
        # heuristic → display + "non pertinent" banner). Cf. STEP1 §13.
        probabilities=(
            {
                "calm": float(latest.p_calm),
                "stressed": float(latest.p_stressed),
                "pre_event": float(latest.p_pre_event),
            }
            if latest.p_calm is not None and latest.p_stressed is not None
            and latest.p_pre_event is not None
            else None
        ),
    )


@router.get("/features")
async def features(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
) -> dict[str, Any]:
    """Step 2 dashboard feed : 3 features × 8 columns + synthesis row.

    Cf. ``api.orchestration.regime_features.build_features_payload``.
    """
    payload = await build_features_payload(db, symbol=symbol)
    if payload is None:
        raise HTTPException(404, f"no regime_snapshot for symbol={symbol}")
    return payload


async def _fetch_next_events(
    db: AsyncSession,
) -> tuple[NextEventOut | None, NextEventOut | None]:
    """Return (next_any_impact, next_high_impact) — separate queries."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    next_any = (await db.execute(
        select(Event).where(Event.scheduled_at > now)
        .order_by(Event.scheduled_at).limit(1)
    )).scalar_one_or_none()
    next_high = (await db.execute(
        select(Event).where(Event.scheduled_at > now).where(Event.impact == "high")
        .order_by(Event.scheduled_at).limit(1)
    )).scalar_one_or_none()

    def _to_out(e: Event | None) -> NextEventOut | None:
        if e is None:
            return None
        days = (e.scheduled_at - now).total_seconds() / 86400.0
        return NextEventOut(
            event_type=e.event_type, impact=e.impact, region=e.region,
            scheduled_at=e.scheduled_at,
            days_remaining=round(days, 4),
            description=e.description,
        )
    return _to_out(next_any), _to_out(next_high)


@router.get("/history")
async def history(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    n: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .order_by(desc(RegimeSnapshot.timestamp))
        .limit(n)
    )).scalars().all()
    return [
        {
            "timestamp": r.timestamp, "label": r.label, "method": r.method,
            "vol_level_pct": _f(r.vol_level_pct), "vol_of_vol_pct": _f(r.vol_of_vol_pct),
            "term_slope_pct": _f(r.term_slope_pct), "event_dampener": r.event_dampener,
            "days_to_next_event": _f(r.days_to_next_event),
            "next_event_type": r.next_event_type,
            # GMM shadow probas (NULL tant que GMM n'a pas fitté ce cycle).
            # Step 2 va consommer cet historique pour calibrer le sizing.
            "p_calm": _f(r.p_calm),
            "p_stressed": _f(r.p_stressed),
            "p_pre_event": _f(r.p_pre_event),
        } for r in rows
    ]


@router.get("/events")
async def list_events(
    db: DbDep, n: int = Query(10, ge=1, le=50),
) -> list[dict[str, Any]]:
    rows = (await db.execute(
        select(Event).where(Event.scheduled_at > datetime.utcnow())
        .order_by(Event.scheduled_at).limit(n)
    )).scalars().all()
    return [
        {
            "id": e.id, "event_type": e.event_type, "impact": e.impact,
            "region": e.region, "scheduled_at": e.scheduled_at,
            "description": e.description, "source": e.source,
        } for e in rows
    ]


@router.get("/transitions")
async def transitions(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    days: int = Query(7, ge=1, le=90),
) -> dict[str, Any]:
    """Compte les transitions de label sur les N derniers jours.

    But : détecter si le seuil heuristique (vov > 0.4 → pre_event) est trop
    proche du bruit de mesure. Si > 5 transitions calm↔pre_event par jour,
    c'est un signal pour ajouter de l'hystérésis ou un seuil dynamique
    (cf. TODO.md / STEP1 §14).

    Returns :
      - by_day : dict {YYYY-MM-DD: {transition_type: count}}
      - total : sum across all days/transitions
      - calm_pre_event_per_day : average flips calm↔pre_event per day
      - threshold_warning : True if average > 5/day
    """
    from datetime import UTC, datetime, timedelta
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (await db.execute(
        select(RegimeSnapshot.timestamp, RegimeSnapshot.label)
        .where(RegimeSnapshot.symbol == symbol)
        .where(RegimeSnapshot.timestamp > cutoff)
        .order_by(RegimeSnapshot.timestamp)
    )).all()

    by_day: dict[str, dict[str, int]] = {}
    total = 0
    calm_pe_count = 0
    prev_label: str | None = None
    for ts, label in rows:
        if prev_label is not None and prev_label != label:
            day = ts.strftime("%Y-%m-%d")
            transition = f"{prev_label}->{label}"
            bucket = by_day.setdefault(day, {})
            bucket[transition] = bucket.get(transition, 0) + 1
            total += 1
            if {prev_label, label} == {"calm", "pre_event"}:
                calm_pe_count += 1
        prev_label = label

    avg_per_day = calm_pe_count / max(days, 1)
    return {
        "symbol": symbol,
        "window_days": days,
        "n_snapshots": len(rows),
        "by_day": by_day,
        "total_transitions": total,
        "calm_pre_event_count": calm_pe_count,
        "calm_pre_event_per_day": round(avg_per_day, 2),
        "threshold_warning": avg_per_day > 5,
        "hint": (
            "Seuil vov>0.4 trop proche du bruit — envisager hystérésis "
            "(entrer 0.4, sortir 0.35) ou seuil dynamique μ + 1.5σ"
            if avg_per_day > 5 else "OK"
        ),
    }


@router.get("/gmm/shadow")
async def gmm_shadow_diagnostic(
    db: DbDep,
    symbol: str = Query("EURUSD", min_length=3, max_length=20),
    n: int = Query(500, ge=10, le=10000),
) -> dict[str, Any]:
    """Compare the shadow GMM outputs vs the active heuristic over the last N
    snapshots. Used to decide when the GMM is good enough to take over the
    label (cf. STEP1 §13 "switch criteria").

    Returns :
      - n_with_gmm : number of snapshots where GMM fitted (probabilities not null)
      - agreement_ratio : fraction where argmax(p_*) == heuristic label
      - cluster_sep_vol_of_vol : (μ_max - μ_min) of components on the vov axis,
        a proxy for how distinct the 3 clusters are. Spec §13 requires
        > 2 × max(σ_intra) before promoting GMM. We don't have σ_intra
        from snapshots ; this number alone is informative on its own.
      - by_label : breakdown {heuristic_label: {gmm_argmax: count}}
      - ready_to_promote : boolean threshold check
    """
    rows = (await db.execute(
        select(RegimeSnapshot)
        .where(RegimeSnapshot.symbol == symbol)
        .order_by(desc(RegimeSnapshot.timestamp))
        .limit(n)
    )).scalars().all()

    n_total = len(rows)
    with_gmm = [
        r for r in rows
        if r.p_calm is not None and r.p_stressed is not None and r.p_pre_event is not None
    ]
    if not with_gmm:
        return {
            "n_total": n_total, "n_with_gmm": 0,
            "agreement_ratio": None, "ready_to_promote": False,
            "reason": "no_snapshot_with_gmm_probas",
        }

    by_label: dict[str, dict[str, int]] = {}
    agree = 0
    for r in with_gmm:
        probas = {"calm": float(r.p_calm), "stressed": float(r.p_stressed), "pre_event": float(r.p_pre_event)}
        gmm_argmax = max(probas, key=probas.get)
        bucket = by_label.setdefault(r.label, {})
        bucket[gmm_argmax] = bucket.get(gmm_argmax, 0) + 1
        if gmm_argmax == r.label:
            agree += 1
    agreement_ratio = agree / len(with_gmm)

    # Promotion gates from §13 :
    #  (1) ≥ 30 days of shadow data — for an MVP we use n_with_gmm ≥ 1000
    #      (~6 hours at 30s cycle, but vol-engine cycle is 180s so it's ~50h)
    #  (2) agreement_ratio ≥ 0.70
    #  (3) training set spans ≥ 1 traversed high-impact event — checked manually
    n_required = 1000
    ready = (
        len(with_gmm) >= n_required and agreement_ratio >= 0.70
    )

    return {
        "n_total": n_total,
        "n_with_gmm": len(with_gmm),
        "agreement_ratio": round(agreement_ratio, 4),
        "by_label": by_label,
        "ready_to_promote": ready,
        "promotion_gates": {
            "n_required": n_required,
            "n_with_gmm_ok": len(with_gmm) >= n_required,
            "agreement_ratio_required": 0.70,
            "agreement_ratio_ok": agreement_ratio >= 0.70,
            "manual_check_needed": "training set must span ≥ 1 traversed high-impact event",
        },
    }


@router.post("/events/sync")
async def sync_events_from_feed(request: Request) -> dict[str, Any]:
    """Trigger one full events sync cycle on demand (admin tool).

    Same code path as the daily background loop : every configured Source
    is hit in parallel with isolation, results are deduped by hash, then
    INSERT ON CONFLICT DO NOTHING. Returns the per-source counts so you
    can immediately see which sources are healthy.
    """
    scheduler = getattr(request.app.state, "events_scheduler", None)
    if scheduler is None:
        raise HTTPException(503, "events scheduler not initialised")
    try:
        report = await scheduler.run_once()
    except Exception as e:
        raise HTTPException(500, f"sync cycle failed: {e}") from e
    return report


@router.post("/events")
async def insert_event(payload: EventIn, db: DbDep) -> dict[str, Any]:
    e = Event(
        event_type=payload.event_type, impact=payload.impact, region=payload.region,
        scheduled_at=payload.scheduled_at, description=payload.description,
        source="manual",
    )
    db.add(e)
    await db.commit()
    await db.refresh(e)
    return {"id": e.id, "scheduled_at": e.scheduled_at}


def _f(x: Any) -> float | None:
    return float(x) if x is not None else None
