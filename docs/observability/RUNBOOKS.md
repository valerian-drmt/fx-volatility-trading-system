# Runbooks — debug guide via the obs stack

3 typical incidents + diagnostic path via Grafana / LGTM.
Companion to `CONVENTIONS.md`.

Prerequisite: `obs` profile started (`docker compose --profile obs up -d`),
Grafana open at http://localhost:3000.

---

## Runbook 1 — "The vol-engine cycle is stuck"

### Symptoms
- Panel E (Portfolio) shows frozen stress-grid / greeks-ladder
- `latest_vol_surface:EURUSD` in Redis no longer changes
- vol_engine heartbeat is green in EngineHealth, so the process is running

### LGTM diagnostics

**Step 1 — Cycle rate in Prometheus**

Grafana → Explore → Prometheus → query:
```promql
rate(engine_cycles_total{engine="vol_engine"}[5m])
```
- If rate = 0 → the cycle never completes. Go to step 2.
- If rate > 0 → cycles run but skip quickly (no_spot / no_surface). Go to step 4.

**Step 2 — Which stage is blocking?**

Grafana → Explore → Tempo → Search → service.name=vol_engine → sort by duration desc → open the longest trace → flame graph.

Spot the dominating child span:
- `vol_read_spot` → Redis spot key issue
- `vol_compute_surface` → slow IB chain fetch (chain_fetcher.scan_all_tenors_concurrent)
- `vol_compute_regime` → DB lookup feature_history
- `vol_compute_pca` → PCA model recalc
- `vol_redis_publish` → slow Redis write

**Step 3 — Logs for the span**

In the slow span's detail view, "Logs for this span" button → Loki opens filtered on trace_id. You see exactly what was attempted and what went wrong.

**Step 4 — Cycle skipped reasons**

If rate > 0 but the panels are frozen, cycles end in an early-return. Grafana → Explore → Loki:
```logql
{engine="vol-engine"} |= "vol_cycle_skipped" | json
```
Filter on `reason`: `market_closed`, `no_spot`, `no_surface`. If `no_spot` is repeating → market-data engine is down (see runbook 3).

---

## Runbook 2 — "Postgres writes are slow"

### Symptoms
- Panel E positions out of sync vs IB live (gap > 30s)
- `position_metric_history` count grows too slowly
- db-writer container UP but heartbeat delayed

### Diagnostics

**Step 1 — Drain rate**

Grafana → Explore → Prometheus:
```promql
rate(engine_cycles_total{engine="db_writer"}[5m])
```
Normal target: ~0.2 cycles/s (HEARTBEAT_INTERVAL_S=5s). If lower → slow queue drain.

**Step 2 — Cycle duration**

```promql
histogram_quantile(0.99, sum by (le) (rate(engine_cycle_duration_seconds_bucket{engine="db_writer"}[5m])))
```
If p99 > 1s for a heartbeat cycle (which should be < 5 ms) → slow DB or saturated queue.

**Step 3 — Batch write traces**

Grafana → Explore → Tempo → Search → service.name=db_writer → trace of the slow `db_writer_cycle`. The span has no children (cycle = heartbeat ping, the bulk-insert drain lives in `persistence.writer.AsyncDatabaseWriter.run()` which is not instrumented). So you will only get the overall timing.

**Step 4 — Explicit DB errors**

Grafana → Explore → Loki:
```logql
{engine="db-writer"} |~ "writer loop error|psycopg|asyncpg"
```
Look for `connection refused`, `deadlock`, `unique constraint violation`. Correlate via timestamp.

**Step 5 — Postgres itself**

```powershell
docker compose exec postgres pg_isready -U fxvol
docker compose exec postgres psql -U fxvol -c "SELECT count(*) FROM position_metric_history;"
docker compose stats fxvol-postgres
```

If postgres CPU > 80% → query plan to analyze (EXPLAIN ANALYZE on an INSERT). Out of LGTM scope, business-level problem.

---

## Runbook 3 — "IB Gateway got disconnected"

### Symptoms
- Grafana panel 5 "IB session uptime" red on one or more clientIDs
- Repeating `ib_not_connected` logs in market-data / risk / execution
- Engine heartbeats OK (cycle loop is running), but their IB steps fail

### Diagnostics

**Step 1 — Confirm on the metrics side**

Grafana → Explore → Prometheus:
```promql
ib_session_connected
```
0 = down for that clientID. Note the affected IDs (1=market-data, 2=vol, 3=risk, 5=execution).

**Step 2 — When did it happen?**

Widen the time window at the top right of Grafana → "Last 6 hours". You see the 1→0 flip. Note the timestamp.

**Step 3 — ib-gateway logs around the timestamp**

Grafana → Explore → Loki:
```logql
{container="fxvol-ib-gateway"} |~ "Login|Logout|TrustedIPs|socat|2FA|Connecting"
```
Look for `Login has completed`, `Logout`, `Pending Tasks`, `Connecting to server`. If several consecutive `Connecting to server` → flapping (= another IB session elsewhere, cf. memory `IB single session per userid`).

**Step 4 — Engine logs on the client side**

```logql
{engine=~"market-data|vol-engine|risk-engine|execution-engine"} |~ "Disconnect|Peer closed|API connection failed"
```
If `Peer closed connection` = IB Gateway kicked the client. If `API connection failed: TimeoutError` = TCP timeout during the handshake.

**Step 5 — Corrective actions**

| Cause | Fix |
|---|---|
| Another web/TWS/mobile login active | Close those sessions; `docker compose restart ib-gateway` then the engines |
| TrustedIPs lost after recreate | Reconnect VNC `127.0.0.1:5900`, Configure → API → Trusted IPs : 127.0.0.1 + 172.20.0.10/11/12/14, OK + Save Settings |
| 2FA expired | Approve via mobile push or switch to TOTP on the IB account side |
| Daily auto-restart 23:59 Paris | Normal, wait 1-2 min then verify the reconnect |

---

## Quick reference — useful queries

```promql
# Cycle rate per engine
sum by (engine) (rate(engine_cycles_total[5m]))

# Error rate per engine
sum by (engine) (rate(engine_cycles_total{status="error"}[5m]))

# Last cycle age
time() - engine_last_cycle_timestamp_seconds

# IB session aggregate
sum(ib_session_connected)
```

```logql
# All error/exception logs across all engines
{engine=~".+"} |~ "(?i)error|exception|traceback|failed"

# Logs for a specific cycle
{engine=~".+"} | json | cycle_id="abc123..."

# Logs for a specific trace (cross-link Tempo→Loki)
{engine=~".+"} | json | trace_id="d723131bd6..."
```

```traceql
# Traces > 1 second
{ duration > 1s }

# vol-engine traces with n_pillars=0 (empty chain fetch)
{ resource.service.name = "vol_engine" && span.n_pillars = 0 }
```
