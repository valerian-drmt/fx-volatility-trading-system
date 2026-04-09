"""
Data feed — Thread 1 (main thread).
Handles all IB data collection: spot tick streaming, FOP chain discovery,
and account/portfolio snapshots.
"""
from __future__ import annotations

import math
import time
from datetime import date, timedelta
from typing import Any

from ib_insync import Contract

from services.ib_client import IBClient

# ══════════════════════════════════════════════════════════════════════════════
# MarketDataWorker — spot tick polling
# ══════════════════════════════════════════════════════════════════════════════


class MarketDataWorker:
    NO_TICK_CHECK_SECONDS = 2.0
    NO_TICK_CHECK_REPETITIONS = 3

    # Initialize market-data polling state.
    def __init__(
        self,
        ib_client: IBClient,
        interval_ms: int = 100,
        snapshot_interval_ms: int = 750,
        orders_client: IBClient | None = None,
        portfolio_client: IBClient | None = None,
        status_client: IBClient | None = None,
    ) -> None:
        self.ib_client = ib_client
        self._orders_client = orders_client if orders_client is not None else ib_client
        self._portfolio_client = portfolio_client if portfolio_client is not None else ib_client
        self._status_client = status_client if status_client is not None else ib_client
        self._interval_ms = max(25, int(interval_ms))
        self._snapshot_interval_ms = max(100, int(snapshot_interval_ms))
        self._last_snapshot_monotonic = 0.0
        self._no_tick_check_started_at: float | None = None
        self._no_tick_check_count = 0
        self._no_tick_warning_emitted = False
        self._has_received_stream_ticks = False

    # Collect open-orders from cached snapshot (no active IB request).
    @staticmethod
    def _collect_open_orders(orders_client: IBClient) -> list[object]:
        try:
            open_orders = orders_client.get_open_orders_snapshot() or []
        except Exception:
            open_orders = []
        return open_orders if isinstance(open_orders, list) else []

    # Collect recent fills from cached snapshot (no active IB request).
    @staticmethod
    def _collect_recent_orders(orders_client: IBClient) -> list[object]:
        try:
            recent_orders = orders_client.get_fills_snapshot() or []
        except Exception:
            recent_orders = []
        return recent_orders if isinstance(recent_orders, list) else []

    # Poll IB state once and return a normalized payload for the UI.
    def poll_once(self) -> dict:
        messages: list[str] = []

        status = self._status_client.get_status_snapshot()
        connection_state = self._status_client.get_connection_state()
        connected = connection_state == "connected"
        now = time.monotonic()

        ticks = self.ib_client.process_messages() if connected else []
        if not isinstance(ticks, list):
            ticks = []
        else:
            ticks = [tick for tick in ticks if isinstance(tick, dict)]

        need_snapshot = False
        if connected:
            need_snapshot = (now - self._last_snapshot_monotonic) * 1000 >= self._snapshot_interval_ms

        if connected:
            if ticks:
                if self._no_tick_warning_emitted:
                    messages.append("[INFO][market_data] tick stream resumed.")
                self._has_received_stream_ticks = True
                self._no_tick_check_started_at = None
                self._no_tick_check_count = 0
                self._no_tick_warning_emitted = False
            else:
                # Startup sanity check: only run no-tick tests until first tick is seen.
                if not self._has_received_stream_ticks:
                    if self._no_tick_check_started_at is None:
                        self._no_tick_check_started_at = now
                    elif not self._no_tick_warning_emitted:
                        no_tick_seconds = now - self._no_tick_check_started_at
                        if no_tick_seconds >= self.NO_TICK_CHECK_SECONDS:
                            self._no_tick_check_count += 1
                            self._no_tick_check_started_at = now
                            check_position = self._no_tick_check_count
                            if check_position >= self.NO_TICK_CHECK_REPETITIONS:
                                messages.append(
                                    f"[WARN][market_data] no ticks received "
                                    f"(test {self.NO_TICK_CHECK_REPETITIONS}/{self.NO_TICK_CHECK_REPETITIONS}); "
                                    "market may be closed or data is unavailable for this symbol."
                                )
                                self._no_tick_warning_emitted = True
                            else:
                                messages.append(
                                    f"[INFO][market_data] no ticks received "
                                    f"(test {check_position}/{self.NO_TICK_CHECK_REPETITIONS})."
                                )
                else:
                    self._no_tick_check_started_at = None
                    self._no_tick_check_count = 0
                    self._no_tick_warning_emitted = False
        else:
            self._no_tick_check_started_at = None
            self._no_tick_check_count = 0
            self._no_tick_warning_emitted = False
            self._has_received_stream_ticks = False

        orders_payload = None
        portfolio_payload = None
        if need_snapshot:
            open_orders = self._collect_open_orders(self._orders_client)
            fills = self._collect_recent_orders(self._orders_client)
            summary, positions = self._portfolio_client.get_portfolio_snapshot()
            orders_payload = {"open_orders": open_orders, "fills": fills}
            portfolio_payload = {"summary": summary, "positions": positions}
            self._last_snapshot_monotonic = now

        return {
            "status": {
                "connection_state": connection_state,
                "mode": status.get("mode", "--"),
                "env": status.get("env", "--"),
                "client_id": status.get("client_id", "--"),
                "account": status.get("account", "--"),
            },
            "ticks": ticks,
            "orders_payload": orders_payload,
            "portfolio_payload": portfolio_payload,
            "messages": messages,
        }


