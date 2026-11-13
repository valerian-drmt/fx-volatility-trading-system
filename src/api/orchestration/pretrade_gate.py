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
from bus import keys
from bus.spot_cache import parse_spot
from core.risk.pretrade import BookGreeks, Caps, GateInputs, GateResult, TradeGreeks, evaluate_pretrade
from core.units import EUR_FOP_MULTIPLIER, PIP_SIZE
from core.vol.cadence import surface_age_limit_s
from persistence.halt import get_halt
from persistence.models import AppConfigScalar, IbConnectionState, OpenPosition

logger = logging.getLogger(__name__)

DEFAULT_MAX_NOTIONAL_EUR = 5_000_000.0
DEFAULT_MAX_RISK_HEARTBEAT_AGE_S = 30.0
DEFAULT_WARN_FRACTION = 0.8
DEFAULT_MAX_ORDER_CONTRACTS = 200.0


# The limits a trader sets himself, in the ``risk`` config namespace. Absent =
# no hard limit on that axis: the level the code derives is then only a
# reference the trade may exceed with a confirmation.
TRADER_LIMIT_KEYS = {
    "delta": "hard_max_delta_usd",
    "vega": "hard_max_vega_usd_per_volpt",
    "gamma": "hard_max_gamma_usd_per_pip",
    "notional": "hard_max_notional_eur",
    "contracts": "hard_max_order_contracts",
}


