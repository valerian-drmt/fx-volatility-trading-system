"""Pre-trade gate: fail CLOSED (#315, audit 03#7 / 03#8 / 03#9 / 04#10-13).

Before this gate the only enforced limit was a book-vega check against a
bootstrapped 0.0, the greek caps were computed for a panel and never checked,
nothing looked at the risk engine's heartbeat, the paper account was recorded
but never asserted, and there was no notional cap, no allow-list and no kill
switch. Every input here is something that can be unknown; an unknown input
is a FAILURE, never a pass: "we could not check" is not "it is fine".

Pure: the api collects the inputs (``api.orchestration.pretrade_gate``), this
module decides, the result lists every reason so the trader sees all of them
at once instead of fixing one and hitting the next.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BookGreeks:
    """Live book greeks in desk units (docs/vol-modeling/units.md)."""
    delta_usd: float
    vega_usd_per_volpt: float
    gamma_usd_per_pip: float
    n_positions: int
    n_priced: int
    unpriced: tuple[str, ...] = ()   # contracts held with no delta, named for the trader


@dataclass(frozen=True)
class TradeGreeks:
    """The candidate structure's incremental greeks, same units, at full size."""
    delta_usd: float
    vega_usd_per_volpt: float
    gamma_usd_per_pip: float
    notional_eur: float


@dataclass(frozen=True)
class Caps:
    delta_usd: float
    vega_usd_per_volpt: float
    gamma_usd_per_pip: float
    max_notional_eur: float


@dataclass(frozen=True)
class GateInputs:
    halted: bool | None                 # None = could not read the flag
    halt_reason: str | None
    live_mode: bool                     # paper/live submit vs mock
    risk_heartbeat_age_s: float | None  # None = no heartbeat
    max_risk_heartbeat_age_s: float
    ib_account_type: str | None         # 'paper' | 'live' | None
    required_account_type: str          # from TRADING_MODE
    book: BookGreeks | None             # None = book could not be read
    trade: TradeGreeks | None           # None = structure greeks unavailable
    caps: Caps | None                   # None = caps could not be computed
    symbols: tuple[str, ...]            # contract symbols of the legs
    allowed_symbols: tuple[str, ...]
    surface_age_s: float | None
    max_surface_age_s: float
    arb_ok: bool | None                 # surface meta butterfly check; None = not reported
    # The size of this order, in contracts, for the trader's own limit on it.
    order_contracts: float | None = None
    # Limits the TRADER set himself, by axis: delta, vega, gamma, notional,
    # contracts. They are the only limits: the code computes none, has no
    # warning level and asks for no confirmation (owner rule, 2026-11-13).
    hard_limits: dict[str, float] | None = None
    # A premium to pay, against what the account has.
    premium_to_pay_usd: float | None = None
    available_funds_usd: float | None = None
    net_liquidation_usd: float | None = None


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)


