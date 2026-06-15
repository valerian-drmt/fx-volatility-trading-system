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
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bus.publisher import publish_exit_alert, publish_position_update
from core.positions.delta_hedge import check_delta_hedge_needed
from core.positions.exit_rules import (
    EXIT_RULES,
    CurrentSignal,
    PositionContext,
    evaluate_all_rules,
    pick_winning_decision,
)
from core.positions.mtm import attribute_pnl, compute_mtm
from core.positions.position_pricing import LegSpec, PositionMark, price_position
from persistence.models import (
    AppConfigScalar,
    BookedPosition,
    BookedPositionMetricHistory,
    ExitAlert,
    HedgeOrder,
    PcaSignal,
    RegimeSnapshot,
    StructureOrder,
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
        # Hedge IDs to POST to execution-engine after the cycle commits.
        self._pending_hedge_ids: list[int] = []
        # (position_id, alert_id, rule_name) tuples to close after commit.
        self._pending_close_alerts: list[tuple[int, int, str]] = []
        # WS payloads accumulated during the cycle, dispatched after commit.
        self._pending_position_updates: list[dict] = []
        self._pending_exit_alerts: list[dict] = []

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
            select(BookedPosition).where(BookedPosition.state == "open")
        )).scalars().all()
        if not open_positions:
            return {"open_positions": 0, "alerts": 0, "hedges": 0}

        # Latest current signals per pc_id (using whatever pca model is currently latest)
        current_signals = await self._load_current_signals(db)
        # Latest regime label (feeds PreEventRegimeRule, priority 6).
        current_regime = await self._load_latest_regime(db)
        # Surface = best-effort from Redis ; markets-closed sandbox falls back to None
        surface, spot_now, iv_now_pct = await self._best_effort_market_snapshot_full()

        n_alerts = 0
        n_hedges = 0
        now = datetime.now(UTC)

        for pos in open_positions:
            try:
                report = await self._monitor_one(
                    db, pos, now, spot_now, iv_now_pct, current_signals,
                    current_regime, surface,
                )
                n_alerts += report["alert_persisted"]
                n_hedges += report["hedge_persisted"]
            except Exception:
                logger.exception("position_monitor_one_failed position_id=%s", pos.id)

        await db.commit()

        # Post-commit : fire pending hedges to execution-engine. Failures
        # leave the row in 'pending' state — operator retries.
        await self._dispatch_pending_hedges()
        await self._dispatch_pending_closes()
        await self._publish_ws_updates()

        return {
            "open_positions": len(open_positions),
            "alerts": n_alerts,
            "hedges": n_hedges,
        }

    async def _dispatch_pending_hedges(self) -> None:
        if not self._pending_hedge_ids:
            return
        ids = list(self._pending_hedge_ids)
        self._pending_hedge_ids.clear()
        try:
            import os

            import httpx
            base = os.environ.get("EXECUTION_ENGINE_URL", "http://execution-engine:8001")
            async with httpx.AsyncClient(timeout=10.0) as client:
                for hid in ids:
                    try:
                        resp = await client.post(
                            f"{base.rstrip('/')}/internal/hedge",
                            json={"hedge_order_id": hid},
                        )
                        if resp.status_code >= 400:
                            logger.warning(
                                "hedge_dispatch_failed id=%s status=%s",
                                hid, resp.status_code,
                            )
                    except Exception:
                        logger.exception("hedge_dispatch_exception id=%s", hid)
        except Exception:
            logger.exception("hedge_dispatch_loop_crashed")

    async def _monitor_one(
        self, db: AsyncSession, pos: BookedPosition, now: datetime,
        spot_now: float | None, iv_now_pct: float | None,
        current_signals: dict[int, CurrentSignal],
        current_regime: str | None = None,
        surface: dict | None = None,
    ) -> dict[str, int]:
        # Load parent structure (for triggering_pc, expiry, armed_z)
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == pos.structure_id).limit(1)
        )).scalar_one_or_none()
        if struct is None:
            return {"alert_persisted": 0, "hedge_persisted": 0}

        spot_eff = spot_now if spot_now is not None else (pos.entry_spot or 1.085)
        iv_eff = iv_now_pct if iv_now_pct is not None else (pos.entry_iv_avg or 7.0)

        # Live re-pricing if surface available — replaces the linearised
        # attribution as primary mark + greeks source. Fallback path keeps
        # the linearised math (markets-closed sandbox).
        live_mark: PositionMark | None = await self._reprice_position(
            db, struct.id, surface, spot_eff, now,
        )

        days_elapsed = (now - pos.opened_at).total_seconds() / 86400.0
        if live_mark is not None and live_mark.n_surface_missing == 0:
            mark_value = live_mark.mark_value_usd
            current_vega = live_mark.total_vega_usd_per_volpt
            current_gamma = live_mark.total_gamma_usd_per_pip2
            current_theta = live_mark.total_theta_usd_per_day
            current_delta_unhedged = live_mark.total_delta
            # Attribution still computed against entry-greeks for the
            # vega/gamma/theta breakdown columns — this is the linearised
            # decomposition of the *gross* PnL ; the residual lands in
            # `other_pnl_usd` and absorbs any non-linearity vs the live mark.
            attribution = attribute_pnl(
                pnl_gross_usd=mark_value - pos.entry_premium_usd,
                entry_vega_usd_per_volpt=pos.entry_vega_usd_per_volpt or 0.0,
                entry_gamma_usd_per_pip2=pos.entry_gamma_usd_per_pip2 or 0.0,
                entry_theta_usd_per_day=pos.entry_theta_usd_per_day or 0.0,
                iv_entry_pct=pos.entry_iv_avg or iv_eff,
                iv_now_pct=iv_eff,
                spot_entry=pos.entry_spot or spot_eff,
                spot_now=spot_eff,
                days_elapsed=days_elapsed,
            )
        else:
            attribution = attribute_pnl(
                pnl_gross_usd=0.0,
                entry_vega_usd_per_volpt=pos.entry_vega_usd_per_volpt or 0.0,
                entry_gamma_usd_per_pip2=pos.entry_gamma_usd_per_pip2 or 0.0,
                entry_theta_usd_per_day=pos.entry_theta_usd_per_day or 0.0,
                iv_entry_pct=pos.entry_iv_avg or iv_eff,
                iv_now_pct=iv_eff,
                spot_entry=pos.entry_spot or spot_eff,
                spot_now=spot_eff,
                days_elapsed=days_elapsed,
            )
            mark_value = pos.entry_premium_usd + (
                attribution.vega_usd + attribution.gamma_usd + attribution.theta_usd
            )
            current_vega = pos.entry_vega_usd_per_volpt
            current_gamma = pos.entry_gamma_usd_per_pip2
            current_theta = pos.entry_theta_usd_per_day
            current_delta_unhedged = 0.0

        hedge_cost_cumul = await self._sum_filled_hedge_costs(db, pos.id)
        mtm = compute_mtm(
            entry_premium_usd=pos.entry_premium_usd,
            mark_value_usd=mark_value,
            entry_total_cost_usd=pos.entry_total_cost_usd or 0.0,
            hedge_cost_cumul_usd=hedge_cost_cumul,
            spot_now=spot_eff, iv_now_pct=iv_eff,
        )

        # Stage WS payload for post-commit publish.
        self._pending_position_updates.append({
            "position_id": pos.id,
            "structure_id": struct.id,
            "spot": spot_eff,
            "iv_avg_pct": iv_eff,
            "current_pnl_gross_usd": mtm.pnl_gross_usd,
            "current_pnl_net_usd": mtm.pnl_net_usd,
            "current_vega_usd_per_volpt": current_vega,
            "current_gamma_usd_per_pip2": current_gamma,
            "current_theta_usd_per_day": current_theta,
            "current_delta_unhedged": current_delta_unhedged,
            "hedge_cost_cumul_usd": hedge_cost_cumul,
        })

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

        # Compute signal-tracking cols, folded into the mtm row (migration 039).
        # All NULL for positions not opened from a triggering PCA signal.
        signal_cols: dict[str, Any] = {
            "triggering_pc": None, "current_z_score": None, "current_label": None,
            "entry_z_score": None, "entry_label": None, "weakening_ratio": None,
            "sign_flipped": None, "signal_status": None,
        }
        if triggering_pc is not None and entry_z is not None:
            current = current_signals.get(triggering_pc)
            if current is not None:
                ratio = (
                    abs(current.z_score) / abs(entry_z)
                    if abs(entry_z) > 1e-9 else None
                )
                flipped = (entry_z > 0) != (current.z_score > 0)
                status = self._signal_status(entry_z, current.z_score, flipped, ratio)
                signal_cols.update({
                    "triggering_pc": triggering_pc,
                    "current_z_score": current.z_score,
                    "current_label": current.label,
                    "entry_z_score": entry_z,
                    "entry_label": struct.armed_signal_label or "FAIR",
                    "weakening_ratio": ratio, "sign_flipped": flipped,
                    "signal_status": status,
                })

        # Persist mtm + signal in a single folded row (migration 039 ;
        # skipped silently on UNIQUE collision for same ts).
        db.add(BookedPositionMetricHistory(
            position_id=pos.id, timestamp=now,
            spot=spot_eff, iv_avg_legs_pct=iv_eff,
            current_pnl_gross_usd=mtm.pnl_gross_usd,
            current_pnl_net_usd=mtm.pnl_net_usd,
            vega_pnl_usd=attribution.vega_usd,
            gamma_pnl_usd=attribution.gamma_usd,
            theta_pnl_usd=attribution.theta_usd,
            other_pnl_usd=attribution.other_usd,
            current_vega_usd_per_volpt=current_vega,
            current_gamma_usd_per_pip2=current_gamma,
            current_theta_usd_per_day=current_theta,
            current_delta_unhedged=current_delta_unhedged,
            **signal_cols,
        ))

        # Exit rules
        decisions = evaluate_all_rules(
            EXIT_RULES,
            ctx=ctx, mtm_pnl_gross_usd=mtm.pnl_gross_usd,
            current_signals=current_signals,
            regime=current_regime,
        )
        winner = pick_winning_decision(decisions)
        alert_persisted = 0
        if winner is not None and not await self._recent_alert_exists(
            db, pos.id, winner.rule_name, now,
        ):
            new_alert = ExitAlert(
                position_id=pos.id, timestamp=now,
                rule_triggered=winner.rule_name,
                action_recommended=winner.action,
                priority=winner.priority,
                rule_detail=winner.detail,
                auto_executed=False,
                execution_status=None,
            )
            db.add(new_alert)
            await db.flush()
            alert_persisted = 1
            self._pending_exit_alerts.append({
                "position_id": pos.id, "alert_id": new_alert.id,
                "rule_triggered": winner.rule_name,
                "action_recommended": winner.action,
                "priority": winner.priority,
                "rule_detail": winner.detail,
            })
            # Phase 3 : EXIT alerts trigger an automatic close.
            from api.orchestration.position_close import auto_execute_enabled
            if winner.action == "EXIT" and auto_execute_enabled():
                self._pending_close_alerts.append(
                    (pos.id, new_alert.id, winner.rule_name),
                )

        # Delta hedge — feeds on the live ``current_delta_unhedged`` computed
        # above. ``execute_hedge=True`` POSTs the new HedgeOrder row to
        # execution-engine ``/internal/hedge`` after persisting it.
        hedge_persisted = 0
        last_hedge = (await db.execute(
            select(HedgeOrder).where(HedgeOrder.position_id == pos.id)
            .order_by(desc(HedgeOrder.triggered_at)).limit(1)
        )).scalar_one_or_none()
        cfg = await self._load_hedge_config(db)
        decision = check_delta_hedge_needed(
            delta_unhedged=float(current_delta_unhedged),
            threshold=cfg["rebalance_threshold_delta"],
            min_hedge_qty=int(cfg["min_hedge_qty"]),
            last_hedge_at=last_hedge.triggered_at if last_hedge else None,
            now=now,
            cooldown_seconds=cfg["max_hedge_frequency_seconds"],
        )
        if decision.needs_hedge:
            hedge = HedgeOrder(
                position_id=pos.id, triggered_at=now,
                delta_imbalance_at_trigger=decision.delta_imbalance,
                rebalance_threshold_used=decision.threshold_used,
                hedge_qty=decision.hedge_qty, side=decision.side,
                state="pending",
            )
            db.add(hedge)
            await db.flush()
            hedge_persisted = 1
            if self.execute_hedge:
                # Caller fires the IB submit out-of-band so this DB
                # transaction commits independently.
                self._pending_hedge_ids.append(hedge.id)

        return {"alert_persisted": alert_persisted, "hedge_persisted": hedge_persisted}

    async def _publish_ws_updates(self) -> None:
        """Best-effort PUBLISH of position_updates + exit_alerts to Redis.

        Failures here are non-fatal — the DB rows are already persisted, so
        any WS client that misses a frame can re-fetch via REST.
        """
        positions = list(self._pending_position_updates)
        alerts = list(self._pending_exit_alerts)
        self._pending_position_updates.clear()
        self._pending_exit_alerts.clear()
        if not (positions or alerts):
            return
        try:
            from api.dependencies import get_redis_client_or_none
            redis = get_redis_client_or_none()
            if redis is None:
                return
            for p in positions:
                try:
                    await publish_position_update(redis, p)
                except Exception:
                    logger.warning("publish_position_update_failed", exc_info=True)
            for a in alerts:
                try:
                    await publish_exit_alert(redis, a)
                except Exception:
                    logger.warning("publish_exit_alert_failed", exc_info=True)
        except Exception:
            logger.exception("ws_publish_loop_crashed")

    async def _dispatch_pending_closes(self) -> None:
        if not self._pending_close_alerts:
            return
        items = list(self._pending_close_alerts)
        self._pending_close_alerts.clear()
        from api.orchestration.position_close import initiate_position_close
        for position_id, alert_id, rule_name in items:
            try:
                await initiate_position_close(
                    sessionmaker_factory=self._sm_factory,
                    position_id=position_id,
                    reason=f"auto-exit: {rule_name}",
                    exit_alert_id=alert_id,
                    execution_mode="live",
                )
            except Exception:
                logger.exception(
                    "auto_close_failed pos=%s alert=%s", position_id, alert_id,
                )

    async def _reprice_position(
        self, db: AsyncSession, structure_id: int,
        surface: dict | None, spot: float, now: datetime,
    ) -> PositionMark | None:
        """Build LegSpec list from structure_orders and call price_position.

        Returns None when entry orders cannot be located (very early in the
        cascade) — caller falls back to the linearised path.
        """
        rows = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == "entry")
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        if not rows:
            return None
        legs: list[LegSpec] = []
        for o in rows:
            if o.contract_strike is None or o.contract_expiry is None:
                continue
            qty_eff = int(o.qty_filled) if (o.qty_filled or 0) > 0 else int(o.qty)
            if qty_eff <= 0:
                continue
            tenor = self._tenor_from_expiry(o.contract_expiry, now)
            fallback_iv = (
                float(o.preview_iv_pct) / 100.0 if o.preview_iv_pct else None
            )
            legs.append(LegSpec(
                leg_idx=o.leg_idx, contract_type=o.contract_type,
                strike=float(o.contract_strike), expiry=o.contract_expiry,
                tenor=tenor, side=o.side, qty=qty_eff,
                fallback_iv=fallback_iv,
            ))
        if not legs:
            return None
        return price_position(legs=legs, surface=surface, spot=spot, now=now)

    @staticmethod
    def _tenor_from_expiry(expiry: date, now: datetime) -> str:
        """Pick the surface pillar tenor closest to days-to-expiry."""
        days = max(1, (expiry - now.date()).days)
        # Map days → label using nearest match to the standard pillars.
        candidates = {"1M": 30, "2M": 60, "3M": 90, "6M": 180}
        return min(candidates.items(), key=lambda kv: abs(kv[1] - days))[0]

    async def _sum_filled_hedge_costs(
        self, db: AsyncSession, position_id: int,
    ) -> float:
        rows = (await db.execute(
            select(HedgeOrder)
            .where(HedgeOrder.position_id == position_id)
            .where(HedgeOrder.state == "filled")
        )).scalars().all()
        return sum(float(r.total_cost_usd or 0.0) for r in rows)

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

    async def _load_latest_regime(self, db: AsyncSession) -> str | None:
        """Read latest regime label for this symbol. None if no snapshot yet.

        Used to feed `PreEventRegimeRule` (priority 6 — max). When regime
        flips to ``pre_event`` (e.g. NFP / FOMC stickers), every open
        position gets an EXIT alert (cf. spec STEP5 §3.5).
        """
        row = (await db.execute(
            select(RegimeSnapshot)
            .where(RegimeSnapshot.symbol == self.symbol)
            .order_by(desc(RegimeSnapshot.timestamp))
            .limit(1)
        )).scalar_one_or_none()
        return str(row.label) if row is not None else None

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
        # delta_hedge_config rows folded into config_scalar with
        # namespace='delta_hedge' (migration 033).
        rows = (await db.execute(
            select(AppConfigScalar).where(AppConfigScalar.namespace == "delta_hedge")
        )).scalars().all()
        defaults = {
            "rebalance_threshold_delta": 0.05,
            "min_hedge_qty": 1.0,
            "max_hedge_frequency_seconds": 300.0,
            "hedge_during_close": 0.0,
        }
        out = dict(defaults)
        for r in rows:
            out[r.name] = r.value
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

    async def _best_effort_market_snapshot_full(
        self,
    ) -> tuple[dict | None, float | None, float | None]:
        """Read full surface + spot + 3M ATM IV from Redis.

        Returns (surface_dict, spot, iv_3m_atm_pct). All three None when
        Redis is empty or unreachable (markets-closed sandbox).
        """
        try:
            from api.dependencies import get_redis_client_or_none
            client = get_redis_client_or_none()
            if client is None:
                return None, None, None
            raw = await client.get(f"latest_vol_surface:{self.symbol}")
            if not raw:
                return None, None, None
            import json
            payload = json.loads(raw)
            surface = payload.get("surface") or payload
            spot = payload.get("spot")
            atm_3m = (surface.get("3M") or {}).get("atm", {})
            iv_3m = atm_3m.get("iv") if isinstance(atm_3m, dict) else None
            return (
                surface if isinstance(surface, dict) else None,
                float(spot) if spot is not None else None,
                float(iv_3m) * 100.0 if iv_3m is not None else None,
            )
        except Exception:
            return None, None, None


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
