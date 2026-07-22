# Observability conventions

Metric naming and label-cardinality rules for the stack. The primitives live in
[`src/shared/observability.py`](../../src/shared/observability.py); the scrape
config in [`obs/prometheus.yml`](../../obs/prometheus.yml). See the operator
playbooks in [runbooks.md](runbooks.md).

## Metric naming

Every metric follows the Prometheus convention `<namespace>_<subsystem>_<name>_<unit>`:
snake_case, a base-unit suffix (`_seconds`, `_total`), no camelCase. Counters end
in `_total`. Names are stable across engines — one `engine` label distinguishes
producers rather than one metric per engine.

## Metric families

Each engine process defines the shared families in `observability.py` and exposes
them on its own `/metrics` endpoint. Cross-engine cardinality is partitioned by
port, so the `engine` label stays small.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `engine_cycles_total` | Counter | `engine`, `status` | Cycles completed, by terminal status (`ok`/`error`). |
| `engine_cycle_duration_seconds` | Histogram | `engine` | Wall-clock duration of one cycle (buckets 0.01s–120s). |
| `engine_last_cycle_timestamp_seconds` | Gauge | `engine` | Unix ts of the last completed cycle — feeds staleness alerts. |
| `ib_session_connected` | Gauge | `client_id` | IB Gateway session state (1=connected, 0=disconnected). |
| `ib_requests_total` | Counter | `engine`, `request_type`, `status` | IB API requests, by type and outcome. |

The `status="error"` series is pre-warmed to `0` at startup
(`start_metrics_server(port, engine)`), so Grafana's error-rate panel renders a
flat zero line instead of "No data" before the first failure.

### The `observed_cycle` wrapper

`observed_cycle(engine)` is the one-call context manager engines wrap each loop
in. On exit it increments `engine_cycles_total`, observes
`engine_cycle_duration_seconds`, and sets `engine_last_cycle_timestamp_seconds`;
on exception it flips `status="error"` and re-raises. It also emits `cycle_start`
/ `cycle_end` structlog events and opens the root OTel span for the cycle.

## Label-cardinality rules

Labels stay **low-cardinality**. Allowed label values are bounded sets:

- `engine` — one of `market_data`, `vol_engine`, `risk_engine`, `execution`, `db_writer`.
- `status` — `ok` / `error`.
- `client_id` — the four fixed IB client IDs (1/2/3/5).
- `request_type` — a bounded set of IB call kinds.

**Forbidden as labels** (unbounded → series explosion): `cycle_id`, `trace_id`,
order IDs, symbols with free-form suffixes, timestamps, prices. `cycle_id` and
`trace_id` are propagated in **logs**, not metrics — they stay in the log message
body and are queried via LogQL, never promoted to a Loki/Prometheus label.

## Scrape jobs

`obs/prometheus.yml` scrapes at a 15s interval with `external_labels: {environment: dev}`:

| Job | Targets | Purpose |
|---|---|---|
| `engines` | `market-data:9101`, `vol-engine:9102`, `risk-engine:9103`, `execution-engine:9104`, `db-writer:9105` | Per-engine cycle + IB metrics. |
| `cadvisor` | `cadvisor:8080` | Per-container CPU/RAM/net/fs → the /dev Hardware tab. |
| `prometheus` | `localhost:9090` | Self-scrape (health + tsdb size). |
| `loki` | `loki:3100` | Loki ingest rate + storage. |

## Log labels (Promtail → Loki)

Promtail scrapes container stdout via Docker service discovery
([`obs/promtail.yml`](../../obs/promtail.yml)). Only low-cardinality fields become
Loki labels: `container`, `engine` (compose service), `stream` (stdout/stderr) and
`level`. `level` is extracted from JSON engine logs, or via per-service regex
fallbacks for redis / postgres / ib-gateway plain-text lines (unmatched lines get
`level="unknown"`). `cycle_id` and `trace_id` stay in the message body —
queryable with `|= "cycle_id"` but never indexed as labels.

## OTel tracing

`observed_cycle` opens a root span `<engine>_cycle` per cycle with `engine` +
`cycle_id` attributes; child spans attach via contextvars propagation. The span's
32-char `trace_id` is bound to structlog so log lines carry it. Engines export
OTLP/gRPC to `otel-collector:4317`, which batches and forwards to `tempo:4317`.
Grafana links logs to traces through the `trace_id` derived field (Loki → Tempo)
and `tracesToLogsV2` (Tempo → Loki), keyed on `cycle_id` + `engine`.