def trader_limits(limits: dict[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for axis, key in TRADER_LIMIT_KEYS.items():
        v = limits.get(key)
        if v is not None and float(v) > 0:
            out[axis] = float(v)
    return out


def _premium_to_pay(payload: dict[str, Any]) -> float | None:
    """Premium and commission the whole order pays out; None for a credit.

    ``pricing.premium_paid_usd`` is per unit of the structure and carries the
    sign (+ = paid); ``costs.total_trade_cost_usd`` is the sized amount."""
    try:
        per_unit = float((payload.get("pricing") or {}).get("premium_paid_usd") or 0.0)
        total = float((payload.get("costs") or {}).get("total_trade_cost_usd") or 0.0)
    except (TypeError, ValueError):
        return None
    return total if (per_unit > 0 and total > 0) else None


async def _account_funds(db: AsyncSession, spot: float | None = None) -> tuple[float | None, float | None]:
    """(funds available, net liquidation) from the latest account snapshot;
    (None, None) when there is none, and then nothing is concluded.

    IB reports the snapshot in the account's base currency, which the row
    does not record. The refusal must be a fact in either case, so the net
    liquidation is taken at the larger of its two readings (as USD, or as EUR
    converted at spot): a premium above it cannot be paid whatever the base."""
    try:
        from persistence.rollups import latest_account_row

        row = await latest_account_row(db)
    except Exception:
        return None, None
    if not row:
        return None, None

    def _num(v: Any) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    net_liq = _num(row.get("net_liq_usd"))
    if net_liq is not None and spot and spot > 1.0:
        net_liq = net_liq * float(spot)
    return _num(row.get("excess_liquidity")), net_liq


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
    # A package is judged as it will sit in the book: with its hedge on (#482).
    hedge = payload.get("hedge") or {}
    if hedge.get("qty"):
        try:
            delta_usd = float(hedge["residual_delta_usd"])
        except (KeyError, TypeError, ValueError):
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
    surface_age_s: float | None = None,
    log_refusal: bool = True,
    confirmed: bool = False,
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
        unpriced: tuple[str, ...] = ()
        if int(r[4]) < int(r[3]):
            unpriced = tuple(str(s) for s in (await db.execute(
                select(OpenPosition.structure).where(OpenPosition.delta_usd.is_(None))
                .order_by(OpenPosition.structure).limit(20)
            )).scalars().all())
        book = BookGreeks(float(r[0]), float(r[1]), float(r[2]), int(r[3]), int(r[4]), unpriced)
    except Exception:
        logger.exception("pretrade_book_read_failed")
        book = None

    # No level is derived from the account's size here (owner's rule,
    # 2026-11-13): the gate judges facts and the trader's own limits only.
    caps: Caps | None = None

    spot = await _spot(redis, payload)
    funds = await _account_funds(db, spot)
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
        # The caller's age NOW of the latest surface when it has one, else the
        # age recorded on the preview (#480); the limit follows the cadence.
        surface_age_s=(
            surface_age_s if surface_age_s is not None
            else (float(payload["surface_age_seconds"]) if payload.get("surface_age_seconds") is not None else None)
        ),
        max_surface_age_s=surface_age_limit_s(limits.get("max_iv_data_age_seconds")),
        arb_ok=arb if isinstance(arb, bool) else None,
        warn_fraction=float(limits.get("limit_warn_fraction", DEFAULT_WARN_FRACTION)),
        confirmed=confirmed,
        order_contracts=sum(
            abs(float(leg.get("qty") or 0)) for leg in ((payload.get("structure") or {}).get("legs") or [])
            if (leg.get("contract_type") or "") != "spot"
        ),
        max_order_contracts=None,
        hard_limits=trader_limits(limits),
        premium_to_pay_usd=_premium_to_pay(payload),
        available_funds_usd=funds[0], net_liquidation_usd=funds[1],
    )
    result = evaluate_pretrade(inputs)
    if not result.passed and log_refusal:
        logger.warning("pretrade_gate_refused failures=%s", ",".join(result.failures))
    return result


# What each limit is called on the ticket, and the keys of the gate's details.
_BUDGET_ROWS = (
    ("delta", "Delta", "USD", "delta_usd", "delta_usd", "cap_delta"),
    ("vega", "Vega", "USD per vol point", "vega_usd_per_volpt", "vega_usd_per_volpt", "cap_vega"),
    ("gamma", "Gamma", "USD per pip", "gamma_usd_per_pip", "gamma_usd_per_pip", "cap_gamma"),
    ("notional", "Notional", "EUR", "notional_eur", "max_notional_eur", "cap_notional"),
)
# Failures that depend on the session at submit time, not on the trade: the
# preview does not announce them.
_SESSION_ONLY = frozenset({"account_type_mismatch", "risk_engine_stale", "surface_stale"})


def max_qty_within_limits(rows: list[dict[str, Any]], qty: int) -> int | None:
    """The largest size of this same trade that stays inside the limits the
    trader set himself (``rows`` = those limits only).

    Greeks are linear in the size, so per limit: the book before, plus n
    times the trade's contribution per unit, must stay within the cap. 0 when
    even one unit does not fit; None when a limit is unknown. The delta of a
    hedged package is its residual, which does not scale with the size, so it
    does not bind here."""
    if qty <= 0:
        return None
    best: float | None = None
    for r in rows:
        cap, trade = r.get("cap"), r.get("trade")
        if cap is None or trade is None:
            return None
        if r["key"] == "delta" and r.get("hedged"):
            continue
        unit = float(trade) / qty
        if abs(unit) < 1e-12:
            continue
        before = float(r.get("before") or 0.0)
        # |before + n * unit| <= cap, n >= 0, on the side the trade pushes to
        room = float(cap) - (before if unit > 0 else -before)
        n = room / abs(unit)
        best = n if best is None else min(best, n)
    if best is None:
        return None
    return max(0, int(best + 1e-9))


def budget_from_gate(result: GateResult, qty: int | None = None, hedged: bool = False) -> dict[str, Any]:
    """The gate's verdict as the ticket shows it (#479): per limit, the cap,
    the book before, the trade, the book after, and the share of the cap used
    after the trade. Same numbers as the decision, because it IS the decision."""
    d = result.details
    caps = d.get("caps") or {}
    book = d.get("book") or {}
    trade = d.get("trade") or {}
    post = d.get("post_trade") or {}
    rows: list[dict[str, Any]] = []
    for key, label, unit, value_key, cap_key, code in _BUDGET_ROWS:
        cap = caps.get(cap_key)
        after = post.get(value_key)
        used = (abs(float(after)) / float(cap)) if (cap and after is not None and float(cap) > 0) else None
        rows.append({
            "hedged": hedged and key == "delta",
            "key": key, "label": label, "unit": unit, "cap": cap,
            "before": book.get(value_key) if key != "notional" else None,
            "trade": trade.get(value_key), "after": after,
            "used": round(used, 4) if used is not None else None,
            # cap = a limit the trader set himself, None otherwise.
            # breached = over that limit: refused.
            "breached": code in result.failures,
            "hard": key in (d.get("hard_limits") or []),
        })
    return {
        "rows": rows if (d.get("post_trade") or d.get("trade")) else [],
        "warnings": list(result.warnings),
        "warn_fraction": d.get("warn_fraction"),
        # a size is suggested only when one of the trader's own limits refuses
        "max_qty": (
            max_qty_within_limits([r for r in rows if r["hard"]], qty)
            if (qty and any(r["breached"] for r in rows)) else None
        ),
        "qty": qty,
        "failures": [f for f in result.failures if f not in _SESSION_ONLY],
        "details": {k: d[k] for k in (
            "book_unpriced", "book_unpriced_lines", "symbols", "allowed_symbols", "halt_reason",
            "premium_to_pay_usd", "net_liquidation_usd", "available_funds_usd",
            "order_contracts", "max_order_contracts", "hard_limits",
        ) if k in d},
    }


async def preview_risk_budget(
    db: AsyncSession, redis: aioredis.Redis | None, payload: dict[str, Any],
) -> dict[str, Any]:
    """The gate, run on a preview: what the trade would use of each limit and
    which limits it would break. ``live_mode=False`` leaves out the checks
    that only make sense at submit (account, heartbeat, surface age)."""
    # confirmed=True: a preview shows the warning zone, it does not refuse on it
    result = await run_pretrade_gate(
        db, redis, payload=payload, live_mode=False, log_refusal=False, confirmed=True,
    )
    qty = int((payload.get("sizing") or {}).get("base_qty") or 0) or None
    return budget_from_gate(result, qty=qty, hedged=bool((payload.get("hedge") or {}).get("qty")))


async def run_spot_order_gate(
    db: AsyncSession, redis: aioredis.Redis | None, *, side: str, qty_eur: float,
) -> GateResult:
    """The pre-trade checks for a spot FX order (#491): the kill switch, a
    book that can be read, and the delta and notional limits the TRADER set,
    if any. Levels the code derives never refuse (owner's rule, 2026-11-13).

    An order that brings the book's delta closer to zero is never refused by
    the delta limit: reducing risk must stay possible when the book is over
    it.
    """
    spot = await _spot(redis, {})
    signed_eur = float(qty_eur) if side.upper() == "BUY" else -float(qty_eur)
    payload = {
        "spot": spot,
        "greeks_net": {
            "delta_unhedged": signed_eur * float(spot or 0.0),
            "vega_usd_per_volpt": 0.0, "gamma_usd_per_pip2": 0.0,
        },
        # notional in EUR = contracts x 125,000: express the order in contracts
        "structure": {"legs": [{"contract_type": "spot", "qty": abs(float(qty_eur)) / EUR_FOP_MULTIPLIER}]},
    }
    result = await run_pretrade_gate(db, redis, payload=payload, live_mode=False, log_refusal=False, confirmed=True)
    failures = list(result.failures)
    book = (result.details.get("book") or {}) if isinstance(result.details, dict) else {}
    post = (result.details.get("post_trade") or {}) if isinstance(result.details, dict) else {}
    try:
        reduces = abs(float(post["delta_usd"])) <= abs(float(book["delta_usd"]))  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        reduces = False
    if reduces and "cap_delta" in failures:
        failures.remove("cap_delta")
    if failures:
        logger.warning("spot_order_gate_refused failures=%s", ",".join(failures))
    return GateResult(passed=not failures, failures=failures, details=result.details, warnings=result.warnings)

