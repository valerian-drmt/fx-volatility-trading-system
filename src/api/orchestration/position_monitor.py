"""Background loop that monitors open positions (Step 5).

Runs in the api container alongside the PCA refit scheduler. Cycle = 60s
(spec STEP5 §1, faster than vol-engine's 180s — exit reactivity).

Per cycle, per open position :
    1. compute MTM (linearised against entry snapshot)
    2. attribute P&L (vega / gamma / theta / other)
    3. evaluate the 5 exit rules → pick winner
    4. cooldown-aware delta-hedge check
    5. persist `position_mtm_history` + `position_signal_tracking` rows
    6. if EXIT triggered : create `exit_alerts` row (5-min cooldown to avoid spam)
    7. if hedge triggered : create `hedge_orders` row (in 'pending' state — real
       IB submit is deferred to execution-engine when markets are open)

In sandbox / mock mode, ``execute_hedge=False`` keeps everything as DB rows
without any IB call.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.positions.delta_hedge import check_delta_hedge_needed
from core.positions.exit_rules import (
    EXIT_RULES,
    CurrentSignal,
    PositionContext,
    evaluate_all_rules,
    pick_winning_decision,
)
from core.positions.mtm import attribute_pnl, compute_mtm
from persistence.models import (
    DeltaHedgeConfig,
    ExitAlert,
    HedgeOrder,
    PcaSignal,
    PositionMtmHistory,
    PositionSignalTracking,
    TradePosition,
    TradeStructure,
)

logger = logging.getLogger(__name__)


class PositionMonitorScheduler:
    """Async loop. Same shape as PcaRefitScheduler — owns the asyncio.Task."""

    def __init__(
        self,
        sessionmaker_factory: Callable[[], async_sessionmaker[AsyncSession]],
        symbol: str = "EURUSD",
        interval_s: float = 60.0,
        startup_delay_s: float = 30.0,
        alert_cooldown_minutes: float = 5.0,
        hedge_cooldown_seconds: float = 300.0,
        execute_hedge: bool = False,
    ):
        self._sm_factory = sessionmaker_factory
        self.symbol = symbol
        self.interval_s = interval_s
        self.startup_delay_s = startup_delay_s
        self.alert_cooldown = timedelta(minutes=alert_cooldown_minutes)
        self.hedge_cooldown_seconds = hedge_cooldown_seconds
        self.execute_hedge = execute_hedge
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="position_monitor_loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        await asyncio.sleep(self.startup_delay_s)
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("position_monitor_cycle_crashed")
            await asyncio.sleep(self.interval_s)

    async def run_once(self) -> dict[str, Any]:
        """One pass over all open positions. Returns a small report."""
        sm = self._sm_factory()
        async with sm() as db:
            return await self._cycle(db)

    async def _cycle(self, db: AsyncSession) -> dict[str, Any]:
        open_positions = (await db.execute(
            select(TradePosition).where(TradePosition.state == "open")
        )).scalars().all()
        if not open_positions:
            return {"open_positions": 0, "alerts": 0, "hedges": 0}

        # Latest current signals per pc_id (using whatever pca model is currently latest)
        current_signals = await self._load_current_signals(db)
        # Surface = best-effort from Redis ; markets-closed sandbox falls back to None
        spot_now, iv_now_pct = await self._best_effort_market_snapshot()

        n_alerts = 0
        n_hedges = 0
        now = datetime.now(UTC)

        for pos in open_positions:
            try:
                report = await self._monitor_one(
                    db, pos, now, spot_now, iv_now_pct, current_signals,
                )
                n_alerts += report["alert_persisted"]
                n_hedges += report["hedge_persisted"]
            except Exception:
                logger.exception("position_monitor_one_failed position_id=%s", pos.id)

        await db.commit()
        return {
            "open_positions": len(open_positions),
            "alerts": n_alerts,
            "hedges": n_hedges,
        }

    async def _monitor_one(
        self, db: AsyncSession, pos: TradePosition, now: datetime,
        spot_now: float | None, iv_now_pct: float | None,
        current_signals: dict[int, CurrentSignal],
    ) -> dict[str, int]:
        # Load parent structure (for triggering_pc, expiry, armed_z)
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
        )).scalar_one_or_none()
        if struct is None:
            return {"alert_persisted": 0, "hedge_persisted": 0}

        spot_eff = spot_now if spot_now is not None else (pos.entry_spot or 1.085)
        iv_eff = iv_now_pct if iv_now_pct is not None else (pos.entry_iv_avg or 7.0)

        # Linearised attribution from entry. mark_value is approximated as
        # entry_premium + Δvega·Δiv + ½·γ·Δs² — i.e. attribution itself acts
        # as the mark for monitoring purposes.
        days_elapsed = (now - pos.opened_at).total_seconds() / 86400.0
        # Use entry-time greeks as approximation for current greeks (decay
        # captured in `theta_pnl_usd`). Real implementation would re-price legs.
        attribution = attribute_pnl(
            pnl_gross_usd=0.0,                 # placeholder — overwritten below
            entry_vega_usd_per_volpt=pos.entry_vega_usd_per_volpt or 0.0,
            entry_gamma_usd_per_pip2=pos.entry_gamma_usd_per_pip2 or 0.0,
            entry_theta_usd_per_day=pos.entry_theta_usd_per_day or 0.0,
            iv_entry_pct=pos.entry_iv_avg or iv_eff,
            iv_now_pct=iv_eff,
            spot_entry=pos.entry_spot or spot_eff,
            spot_now=spot_eff,
            days_elapsed=days_elapsed,
        )
        # In linearised-attribution mode, gross = vega + gamma + theta (no other)
        pnl_gross = attribution.vega_usd + attribution.gamma_usd + attribution.theta_usd

        mtm = compute_mtm(
            entry_premium_usd=pos.entry_premium_usd,
            mark_value_usd=pos.entry_premium_usd + pnl_gross,
            entry_total_cost_usd=pos.entry_total_cost_usd or 0.0,
            hedge_cost_cumul_usd=0.0,           # MVP : ignore until real fills exist
            spot_now=spot_eff, iv_now_pct=iv_eff,
        )

        # Persist mtm row (skipped silently on UNIQUE collision for same ts)
        db.add(PositionMtmHistory(
            position_id=pos.id, timestamp=now,
            spot=spot_eff, iv_avg_legs_pct=iv_eff,
            current_pnl_gross_usd=mtm.pnl_gross_usd,
            current_pnl_net_usd=mtm.pnl_net_usd,
            vega_pnl_usd=attribution.vega_usd,
            gamma_pnl_usd=attribution.gamma_usd,
            theta_pnl_usd=attribution.theta_usd,
            other_pnl_usd=attribution.other_usd,
            current_vega_usd_per_volpt=pos.entry_vega_usd_per_volpt,
            current_gamma_usd_per_pip2=pos.entry_gamma_usd_per_pip2,
            current_theta_usd_per_day=pos.entry_theta_usd_per_day,
            current_delta_unhedged=0.0,
        ))

        # Build context for exit rules
        triggering_pc = struct.triggering_pc
        entry_z = float(struct.armed_z_score) if struct.armed_z_score is not None else None
        days_remaining = self._days_remaining(struct, now)
        dte_at_entry = self._dte_at_entry(struct, pos.opened_at)

        ctx = PositionContext(
            position_id=pos.id, triggering_pc=triggering_pc,
            entry_z_score=entry_z,
            entry_vega_usd_per_volpt=pos.entry_vega_usd_per_volpt or 0.0,
            dte_at_entry=dte_at_entry, days_remaining=days_remaining,
        )

        # Persist signal tracking (only if we have a triggering_pc and entry_z)
        if triggering_pc is not None and entry_z is not None:
            current = current_signals.get(triggering_pc)
            if current is not None:
                ratio = (
                    abs(current.z_score) / abs(entry_z)
                    if abs(entry_z) > 1e-9 else None
                )
                flipped = (entry_z > 0) != (current.z_score > 0)
                status = self._signal_status(entry_z, current.z_score, flipped, ratio)
                db.add(PositionSignalTracking(
                    position_id=pos.id, timestamp=now,
                    triggering_pc=triggering_pc,
                    current_z_score=current.z_score,
                    current_label=current.label,
                    entry_z_score=entry_z,
                    entry_label=struct.armed_signal_label or "FAIR",
                    weakening_ratio=ratio, sign_flipped=flipped,
                    status=status,
                ))

        # Exit rules
        decisions = evaluate_all_rules(
            EXIT_RULES,
            ctx=ctx, mtm_pnl_gross_usd=mtm.pnl_gross_usd,
            current_signals=current_signals,
            regime=None,                          # MVP : skip regime (Step 1 service)
        )
        winner = pick_winning_decision(decisions)
        alert_persisted = 0
        if winner is not None and not await self._recent_alert_exists(
            db, pos.id, winner.rule_name, now,
        ):
            db.add(ExitAlert(
                position_id=pos.id, timestamp=now,
                rule_triggered=winner.rule_name,
                action_recommended=winner.action,
                priority=winner.priority,
                rule_detail=winner.detail,
                auto_executed=False,                  # mock mode : human review
                execution_status=None,
            ))
            alert_persisted = 1

        # Delta hedge — current_delta = 0 in linearised model ; real delta needs
        # full-leg re-pricing which arrives with markets-open phase.
        # Code is wired so flip to live data is a 1-liner.
        hedge_persisted = 0
        last_hedge = (await db.execute(
            select(HedgeOrder).where(HedgeOrder.position_id == pos.id)
            .order_by(desc(HedgeOrder.triggered_at)).limit(1)
        )).scalar_one_or_none()
        cfg = await self._load_hedge_config(db)
        decision = check_delta_hedge_needed(
            delta_unhedged=0.0,                       # placeholder — see comment above
            threshold=cfg["rebalance_threshold_delta"],
            min_hedge_qty=int(cfg["min_hedge_qty"]),
            last_hedge_at=last_hedge.triggered_at if last_hedge else None,
            now=now,
            cooldown_seconds=cfg["max_hedge_frequency_seconds"],
        )
        if decision.needs_hedge:
            db.add(HedgeOrder(
                position_id=pos.id, triggered_at=now,
                delta_imbalance_at_trigger=decision.delta_imbalance,
                rebalance_threshold_used=decision.threshold_used,
                hedge_qty=decision.hedge_qty, side=decision.side,
                state="pending",                  # real submit when markets open
            ))
            hedge_persisted = 1

        return {"alert_persisted": alert_persisted, "hedge_persisted": hedge_persisted}

    @staticmethod
    def _days_remaining(struct: TradeStructure, now: datetime) -> int:
        if struct.expiry_date is None:
            return 90
        return max(0, (struct.expiry_date - now.date()).days)

    @staticmethod
    def _dte_at_entry(struct: TradeStructure, opened_at: datetime) -> int:
        if struct.expiry_date is None:
            return 90
        return max(1, (struct.expiry_date - opened_at.date()).days)

    @staticmethod
    def _signal_status(
        entry_z: float, current_z: float, flipped: bool, ratio: float | None,
    ) -> str:
        if flipped or abs(current_z) < 0.5:
            return "EXIT"
        if ratio is not None and ratio < 0.5:
            return "TRIM"
        return "HOLD"

    async def _load_current_signals(self, db: AsyncSession) -> dict[int, CurrentSignal]:
        """Get the latest signal per pc_id (most recent timestamp)."""
        rows = (await db.execute(
            select(PcaSignal).where(PcaSignal.symbol == self.symbol)
            .order_by(desc(PcaSignal.timestamp)).limit(50)
        )).scalars().all()
        out: dict[int, CurrentSignal] = {}
        for r in rows:
            if r.pc_id not in out:
                out[r.pc_id] = CurrentSignal(
                    pc_id=r.pc_id, z_score=float(r.z_score), label=r.label,
                )
        return out

    async def _load_hedge_config(self, db: AsyncSession) -> dict[str, float]:
        rows = (await db.execute(select(DeltaHedgeConfig))).scalars().all()
        defaults = {
            "rebalance_threshold_delta": 0.05,
            "min_hedge_qty": 1.0,
            "max_hedge_frequency_seconds": 300.0,
            "hedge_during_close": 0.0,
        }
        out = dict(defaults)
        for r in rows:
            out[r.config_name] = r.config_value
        return out

    async def _recent_alert_exists(
        self, db: AsyncSession, position_id: int, rule_name: str, now: datetime,
    ) -> bool:
        cutoff = now - self.alert_cooldown
        existing = (await db.execute(
            select(ExitAlert).where(ExitAlert.position_id == position_id)
            .where(ExitAlert.rule_triggered == rule_name)
            .where(ExitAlert.timestamp > cutoff).limit(1)
        )).scalar_one_or_none()
        return existing is not None

    async def _best_effort_market_snapshot(self) -> tuple[float | None, float | None]:
        """Try to read latest spot + ATM IV from Redis. Returns (None, None) if absent."""
        try:
            from api.dependencies import get_redis_client_or_none
            client = get_redis_client_or_none()
            if client is None:
                return None, None
            raw = await client.get(f"latest_vol_surface:{self.symbol}")
            if not raw:
                return None, None
            import json
            payload = json.loads(raw)
            surface = payload.get("surface") or payload
            spot = payload.get("spot")
            atm_3m = (surface.get("3M") or {}).get("atm", {}).get("iv")
            return (
                float(spot) if spot is not None else None,
                float(atm_3m) * 100.0 if atm_3m is not None else None,
            )
        except Exception:
            return None, None


def build_position_monitor_scheduler() -> PositionMonitorScheduler:
    """Wire dependencies + read env-driven config."""
    from persistence.db import get_sessionmaker

    interval = float(os.environ.get("POSITION_MONITOR_INTERVAL_S", "60.0"))
    startup = float(os.environ.get("POSITION_MONITOR_STARTUP_DELAY_S", "30.0"))
    symbol = os.environ.get("POSITION_MONITOR_SYMBOL", "EURUSD")
    return PositionMonitorScheduler(
        sessionmaker_factory=get_sessionmaker,
        symbol=symbol, interval_s=interval, startup_delay_s=startup,
    )
