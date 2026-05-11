# Observability conventions

Naming + cardinality rules for metrics, log fields, and (later) spans.
Spec : `docs/LGTM_IMPLEMENTATION_SPEC.md` § 3.

---

## Metrics (Prometheus)

Format : `<namespace>_<subsystem>_<name>_<unit>`.

### Defined in `src/shared/observability.py`

| Metric | Type | Labels | Unit | Use |
|---|---|---|---|---|
| `engine_cycles_total` | Counter | `engine`, `status` | count | cycle throughput, error rate |
| `engine_cycle_duration_seconds` | Histogram | `engine` | seconds | latency p50/p99 per engine |
| `engine_last_cycle_timestamp_seconds` | Gauge | `engine` | unix ts | staleness alerts (`time() - x > N×period`) |
| `ib_session_connected` | Gauge | `client_id` | 0/1 | IB session health |
| `ib_requests_total` | Counter | `engine`, `request_type`, `status` | count | IB API call throughput + error rate |

### Label cardinality rules

**Autorisés** (low-cardinality, ≤ ~50 valeurs cumulées) :
- `engine` ∈ {market_data, vol_engine, risk_engine, execution_engine, db_writer}
- `status` ∈ {ok, error, timeout}
- `client_id` ∈ {1, 2, 3, 5}
- `request_type` ∈ {market_data, historical, chain, order, account}
- `symbol` ∈ {EURUSD, ...} (cap à 10)

**Interdits** (high-cardinality, explosent le TSDB) :
- `instrument_id`, `contract_id`, `con_id`, `trade_id`, `order_id`
- `cycle_id`, `trace_id`, `span_id`
- `user_id`, `account_id`

→ Si tu as besoin de cette info à des fins de debug, mets-la dans les **logs** (Loki), pas les **metrics** (Prometheus).

---

## Ports `/metrics` par engine

| Engine | Port | Cycle wrapped |
|---|---|---|
| market-data | 9101 | `_poll_once` (~10/s) |
| vol-engine | 9102 | `run_cycle` (180s cadence) |
| risk-engine | 9103 | `run_cycle` (2s cadence) |
| execution-engine | 9104 | `position_sync_loop` tick (1s cadence) |
| db-writer | 9105 | `_heartbeat_loop` tick (heartbeat interval) |

Exposés en `expose:` interne sur `fxvol-internal` (pas `ports:` → pas accessibles host). Scrappés par Prometheus en P1.

---

## Log fields (structlog JSON)

### Auto-injectés

| Field | Source | Toujours présent ? |
|---|---|---|
| `timestamp` | `structlog.processors.TimeStamper(fmt="iso", utc=True)` | oui |
| `level` | `structlog.processors.add_log_level` | oui |
| `event` | premier arg de `log.info(...)` | oui |
| `service_name` | bound dans `configure_logging()` | oui |
| `cycle_id` | bound par `new_cycle()` (via `observed_cycle`) | oui pendant un cycle |
| `trace_id` | bound par OTel SDK en Phase 2 | non en P0 |

### À ajouter par log

Convention : verbe court + variables de contexte.

```python
log.info("chain_fetched", symbol="EURUSD", n_strikes=47, duration_ms=234)
log.info("db_inserted", table="position_metric_history", rows=12)
log.error("ib_request_failed", request_type="reqMktData", error_code=354)
```

Verbes recommandés : `cycle_start`, `cycle_end`, `chain_fetched`, `surface_calibrated`,
`db_inserted`, `redis_published`, `ib_request_*`, `engine_started`, `engine_stopped`.

---

## Spans OTel (Phase 2, pas encore actif)

Format : `<engine>_<verb>` ou `<engine>_<noun>`.

| Span | Niveau | Attributs typiques |
|---|---|---|
| `vol_cycle` | racine | engine, cycle_id, symbol |
| `vol_fetch_chain` | enfant | n_strikes, duration_ms |
| `vol_calibrate_garch` | enfant | alpha, beta, omega |
| `vol_fit_svi` | enfant | tenor, rmse |
| `risk_cycle` | racine | engine, cycle_id, n_positions |
| `risk_compute_greeks` | enfant | spot, iv |
| `db_write` | enfant ou racine remote | table, rows |

**Granularité** : 1 span par stage métier. **PAS** 1 span par item d'une boucle (ex : 1 span par strike d'une chain → 47 strikes × 5 stages × 1 cycle/180s = OK ; 47 strikes × spans imbriqués = ✗ catastrophe Tempo).

---

## Quick reference — instrumenter un nouvel engine

1. Importer `from shared.observability import observed_cycle, start_metrics_server`
2. Dans `main.py`, après `configure_logging(...)` : `start_metrics_server(<port>)`
3. Dans le `while not self._stop.is_set()` du engine : `with observed_cycle("<engine_name>"): ...`
4. Pour les events métier dans le cycle : `log = structlog.get_logger(); log.info("event_name", k=v)`
5. Ajouter `expose: ["<port>"]` au service dans `docker-compose.yml`
6. Mettre à jour ce tableau de ports si nouveau port.

C'est tout. Pas de wiring manuel de cycle_id (auto via structlog ContextVar).
