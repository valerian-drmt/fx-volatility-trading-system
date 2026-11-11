"""Collect the pre-trade gate inputs and decide (#315).

``core.risk.pretrade.evaluate_pretrade`` is the pure decision; this module
reads the world for it: the kill switch, the risk engine's heartbeat, the IB
account type, the live book's greeks (``open_position``, the risk engine's
numbers), the candidate structure's greeks from the armed preview, the caps
the greek-limits panel shows, the surface age and the arbitrage flag. Any
read that fails leaves its input None, which the gate treats as a failure.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from redis import asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import get_settings
from api.orchestration.greek_caps import compute_live_caps
from bus import keys
from bus.spot_cache import parse_spot
from core.risk.pretrade import BookGreeks, Caps, GateInputs, GateResult, TradeGreeks, evaluate_pretrade
from core.units import EUR_FOP_MULTIPLIER, PIP_SIZE
from persistence.halt import get_halt
from persistence.models import AppConfigScalar, IbConnectionState, OpenPosition

logger = logging.getLogger(__name__)

DEFAULT_MAX_NOTIONAL_EUR = 5_000_000.0
DEFAULT_MAX_RISK_HEARTBEAT_AGE_S = 30.0


def _allowed_symbols() -> tuple[str, ...]:
    return get_settings().allowed_symbols


def _required_account_type() -> str:
    return get_settings().trading_mode


async def _risk_heartbeat_age_s(redis: aioredis.Redis | None) -> float | None:
    if redis is None:
        return None
    try:
        raw = await redis.get(keys.HEARTBEAT.format(engine_name=keys.ENGINE_RISK))
    except Exception:
        return None
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(UTC) - ts).total_seconds())


async def _spot(redis: aioredis.Redis | None, payload: dict[str, Any]) -> float | None:
    if redis is not None:
        try:
            spot = parse_spot(await redis.get(keys.LATEST_SPOT.format(symbol="EURUSD")))[0]
            if spot is not None:
                return spot
        except Exception:
            pass
    s = payload.get("spot") or (payload.get("market") or {}).get("spot")
    try:
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def trade_greeks_from_preview(payload: dict[str, Any], spot: float | None) -> TradeGreeks | None:
    """Full-size structure greeks in desk units from the armed preview's
    ``greeks_net`` (core.trade_preview._leg_greeks: vega $/vol pt, gamma
    $/pip², delta already in USD, bs_delta × spot × multiplier) -> gamma $/pip,
    delta USD exposure. The delta used to be multiplied by the contract
    multiplier and spot a second time, so the delta cap refused any trade
    that was not delta-neutral."""
    g = payload.get("greeks_net") or {}
    legs = (payload.get("structure") or {}).get("legs") or []
    if not g or not legs or spot is None:
        return None
    try:
        vega = float(g.get("vega_usd_per_volpt") or 0.0)
        gamma_pip = float(g.get("gamma_usd_per_pip2") or 0.0) / PIP_SIZE
        delta_usd = float(g.get("delta_unhedged") or 0.0)
    except (TypeError, ValueError):
        return None
    mult = float((payload.get("structure") or {}).get("future_multiplier_eur") or EUR_FOP_MULTIPLIER)
    notional = sum(abs(float(leg.get("qty") or 0)) for leg in legs) * mult
    return TradeGreeks(
        delta_usd=delta_usd,
        vega_usd_per_volpt=vega,
        gamma_usd_per_pip=gamma_pip,
        notional_eur=notional,
    )


def leg_symbols(payload: dict[str, Any]) -> tuple[str, ...]:
    structure = payload.get("structure") or {}
    size = structure.get("future_contract_size")
    if size == "micro":
        return ("M6E",)
    return ("EUR",)


async def run_pretrade_gate(
    db: AsyncSession, redis: aioredis.Redis | None, *,
    payload: dict[str, Any], live_mode: bool,
) -> GateResult:
    # kill switch
    try:
        h = await get_halt(db)
        halted, halt_reason = h.halted, h.reason
    except Exception:
        logger.exception("pretrade_halt_read_failed")
        halted, halt_reason = None, None

    # limits namespace (max notional, surface age)
    try:
        rows = (await db.execute(
            select(AppConfigScalar.name, AppConfigScalar.value)
            .where(AppConfigScalar.namespace == "risk", AppConfigScalar.is_active.is_(True))
        )).all()
        limits = {n: float(v) for n, v in rows}
    except Exception:
        limits = {}

    # IB account type
    try:
        acct_type = (await db.execute(
            select(IbConnectionState.account_type).where(IbConnectionState.broker == "IB").limit(1)
        )).scalar_one_or_none()
    except Exception:
        acct_type = None

    # live book greeks (risk engine's numbers on the IB mirror)
    book: BookGreeks | None
    try:
        r = (await db.execute(select(
            func.coalesce(func.sum(OpenPosition.delta_usd), 0.0),
            func.coalesce(func.sum(OpenPosition.vega_usd), 0.0),
            func.coalesce(func.sum(OpenPosition.gamma_usd), 0.0),
            func.count(OpenPosition.id),
            func.count(OpenPosition.delta_usd),
        ))).one()
        book = BookGreeks(float(r[0]), float(r[1]), float(r[2]), int(r[3]), int(r[4]))
    except Exception:
        logger.exception("pretrade_book_read_failed")
        book = None

    # caps from the same computation as the panel
    caps: Caps | None
    try:
        live = await compute_live_caps(db)
        caps = Caps(
            delta_usd=live.caps.delta_usd, vega_usd_per_volpt=live.caps.vega_usd,
            gamma_usd_per_pip=live.caps.gamma_pip,
            max_notional_eur=limits.get("max_notional_eur", DEFAULT_MAX_NOTIONAL_EUR),
        )
    except Exception:
        logger.exception("pretrade_caps_failed")
        caps = None

    spot = await _spot(redis, payload)
    meta = payload.get("surface_meta") or {}
    arb = meta.get("butterfly_ok")
    inputs = GateInputs(
        halted=halted, halt_reason=halt_reason, live_mode=live_mode,
        risk_heartbeat_age_s=await _risk_heartbeat_age_s(redis),
        max_risk_heartbeat_age_s=limits.get("max_risk_heartbeat_age_seconds", DEFAULT_MAX_RISK_HEARTBEAT_AGE_S),
        ib_account_type=str(acct_type) if acct_type else None,
        required_account_type=_required_account_type(),
        book=book, trade=trade_greeks_from_preview(payload, spot), caps=caps,
        symbols=leg_symbols(payload), allowed_symbols=_allowed_symbols(),
        surface_age_s=float(payload["surface_age_seconds"]) if payload.get("surface_age_seconds") is not None else None,
        max_surface_age_s=limits.get("max_iv_data_age_seconds", 120.0),
        arb_ok=arb if isinstance(arb, bool) else None,
    )
    result = evaluate_pretrade(inputs)
    if not result.passed:
        logger.warning("pretrade_gate_refused failures=%s", ",".join(result.failures))
    return result
