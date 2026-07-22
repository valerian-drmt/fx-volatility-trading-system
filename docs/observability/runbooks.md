# Observability runbooks

Operator playbooks for the LGTM stack (Loki logs, Prometheus metrics, Tempo
traces, Grafana dashboards). Metric naming lives in
[conventions.md](conventions.md); the local stack in
[../ops/local-stack.md](../ops/local-stack.md).

## Bring the stack up

The observability services live on the `obs` compose profile — opt-in, so a plain
`docker compose up` stays light:

```powershell
docker compose --profile obs up -d
```

This starts `prometheus`, `cadvisor`, `loki`, `promtail`, `tempo`,
`otel-collector` and `grafana`, all on `fxvol-internal`. Grafana maps to the host
at `127.0.0.1:3000` (anonymous Viewer access, edits via the login form).

`metrics` is a deliberate **subset** — `prometheus` + `cadvisor` only:

```powershell
docker compose --profile metrics up -d
```

It gives the /dev Hardware tab its per-container CPU/RAM graphs **without** the
parts that must not run on a public host (Grafana's host-mapped port, promtail's
read-write `docker.sock`). Prod runs `metrics`, never `obs`.

## Loki — query logs

Open Grafana → Explore → **Loki**. Labels are `container`, `engine`, `stream`,
`level` (see [conventions.md](conventions.md#log-labels-promtail--loki)).

```logql
# tail vol-engine errors
{engine="vol-engine", level="error"}

# everything for one cycle across all engines (cycle_id is in the body, not a label)
{engine=~".+"} |= "cycle_id" |= "a1b2c3d4"

# parse JSON and filter on a field
{engine="risk-engine"} | json | event="cycle_end"
```

Click a `trace_id` in a log line to jump to its Tempo trace (Loki derived field).

## Prometheus — metrics

Explore → **Prometheus**, or hit `http://127.0.0.1:9090` after mapping the port.
Useful queries:

```promql
# cycle error rate per engine
rate(engine_cycles_total{status="error"}[5m])

# stalled engine: seconds since last cycle
time() - engine_last_cycle_timestamp_seconds

# p95 cycle duration
histogram_quantile(0.95, rate(engine_cycle_duration_seconds_bucket[5m]))

# IB session down
ib_session_connected == 0
```

Check what is being scraped at `http://127.0.0.1:9090/targets` — every `engines`
target should be `UP`.

## Tempo — traces

Explore → **Tempo** → Search by service or `cycle_id`. Each engine cycle is a root
span `<engine>_cycle`; child spans (fetch, calibrate, …) nest under it. From a
span, `tracesToLogsV2` opens the matching Loki logs (keyed on `cycle_id` +
`engine`). Traces flow engines → `otel-collector:4317` → `tempo:4317`; retention
is 7 days.

## Grafana — dashboards

Datasources are auto-provisioned
([`obs/grafana/provisioning/datasources/datasources.yml`](../../obs/grafana/provisioning/datasources/datasources.yml)):
Prometheus (default), Loki, Tempo, with the Loki↔Tempo click-through wired. The
`engines-overview` dashboard
([`obs/grafana/dashboards/engines-overview.json`](../../obs/grafana/dashboards/engines-overview.json))
is provisioned from disk — cycle throughput, error rate, freshness, and a Loki
disk-usage panel.

## The /dev Hardware tab — per-container CPU/RAM

The app's /dev Hardware tab graphs cAdvisor's per-container metrics
(`cadvisor:8080`, scraped by Prometheus). "How much is vol-engine using right now?"
is answered there.

Caveat: on Docker Desktop / WSL2 cAdvisor only sees the root cgroup and reports
nothing per-container — the api then falls back to the `docker.sock` path. On a
real Linux kernel (EC2) cAdvisor serves the tab directly, provided the daemon uses
the classic overlay2 image store (`setup.sh` pins it via
`daemon.json {"features":{"containerd-snapshotter":false}}`).

## Common tasks

| Question | Where |
|---|---|
| Tail an engine's errors | Loki: `{engine="vol-engine", level="error"}` |
| Follow one cycle end-to-end | Loki `\|= "<cycle_id>"`, or Tempo search by `cycle_id` |
| Is an engine stalled? | Prometheus: `time() - engine_last_cycle_timestamp_seconds` |
| A container's CPU/RAM | /dev Hardware tab, or cAdvisor via Prometheus |
| Is IB connected? | Prometheus: `ib_session_connected` |
| Are all targets scraped? | `http://127.0.0.1:9090/targets` |

## Retention

| Store | Retention | Config |
|---|---|---|
| Prometheus | `PROM_RETENTION_TIME` (default 7d) / `512MB` | `docker-compose.yml` command flags |
| Loki | 14 days (`retention_period: 336h`) | [`obs/loki.yml`](../../obs/loki.yml) |
| Tempo | 7 days (`block_retention: 168h`) | [`obs/tempo.yml`](../../obs/tempo.yml) |