def evaluate_pretrade(i: GateInputs) -> GateResult:
    f: list[str] = []
    d: dict[str, object] = {}

    # 0. Kill switch, unknown = halted.
    if i.halted is None:
        f.append("halt_flag_unreadable")
    elif i.halted:
        f.append("trading_halted")
        d["halt_reason"] = i.halt_reason

    # 1. Symbols allow-list (every leg).
    bad = [s for s in i.symbols if s not in i.allowed_symbols]
    if not i.symbols or bad:
        f.append("symbol_not_allowed")
        d["symbols"] = list(i.symbols)
        d["allowed_symbols"] = list(i.allowed_symbols)

    if i.live_mode:
        # 2. Paper assertion: the session we trade on must be the account type
        #    TRADING_MODE promises. Unknown account = refuse.
        if i.ib_account_type is None or i.ib_account_type != i.required_account_type:
            f.append("account_type_mismatch")
            d["ib_account_type"] = i.ib_account_type
            d["required_account_type"] = i.required_account_type

        # 3. Risk engine liveness: the numbers below are its numbers.
        if i.risk_heartbeat_age_s is None or i.risk_heartbeat_age_s > i.max_risk_heartbeat_age_s:
            f.append("risk_engine_stale")
            d["risk_heartbeat_age_s"] = i.risk_heartbeat_age_s
            d["max_risk_heartbeat_age_s"] = i.max_risk_heartbeat_age_s

        # 4. Surface freshness (the IV behind every greek and limit price).
        if i.surface_age_s is None or i.surface_age_s > i.max_surface_age_s:
            f.append("surface_stale")
            d["surface_age_s"] = i.surface_age_s
            d["max_surface_age_s"] = i.max_surface_age_s

    # 5. Static-arbitrage flag from the surface meta: explicit False refuses.
    if i.arb_ok is False:
        f.append("surface_arbitrage")

    # ── Limits (owner's rule, 2026-11-13) ───────────────────────────────────
    # The application is not the trader. It computes no limit, no reference
    # level, no suggested size, and asks for no confirmation. What stops an
    # order is the market and the account: trading halted, the wrong account,
    # data that cannot be used, a premium the account cannot pay, and the
    # broker's own answer. The one exception is a limit the trader typed in
    # himself (``hard_limits``); none exists unless he sets it. ``i.caps``
    # (levels derived from the account's size) is not read here.
    hard = dict(i.hard_limits or {})

    def _check(axis: str, value: float) -> None:
        limit = hard.get(axis)
        if limit is not None and abs(value) > limit:
            f.append(f"cap_{axis}")

    # 5b. The size of this one order, against the trader's own limit if any.
    if i.order_contracts is not None:
        d["order_contracts"] = i.order_contracts
        d["max_order_contracts"] = hard.get("contracts")
        _check("contracts", i.order_contracts)

    # 5c. A premium larger than the whole account cannot be paid: a fact.
    #     Margin is the broker's decision and is left to the broker.
    if i.premium_to_pay_usd is not None and i.premium_to_pay_usd > 0:
        d["premium_to_pay_usd"] = round(i.premium_to_pay_usd, 2)
        if i.net_liquidation_usd is not None and i.premium_to_pay_usd > i.net_liquidation_usd:
            f.append("insufficient_funds")
            d["net_liquidation_usd"] = round(i.net_liquidation_usd, 2)

    # 6. The book after the trade: shown on the ticket, judged only against
    #    the trader's own limits.
    if i.book is None:
        f.append("book_unavailable")
    elif i.trade is None:
        f.append("trade_greeks_unavailable")
    else:
        if i.book.n_priced < i.book.n_positions:
            # information for the ticket: the book figures miss a position
            d["book_unpriced"] = i.book.n_positions - i.book.n_priced
            d["book_unpriced_lines"] = list(i.book.unpriced)
        post_delta = i.book.delta_usd + i.trade.delta_usd
        post_vega = i.book.vega_usd_per_volpt + i.trade.vega_usd_per_volpt
        post_gamma = i.book.gamma_usd_per_pip + i.trade.gamma_usd_per_pip
        d["book"] = {
            "delta_usd": round(i.book.delta_usd, 2), "vega_usd_per_volpt": round(i.book.vega_usd_per_volpt, 2),
            "gamma_usd_per_pip": round(i.book.gamma_usd_per_pip, 2),
        }
        d["trade"] = {
            "delta_usd": round(i.trade.delta_usd, 2), "vega_usd_per_volpt": round(i.trade.vega_usd_per_volpt, 2),
            "gamma_usd_per_pip": round(i.trade.gamma_usd_per_pip, 2), "notional_eur": round(i.trade.notional_eur, 2),
        }
        d["post_trade"] = {
            "delta_usd": round(post_delta, 2), "vega_usd_per_volpt": round(post_vega, 2),
            "gamma_usd_per_pip": round(post_gamma, 2), "notional_eur": round(i.trade.notional_eur, 2),
        }
        # the trader's own limits, None where he set none
        d["caps"] = {
            "delta_usd": hard.get("delta"), "vega_usd_per_volpt": hard.get("vega"),
            "gamma_usd_per_pip": hard.get("gamma"), "max_notional_eur": hard.get("notional"),
        }
        d["hard_limits"] = sorted(hard)
        _check("delta", post_delta)
        _check("vega", post_vega)
        _check("gamma", post_gamma)
        _check("notional", i.trade.notional_eur)

    return GateResult(passed=not f, failures=f, details=d)
