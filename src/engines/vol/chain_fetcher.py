"""Async port of the monolith's FOP chain traversal with bounded parallelism.

Replaces the synthetic sandbox stub in ``engines.vol.main`` by a real
call into IB :

- ``discover_chains(ib, target_dtes)`` picks the closest EUU (EUR futures
  option) chain for each target DTE, via ``reqContractDetailsAsync`` +
  ``reqSecDefOptParamsAsync``.
- ``scan_one_tenor(ib, chain, F)`` qualifies strikes around ATM, fires
  ``reqMktData(contract, \"100\", ...)`` to get modelGreeks, waits for
  the Greeks to populate, collects ``(delta, iv, strike)`` triples and
  cancels market data.
- ``scan_all_tenors_concurrent(ib, F, chains, max_concurrent=3)`` runs
  ``scan_one_tenor`` on each chain in parallel behind a semaphore to
  stay within IB's live-subscription cap (paper = ~100 concurrent).

Ported from ``src/engines/vol_engine.py`` (monolith v1) :
``_discover_chains`` / ``_qualify_contracts`` / ``_scan_iv``. Key async
wins over the monolith :

- ``reqContractDetailsAsync`` + ``reqSecDefOptParamsAsync`` let us fan
  out contract qualification.
- ``asyncio.Semaphore(3)`` scans 3 tenors in parallel instead of 6
  sequential — typical speedup on paper gateway : 24-30s -> 8-10s.

Pure asyncio — the legacy Qt-gated design is gone.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Anchor-discovery targets — the listed expiries we try to qualify, spanning the
# CME monthly-serial range (≈1M..6M). Display pillars come from interpolating
# these (core.vol.tenors). The far quarterlies (9M/1Y) aren't listed on EUU, so
# we don't chase them. See docs/surface_tenor_pillars.md.
DEFAULT_TARGET_DTES: tuple[int, ...] = (30, 60, 90, 120, 150, 180)
DEFAULT_STRIKES_PER_SIDE: int = 18  # ATM ± 18 strikes per tenor
DEFAULT_MAX_CONCURRENT: int = 3
DEFAULT_GREEKS_WAIT_S: int = 12
DEFAULT_CANCEL_PAUSE_S: float = 0.5


def _safe(val: object) -> float | None:
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def tenor_label(dte: int) -> str:
    """Coarse label for a *listed* anchor expiry (logging / bucketing only — the
    display pillars are produced by core.vol.tenors interpolation)."""
    if dte <= 45:
        return "1M"
    if dte <= 75:
        return "2M"
    if dte <= 105:
        return "3M"
    if dte <= 135:
        return "4M"
    if dte <= 165:
        return "5M"
    if dte <= 210:
        return "6M"
    if dte <= 315:
        return "9M"
    return "1Y"


def ensure_delayed_market_data(ib: Any) -> None:
    """Force delayed market-data mode (type 3).

    Without this, paper accounts without a CME Real-Time subscription
    get no ``modelGreeks`` (tick type 100) — reqMktData returns only
    bid/ask, and the IV pillars stay empty. Delayed (20-min) works on
    every paper account and is sufficient for surface estimation.
    """
    try:
        ib.reqMarketDataType(3)
        logger.info("reqMarketDataType(3) — delayed market data enabled")
    except Exception:
        logger.exception("reqMarketDataType_failed")


async def discover_chains(
    ib: Any,
    target_dtes: tuple[int, ...] | list[int] = DEFAULT_TARGET_DTES,
    trading_class: str = "EUU",
) -> list[dict[str, Any]]:
    """Return the EUU option chain closest to each target DTE."""
    from ib_insync import Contract

    fut = Contract(symbol="EUR", secType="FUT", exchange="CME", currency="USD")
    details = await ib.reqContractDetailsAsync(fut)
    now = datetime.now()
    futures: list[tuple[int, Any]] = []
    for d in details:
        exp = d.contract.lastTradeDateOrContractMonth
        try:
            exp_date = (
                datetime.strptime(exp, "%Y%m%d")
                if len(exp) == 8
                else datetime.strptime(exp, "%Y%m")
            )
        except ValueError:
            continue
        dte = (exp_date - now).days
        if dte >= 7:
            futures.append((dte, d.contract))
    futures.sort(key=lambda x: x[0])

    chain_data: dict[str, dict[str, Any]] = {}
    for _dte, fut_c in futures[:8]:
        chains = await ib.reqSecDefOptParamsAsync("EUR", "CME", "FUT", fut_c.conId)
        for ch in chains:
            if ch.tradingClass != trading_class:
                continue
            for exp in sorted(ch.expirations):
                try:
                    exp_date = datetime.strptime(exp, "%Y%m%d")
                except ValueError:
                    continue
                dte_fop = (exp_date - now).days
                if dte_fop < 10:
                    continue
                entry = chain_data.setdefault(
                    exp,
                    {
                        "expiry": exp,
                        "dte": dte_fop,
                        "strikes": set(),
                        "multipliers": set(),
                        "exchange": ch.exchange,
                    },
                )
                entry["strikes"].update(ch.strikes)
                entry["multipliers"].add(str(ch.multiplier))

    euu: list[dict[str, Any]] = []
    for data in chain_data.values():
        data["strikes"] = sorted(data["strikes"])
        data["multipliers"] = sorted(data["multipliers"])
        euu.append(data)
    euu.sort(key=lambda x: x["dte"])

    # Assign each target DTE to the closest chain. Each chain gets a stable
    # ``label`` corresponding to the target it represents, which avoids the
    # bucket-collision bug where two chains mapped to the same "NM" bucket
    # (e.g. 106d and 134d both -> "4M") and overwrote each other in the
    # final dict keyed by label.
    selected: list[dict[str, Any]] = []
    used_expiries: set[str] = set()
    for target in target_dtes:
        if not euu:
            break
        label = f"{target // 30}M"
        # Pick the closest chain that isn't already taken by a previous target.
        remaining = [c for c in euu if c["expiry"] not in used_expiries]
        if not remaining:
            break
        best = min(remaining, key=lambda x: abs(x["dte"] - target))
        best = {**best, "label": label}
        used_expiries.add(best["expiry"])
        selected.append(best)
    logger.info(
        "discover_chains: selected %d tenors (%s)",
        len(selected),
        ",".join(f"{c['label']}({c['dte']}d)" for c in selected),
    )
    return selected


async def _qualify_one(ib: Any, fop: Any) -> Any | None:
    details = await ib.reqContractDetailsAsync(fop)
    return details[0].contract if details else None


async def _qualify_tenor_strikes(
    ib: Any, chain: dict[str, Any], F: float,
    n_side: int = DEFAULT_STRIKES_PER_SIDE,
) -> dict[float, dict[str, Any]]:
    """Qualify ~2*n_side strikes around F, both C and P sides."""
    from ib_insync import Contract

    expiry = chain["expiry"]
    strikes = chain["strikes"]
    multipliers = chain["multipliers"]
    atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - F))
    lo = max(0, atm_idx - n_side)
    hi = min(len(strikes) - 1, atm_idx + n_side)
    scan_strikes = strikes[lo : hi + 1]

    async def _try_multipliers(K: float, right: str) -> tuple[float, str, Any | None]:
        for mult in multipliers:
            fop = Contract(
                symbol="EUR", secType="FOP", exchange=chain["exchange"],
                currency="USD", lastTradeDateOrContractMonth=expiry,
                strike=K, right=right, multiplier=mult, tradingClass="EUU",
            )
            qc = await _qualify_one(ib, fop)
            if qc is not None:
                return K, right, qc
        return K, right, None

    tasks = [_try_multipliers(K, r) for K in scan_strikes for r in ("C", "P")]
    results = await asyncio.gather(*tasks)

    qualified: dict[float, dict[str, Any]] = {}
    for K, right, contract in results:
        if contract is not None:
            qualified.setdefault(K, {})[right] = contract
    return qualified


async def scan_one_tenor(
    ib: Any, chain: dict[str, Any], F: float,
    wait_greeks_s: int = DEFAULT_GREEKS_WAIT_S,
    min_strikes: int = 5,
) -> list[tuple[float, float, float]]:
    """Return (delta, iv, strike) triples for one tenor."""
    t0 = time.monotonic()
    qualified = await _qualify_tenor_strikes(ib, chain, F)
    if not qualified:
        return []

    active: list[tuple[float, str, Any, Any]] = []
    for K, rights in qualified.items():
        for right, contract in rights.items():
            ticker = ib.reqMktData(contract, "100", False, False)
            active.append((K, right, contract, ticker))

    await asyncio.sleep(wait_greeks_s)

    raw: dict[tuple[float, str], dict[str, float | None]] = {}
    n_with_bid = 0
    n_with_greeks = 0
    n_with_iv = 0
    for K, right, contract, ticker in active:
        bid = _safe(getattr(ticker, "bid", None))
        if bid is not None and bid > 0:
            n_with_bid += 1
        greeks = getattr(ticker, "modelGreeks", None)
        if greeks is not None:
            n_with_greeks += 1
        iv = _safe(getattr(greeks, "impliedVol", None)) if greeks else None
        delta = _safe(getattr(greeks, "delta", None)) if greeks else None
        if iv and iv > 0:
            n_with_iv += 1
            raw[(K, right)] = {"iv": iv, "delta": delta}
        try:
            ib.cancelMktData(contract)
        except Exception:
            logger.exception("cancelMktData_failed")
    logger.info(
        "scan_one_tenor %s : %d contracts / %d bid / %d greeks / %d iv>0",
        chain["expiry"], len(active), n_with_bid, n_with_greeks, n_with_iv,
    )

    await asyncio.sleep(DEFAULT_CANCEL_PAUSE_S)

    triples: list[tuple[float, float, float]] = []
    for K in sorted({k for (k, _) in raw}):
        c_data = raw.get((K, "C"))
        p_data = raw.get((K, "P"))
        iv_c = c_data["iv"] if c_data else None
        iv_p = p_data["iv"] if p_data else None
        d_c = c_data["delta"] if c_data else None
        d_p = p_data["delta"] if p_data else None
        iv_merged = (iv_c + iv_p) / 2.0 if iv_c and iv_p else (iv_c or iv_p)
        if not iv_merged:
            continue
        # Call delta ≈ put delta + 1 (put deltas are negative in IB)
        delta = d_c if d_c is not None else (1.0 + d_p if d_p is not None else None)
        if delta is not None:
            triples.append((float(delta), float(iv_merged), float(K)))

    if len(triples) < min_strikes:
        logger.info(
            "scan_one_tenor: %s dropped (%d < min_strikes=%d, took %.1fs)",
            chain["expiry"], len(triples), min_strikes, time.monotonic() - t0,
        )
        return []
    logger.info(
        "scan_one_tenor: %s ok (%d strikes, took %.1fs)",
        chain["expiry"], len(triples), time.monotonic() - t0,
    )
    return triples


async def scan_all_tenors_concurrent(
    ib: Any, F: float, chains: list[dict[str, Any]],
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> dict[str, list[tuple[float, float, float]]]:
    """Run scan_one_tenor in parallel, bounded by a semaphore."""
    sem = asyncio.Semaphore(max_concurrent)
    t0 = time.monotonic()

    async def _one(chain: dict[str, Any]) -> tuple[str, list[tuple[float, float, float]]]:
        async with sem:
            triples = await scan_one_tenor(ib, chain, F)
            # Prefer the label assigned by discover_chains (guaranteed
            # unique per target DTE) over re-bucketing by age.
            label = chain.get("label") or tenor_label(chain["dte"])
            return label, triples

    results = await asyncio.gather(*[_one(ch) for ch in chains])
    out: dict[str, list[tuple[float, float, float]]] = {}
    for label, triples in results:
        if triples:
            out[label] = triples
    logger.info(
        "scan_all_tenors_concurrent: %d tenors in %.1fs (concurrency=%d)",
        len(out), time.monotonic() - t0, max_concurrent,
    )
    return out
