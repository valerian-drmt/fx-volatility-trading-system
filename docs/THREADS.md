# Threading Architecture

## Overview — Main Thread + 3 Worker Threads

| Thread | File | IB Client ID | Loop | Role |
|---|---|---|---|---|
| Main Thread | `controller.py` | 1 (shared) | Qt event loop | UI, orders, coordination |
| Thread 1 | `services/market_data_engine.py` | 1 (shared) | 100ms | Ticks, status, account |
| Thread 2 | `services/vol_engine.py` | 2 (own) | 180s | IV scan, GARCH, fair vol |
| Thread 3 | `services/risk_engine.py` | 3 (own) | 2s | Positions, greeks, PnL |

## Main Thread (Qt Event Loop)

No heavy computation. UI rendering, order execution, queue coordination.

**QTimers:**
- `_ui_poll_timer` (50ms): drains `_ui_queue` — executes callbacks posted by Thread 1
- `_engine_poll_timer` (1s): drains `_vol_output_queue` and `_risk_output_queue`, calls `_refresh_status()`

**Order execution:** synchronous on main thread via `OrderExecutor` (Spot/Future/Option). Uses shared IB connection (client_id=1). Triggered by Book (Preview) button click.

## Thread 1 — MarketDataEngine

```
File:       services/market_data_engine.py
Class:      MarketDataEngine(threading.Thread)
IB:         shared ib_client (client_id=1)
Loop:       100ms
```

**Each iteration:**
1. `ib_client.process_messages()` — drain IB tick queue
2. `ib_client.get_status_snapshot()` — connection state, latency
3. Update `latest_bid` / `latest_ask` from ticks
4. Write `risk_engine.spot = mid` (atomic float, consumed by Thread 3)
5. Every 10s: `ib_client.get_portfolio_snapshot()` — account summary

**Communication:** `_post_ui(callback)` into `_ui_queue`, drained by QTimer 50ms on main thread.

**Panels fed:** chart (ticks), order_ticket (bid/ask), status (connection), account_summary (10s), logs (messages)

## Thread 2 — VolEngine

```
File:       services/vol_engine.py
Class:      VolEngine(threading.Thread)
IB:         own connection, client_id=2, fresh per cycle
Loop:       180s (configurable via config/vol_config.json)
Startup:    10s delay
Condition:  not started if market is closed (spot <= 0)
```

**Each cycle (~15-30s of work):**
1. Connect IB (client_id=2)
2. Get forward price from front EUR future
3. Discover EUU option chains (6 tenors: 1M-6M)
4. Qualify C+P contracts around ATM
5. Scan IV via `reqMktData` on 100+ FOP contracts, wait 8s for greeks
6. PCHIP interpolation to 5 delta pillars per tenor
7. Fetch 1Y OHLC, compute Yang-Zhang realized vol
8. Fit GARCH(1,1), project vol forward
9. Combine: sigma_fair + signal (CHEAP/FAIR/EXPENSIVE)
10. Disconnect IB

**After scan:** writes `risk_engine.iv_surface = {tenor: pillar_dict}` (atomic dict assignment, consumed by Thread 3)

**Communication:** `vol_output_queue.put(result)`, drained by QTimer 1s on main thread.

**Panels fed:** vol_scanner, term_structure, smile_chart

## Thread 3 — RiskEngine

```
File:       services/risk_engine.py
Class:      RiskEngine(threading.Thread)
IB:         own connection, client_id=3, fresh per fetch
Loop:       2s (greeks), sub-loop 10s (positions fetch)
Startup:    15s delay
```

**Each iteration (2s):**
1. Every 10s: connect IB (client_id=3), `reqPositions()`, sleep 2s, disconnect (~2.5s blocking)
2. Read `self.spot` (written by Thread 1) and `self.iv_surface` (written by Thread 2)
3. If spot > 0: BS greeks per position (delta, vega, gamma, theta, PnL), summary, PnL chart (31 points vectorized via numpy)
4. If spot <= 0 (market closed): static positions with basic data only (no greeks, no PnL)

**PnL chart:** `_bs_price_vec()` computes 31 spot points in a single vectorized `scipy.stats.norm.cdf` call instead of 31 x N individual Python calls.

**Communication:** `risk_output_queue.put(result)`, drained by QTimer 1s on main thread.

**Panels fed:** open_positions, book (greeks summary), pnl_chart

## IB Connections

| Client ID | Used by | Lifecycle | Requests |
|---|---|---|---|
| 1 | Thread 1 + Main Thread | App lifetime | Ticks, account, orders (`placeOrder`, `whatIfOrder`) |
| 2 | Thread 2 only | Connect/disconnect each 180s cycle | `reqContractDetails`, `reqMktData` (FOP), `reqHistoricalData` |
| 3 | Thread 3 only | Connect/disconnect each 10s fetch | `reqPositions` |

## Engine Pool

The controller creates and manages all threads as a single pool.

**Start** (`_start_engine_pool`, triggered by "Start Engine" button):
```
1. Create MarketDataEngine, RiskEngine
2. If market open: create VolEngine
3. Wire: market_engine -> risk_engine.spot
4. Wire: vol_engine -> risk_engine.iv_surface
5. Wire: market_engine.on_payload -> controller._on_market_data_payload
6. Start all threads
7. Start QTimer 1s (_poll_engine_queues)
```

**Stop** (`_stop_engine_pool`, triggered by "Stop Engine" button):
```
1. Stop QTimer 1s
2. Call stop() on each thread (sets _stop_event)
3. Join each thread (timeout 5s)
4. Clear references
```

## Communication

```
Thread 1 ----_post_ui(callback)----> _ui_queue ----QTimer 50ms----> main thread -> panel.update()

Thread 2 ----queue.put(result)-----> _vol_output_queue --+
                                                         |--> QTimer 1s -> main thread -> panel.update()
Thread 3 ----queue.put(result)-----> _risk_output_queue -+

Thread 1 ----risk_engine.spot = float----> Thread 3 reads self.spot
Thread 2 ----risk_engine.iv_surface = dict----> Thread 3 reads self.iv_surface
```

## Synchronization

**Stop events:** each thread has a `threading.Event`.
```python
while not self._stop_event.wait(timeout=INTERVAL_S):
    # work
```
`wait(timeout)` sleeps for N seconds or returns immediately when `set()` is called.

**Shared data (no locks):**
- `risk_engine.spot` (float): written by Thread 1, read by Thread 3. Atomic in CPython (GIL).
- `risk_engine.iv_surface` (dict): written by Thread 2, read by Thread 3. Atomic dict assignment in CPython.
- `_ui_queue`, `_vol_output_queue`, `_risk_output_queue`: `queue.Queue`, thread-safe by design.

**Asyncio:** Thread 2 and Thread 3 each create their own event loop (`asyncio.new_event_loop()`), required by ib_insync.

## Lifecycle

```
App Start
    _setup_services()
        QTimer 50ms start (_drain_ui_queue)
        OrderExecutor.start()

Connect Button
    IB client_id=1 connect
    _discover_option_chains()

Start Engine Button
    _start_engine_pool()
        Thread 1 start (MarketDataEngine, immediate)
        Thread 3 start (RiskEngine, 15s delay)
        Thread 2 start (VolEngine, 10s delay) — skipped if market closed
        QTimer 1s start (_poll_engine_queues)

Stop Engine Button
    _stop_engine_pool()
        QTimer 1s stop
        All threads stop + join

Disconnect / Close
    _shutdown_services()
        _stop_engine_pool()
        OrderExecutor.stop()
        QTimer 50ms stop
        IB disconnect
```
