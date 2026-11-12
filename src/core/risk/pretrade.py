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

    # 6. Caps on the POST-trade book, every axis, plus notional.
    if i.caps is None:
        f.append("caps_unavailable")
    elif i.book is None:
        f.append("book_unavailable")
    elif i.trade is None:
        f.append("trade_greeks_unavailable")
    else:
        if i.book.n_priced < i.book.n_positions:
            f.append("book_partially_priced")
            d["book_unpriced"] = i.book.n_positions - i.book.n_priced
            d["book_unpriced_lines"] = list(i.book.unpriced)
        post_delta = i.book.delta_usd + i.trade.delta_usd
        post_vega = i.book.vega_usd_per_volpt + i.trade.vega_usd_per_volpt
        post_gamma = i.book.gamma_usd_per_pip + i.trade.gamma_usd_per_pip
        d["post_trade"] = {
            "delta_usd": round(post_delta, 2), "vega_usd_per_volpt": round(post_vega, 2),
            "gamma_usd_per_pip": round(post_gamma, 2), "notional_eur": round(i.trade.notional_eur, 2),
        }
        d["caps"] = {
            "delta_usd": round(i.caps.delta_usd, 2), "vega_usd_per_volpt": round(i.caps.vega_usd_per_volpt, 2),
            "gamma_usd_per_pip": round(i.caps.gamma_usd_per_pip, 2), "max_notional_eur": i.caps.max_notional_eur,
        }
        if i.caps.delta_usd <= 0 or i.caps.vega_usd_per_volpt <= 0 or i.caps.gamma_usd_per_pip <= 0:
            f.append("caps_not_established")   # no NAV history / no spot yet: refuse
        else:
            if abs(post_delta) > i.caps.delta_usd:
                f.append("cap_delta")
            if abs(post_vega) > i.caps.vega_usd_per_volpt:
                f.append("cap_vega")
            if abs(post_gamma) > i.caps.gamma_usd_per_pip:
                f.append("cap_gamma")
        if i.trade.notional_eur > i.caps.max_notional_eur:
            f.append("cap_notional")

    return GateResult(passed=not f, failures=f, details=d)