# ══════════════════════════════════════════════════════════════════════════════
# VolDataCollector — FOP chain discovery & streaming subscription
# ══════════════════════════════════════════════════════════════════════════════

# EUR CME futures options defaults
SYMBOL = "EUR"
EXCHANGE = "CME"
CURRENCY = "USD"
MULTIPLIER = "125000"
MAX_OTM_PCT = 0.08
MAX_STRIKES_PER_SIDE = 5  # 5 above ATM + 5 below = 10 per tenor
NUM_FRONT_CONTRACTS = 2  # discover the 2 nearest quarterly futures


class VolDataCollector:
    def __init__(
        self,
        ib_client: IBClient,
        target_expirations: dict[str, dict] | None = None,
    ) -> None:
        self.ib_client = ib_client
        self._targets: dict[str, dict] = target_expirations or {}
        self._chain_cache: dict[str, list[float]] = {}
        self._fop_tickers: dict[tuple[str, float, str], Any] = {}
        self._subscribed = False

    def _discover_front_contracts(self) -> dict[str, dict]:
        """Find the N nearest quarterly EUR futures via IB and build targets dict."""
        fut = Contract()
        fut.symbol = SYMBOL
        fut.secType = "FUT"
        fut.exchange = EXCHANGE
        fut.currency = CURRENCY

        print("[VOL_COLLECTOR] Searching for available EUR futures on CME...")
        details_list = self.ib_client.ib.reqContractDetails(fut)
        if not details_list:
            print("[VOL_COLLECTOR] WARNING: no EUR futures found on CME")
            return {}

        QUARTERLY_MONTHS = {3, 6, 9, 12}
        MIN_DTE = 14
        today = date.today()
        min_exp = (today + timedelta(days=MIN_DTE)).strftime("%Y%m%d")

        valid = []
        skipped = []
        for d in details_list:
            exp = d.contract.lastTradeDateOrContractMonth
            if exp < min_exp:
                skipped.append(f"{exp}(too_soon)")
                continue
            try:
                month = int(exp[4:6])
            except (ValueError, IndexError):
                continue
            if month not in QUARTERLY_MONTHS:
                skipped.append(f"{exp}(monthly)")
                continue
            valid.append(d)
        valid.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)

        print(f"[VOL_COLLECTOR] Found {len(valid)} quarterly EUR futures (skipped: {skipped[:8]})")
        for d in valid[:6]:
            c = d.contract
            print(f"  {c.lastTradeDateOrContractMonth} conId={c.conId} localSymbol={c.localSymbol}")

        targets = {}
        for i, d in enumerate(valid[:NUM_FRONT_CONTRACTS]):
            exp = d.contract.lastTradeDateOrContractMonth
            try:
                exp_date = date(int(exp[:4]), int(exp[4:6]), int(exp[6:8]))
            except (ValueError, IndexError):
                exp_date = today + timedelta(days=90 * (i + 1))
            t_years = max((exp_date - today).days / 365.0, 0.01)
            tenor = f"{max(1, round(t_years * 12))}M"
            targets[exp] = {"tenor": tenor, "T": round(t_years, 4), "conId": d.contract.conId}
            print(f"[VOL_COLLECTOR] Target #{i+1}: {exp} tenor={tenor} T={t_years:.4f} conId={d.contract.conId}")

        return targets

    def discover_chains(self, spot: float) -> bool:
        """Discover available strikes for each target expiry via reqSecDefOptParams."""
        print(f"[VOL_COLLECTOR] discover_chains called, spot={spot:.5f}")
        if not self.ib_client.is_connected() or spot <= 0:
            print("[VOL_COLLECTOR] aborted: not connected or spot <= 0")
            return False

        if not self._targets:
            self._targets = self._discover_front_contracts()
            if not self._targets:
                return False

        print(f"[VOL_COLLECTOR] targets: {list(self._targets.keys())}")
        self._chain_cache.clear()
        lo = spot * (1 - MAX_OTM_PCT)
        hi = spot * (1 + MAX_OTM_PCT)
        print(f"[VOL_COLLECTOR] strike range: [{lo:.5f} - {hi:.5f}]")

        for expiry, meta in list(self._targets.items()):
            try:
                con_id = meta.get("conId")
                if not con_id:
                    fut = Contract()
                    fut.symbol = SYMBOL
                    fut.secType = "FUT"
                    fut.exchange = EXCHANGE
                    fut.currency = CURRENCY
                    fut.lastTradeDateOrContractMonth = expiry

                    print(f"[VOL_COLLECTOR] reqContractDetails for FUT {SYMBOL} expiry={expiry}...")
                    details = self.ib_client.ib.reqContractDetails(fut)
                    if not details:
                        print(f"[VOL_COLLECTOR] WARNING: no contract details for {expiry}")
                        continue
                    con_id = details[0].contract.conId

                print(f"[VOL_COLLECTOR] reqSecDefOptParams conId={con_id} for {expiry}...")
                chains = self.ib_client.ib.reqSecDefOptParams(
                    underlyingSymbol=SYMBOL,
                    futFopExchange=EXCHANGE,
                    underlyingSecType="FUT",
                    underlyingConId=con_id,
                )
                if not chains:
                    print(f"[VOL_COLLECTOR] WARNING: no option chain returned for {expiry}")
                    continue
                print(f"[VOL_COLLECTOR] got {len(chains)} chain(s) for {expiry}")

                chain = next((c for c in chains if c.exchange == EXCHANGE), chains[0])
                all_strikes = sorted(chain.strikes)
                fop_expirations = sorted(chain.expirations)
                print(f"[VOL_COLLECTOR] {expiry}: {len(all_strikes)} total strikes "
                      f"[{all_strikes[0]:.4f} - {all_strikes[-1]:.4f}]")
                print(f"[VOL_COLLECTOR] {expiry}: FOP expirations available: {fop_expirations[:6]}")

                if not fop_expirations:
                    print(f"[VOL_COLLECTOR] WARNING: no FOP expirations for {expiry}")
                    continue
                fop_exp = min(fop_expirations, key=lambda e: abs(int(e) - int(expiry)))
                print(f"[VOL_COLLECTOR] {expiry}: using FOP expiration {fop_exp}")

                filtered = [k for k in all_strikes if lo <= k <= hi]
                print(f"[VOL_COLLECTOR] {expiry}: {len(filtered)} strikes in range ±{MAX_OTM_PCT*100:.0f}%")

                if not filtered:
                    print(f"[VOL_COLLECTOR] WARNING: no strikes in range for {expiry}")
                    continue

                atm_idx = min(range(len(filtered)), key=lambda i: abs(filtered[i] - spot))
                start = max(0, atm_idx - MAX_STRIKES_PER_SIDE)
                end = min(len(filtered), atm_idx + MAX_STRIKES_PER_SIDE + 1)
                selected = filtered[start:end]

                self._chain_cache[fop_exp] = selected
                self._targets[fop_exp] = meta
                print(f"[VOL_COLLECTOR] {fop_exp}: selected {len(selected)} strikes: {selected}")
            except Exception as exc:
                print(f"[VOL_COLLECTOR] ERROR discover_chains({expiry}): {exc!r}")

        print(f"[VOL_COLLECTOR] discover_chains done: {len(self._chain_cache)} expiries cached")
        return bool(self._chain_cache)

    def subscribe_all(self) -> bool:
        """Subscribe to streaming market data for all discovered FOP contracts."""
        if not self.ib_client.is_connected() or not self._chain_cache:
            print("[VOL_COLLECTOR] subscribe_all: not connected or no chains")
            return False

        self.unsubscribe_all()
        count = 0
        for expiry, strikes in self._chain_cache.items():
            for strike in strikes:
                for right in ["C", "P"]:
                    key = (expiry, strike, right)
                    try:
                        fop = Contract()
                        fop.symbol = SYMBOL
                        fop.secType = "FOP"
                        fop.exchange = EXCHANGE
                        fop.currency = CURRENCY
                        fop.lastTradeDateOrContractMonth = expiry
                        fop.strike = strike
                        fop.right = right
                        fop.multiplier = MULTIPLIER

                        ticker = self.ib_client.ib.reqMktData(fop, "100", False, False)
                        self._fop_tickers[key] = ticker
                        count += 1
                    except Exception as exc:
                        print(f"[VOL_COLLECTOR] ERROR subscribe {key}: {exc!r}")

        self._subscribed = True
        print(f"[VOL_COLLECTOR] subscribed to {count} FOP tickers "
              f"({len(self._chain_cache)} expiries x strikes x C/P)")
        return count > 0

    def collect_snapshot(self, spot: float) -> dict | None:
        """Read current IV/greeks from all subscribed tickers. Returns input queue message."""
        if not self._subscribed or not self._fop_tickers or spot <= 0:
            return None

        chains: dict[str, dict] = {}
        n_with_iv = 0
        n_total = 0
        for (expiry, strike, right), ticker in self._fop_tickers.items():
            if expiry not in chains:
                meta = self._targets.get(expiry, {"tenor": expiry, "T": 0.25})
                chains[expiry] = {"tenor": meta["tenor"], "T": meta["T"], "rows": []}

            iv = getattr(ticker, "impliedVolatility", None)
            if not iv or (isinstance(iv, float) and math.isnan(iv)):
                iv = getattr(ticker, "lastGreeks", None)
                if iv:
                    iv = getattr(iv, "impliedVol", None)

            greeks = getattr(ticker, "modelGreeks", None) or getattr(ticker, "lastGreeks", None)
            delta = getattr(greeks, "delta", None) if greeks else None

            bid = self._safe_float(getattr(ticker, "bid", None))
            ask = self._safe_float(getattr(ticker, "ask", None))
            if bid is None:
                bid = self._safe_float(getattr(ticker, "delayedBid", None))
            if ask is None:
                ask = self._safe_float(getattr(ticker, "delayedAsk", None))

            raw_vol = getattr(ticker, "volume", 0)
            if not raw_vol or (isinstance(raw_vol, float) and math.isnan(raw_vol)):
                raw_vol = getattr(ticker, "delayedVolume", 0)
            try:
                volume = int(raw_vol) if raw_vol and not math.isnan(float(raw_vol)) else 0
            except (TypeError, ValueError):
                volume = 0

            iv_clean = float(iv) if iv and not math.isnan(iv) and iv > 0 else None
            delta_clean = float(delta) if delta and not math.isnan(delta) else None

            row = {
                "strike": strike,
                "right": right,
                "iv_raw": iv_clean,
                "bid": bid,
                "ask": ask,
                "volume": volume,
                "delta_ib": delta_clean,
            }
            chains[expiry]["rows"].append(row)
            n_total += 1
            if iv_clean is not None:
                n_with_iv += 1

        print(f"[VOL_COLLECTOR] snapshot: {n_with_iv}/{n_total} tickers have IV, spot={spot:.5f}")

        shown = 0
        for (expiry, strike, right), ticker in self._fop_tickers.items():
            if shown >= 3:
                break
            print(f"  [{expiry} K={strike:.4f} {right}] "
                  f"bid={getattr(ticker, 'bid', '?')} ask={getattr(ticker, 'ask', '?')} "
                  f"last={getattr(ticker, 'last', '?')} close={getattr(ticker, 'close', '?')} "
                  f"IV={getattr(ticker, 'impliedVolatility', '?')} "
                  f"delayedBid={getattr(ticker, 'delayedBid', '?')} "
                  f"delayedLast={getattr(ticker, 'delayedLast', '?')} "
                  f"modelGreeks={getattr(ticker, 'modelGreeks', '?')} "
                  f"lastGreeks={getattr(ticker, 'lastGreeks', '?')}")
            shown += 1

        if n_with_iv == 0:
            print("[VOL_COLLECTOR] WARNING: no tickers have IV data — "
                  "you need CME FOP market data subscription in IB Account Management")
            return None

        return {
            "type": "chain_data",
            "timestamp": time.time(),
            "spot": spot,
            "chains": chains,
        }

    def unsubscribe_all(self) -> None:
        """Cancel all FOP market data subscriptions."""
        for (expiry, strike, right), ticker in self._fop_tickers.items():
            contract = getattr(ticker, "contract", None)
            if contract is not None:
                try:
                    self.ib_client.ib.cancelMktData(contract)
                except Exception:
                    pass
        count = len(self._fop_tickers)
        self._fop_tickers.clear()
        self._subscribed = False
        if count:
            print(f"[VOL_COLLECTOR] unsubscribed {count} FOP tickers")

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            f = float(value)
            return f if not math.isnan(f) and f > 0 else None
        except (TypeError, ValueError):
            return None
