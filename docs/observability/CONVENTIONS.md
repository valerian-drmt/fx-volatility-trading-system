# Observability conventions

Naming + cardinality rules for metrics, log fields, and spans. The
canonical definitions live in `src/shared/observability.py` — the
tables below mirror what's instantiated there.

---

## Metrics (Prometheus)

Format: `<namespace>_<subsystem>_<name>_<unit>`.

### Defined in `src/shared/observability.py`

| Metric | Type | Labels | Unit | Use |
|---|---|---|---|---|
| `engine_cycles_total` | Counter | `engine`, `status` | count | cycle throughput, error rate |
| `engine_cycle_duration_seconds` | Histogram | `engine` | seconds | latency p50/p99 per engine |
| `engine_last_cycle_timestamp_seconds` | Gauge | `engine` | unix ts | staleness alerts (`time() - x > N×period`) |
| `ib_session_connected` | Gauge | `client_id` | 0/1 | IB session health |
| `ib_requests_total` | Counter | `engine`, `request_type`, `status` | count | IB API call throughput + error rate |

### Label cardinality rules

**Allowed labels** (low-cardinality, ≤ ~50 cumulative values):
- `engine` ∈ {market_data, vol_engine, risk_engine, execution_engine, db_writer}
- `status` ∈ {ok, error, timeout}
- `client_id` ∈ {1, 2, 3, 5}
- `request_type` ∈ {market_data, historical, chain, order, account}
- `symbol` ∈ {EURUSD, ...} (capped at 10)

**Forbidden labels** (high-cardinality, they blow up the TSDB):
- `instrument_id`, `contract_id`, `con_id`, `trade_id`, `order_id`
- `cycle_id`, `trace_id`, `span_id`
- `user_id`, `account_id`

→ If you need that information for debugging purposes, put it in the **logs** (Loki), not the **metrics** (Prometheus).

---

## `/metrics` ports per engine

| Engine | Port | Cycle wrapped |
|---|---|---|
| market-data | 9101 | `_poll_once` (~10/s) |
| vol-engine | 9102 | `run_cycle` (180s cadence) |
| risk-engine | 9103 | `run_cycle` (2s cadence) |
| execution-engine | 9104 | `position_sync_loop` tick (1s cadence) |
| db-writer | 9105 | `_heartbeat_loop` tick (heartbeat interval) |

Exposed via internal `expose:` on `fxvol-internal` (no `ports:` → not reachable from the host). Scraped by Prometheus in P1.

---

## Log fields (structlog JSON)

### Auto-injected

| Field | Source | Always present? |
|---|---|---|
| `timestamp` | `structlog.processors.TimeStamper(fmt="iso", utc=True)` | yes |
| `level` | `structlog.processors.add_log_level` | yes |
| `event` | first arg of `log.info(...)` | yes |
| `service_name` | bound in `configure_logging()` | yes |
| `cycle_id` | bound by `new_cycle()` (via `observed_cycle`) | yes during a cycle |
| `trace_id` | bound by the OTel SDK in Phase 2 | not in P0 |

### To add per log call

Convention: short verb + context variables.

```python
log.info("chain_fetched", symbol="EURUSD", n_strikes=47, duration_ms=234)
log.info("db_inserted", table="position_metric_history", rows=12)
log.error("ib_request_failed", request_type="reqMktData", error_code=354)
```

Recommended verbs: `cycle_start`, `cycle_end`, `chain_fetched`, `surface_calibrated`,
`db_inserted`, `redis_published`, `ib_request_*`, `engine_started`, `engine_stopped`.

---

## OTel spans (Phase 2, not active yet)

Format: `<engine>_<verb>` or `<engine>_<noun>`.

| Span | Level | Typical attributes |
|---|---|---|
| `vol_cycle` | root | engine, cycle_id, symbol |
| `vol_fetch_chain` | child | n_strikes, duration_ms |
| `vol_calibrate_garch` | child | alpha, beta, omega |
| `vol_fit_svi` | child | tenor, rmse |
| `risk_cycle` | root | engine, cycle_id, n_positions |
| `risk_compute_greeks` | child | spot, iv |
| `db_write` | child or remote root | table, rows |

**Granularity**: 1 span per business stage. **NOT** 1 span per item of a loop (e.g. 1 span per strike of a chain → 47 strikes × 5 stages × 1 cycle/180s = OK; 47 strikes × nested spans = ✗ Tempo catastrophe).

---

## Quick reference — instrumenting a new engine

1. Import `from shared.observability import observed_cycle, start_metrics_server`
2. In `main.py`, after `configure_logging(...)`: `start_metrics_server(<port>)`
3. In the engine's `while not self._stop.is_set()` loop: `with observed_cycle("<engine_name>"): ...`
4. For business events inside the cycle: `log = structlog.get_logger(); log.info("event_name", k=v)`
5. Add `expose: ["<port>"]` to the service in `docker-compose.yml`
6. Update the ports table above if a new port is used.

That's it. No manual cycle_id wiring (automatic via structlog ContextVar).
