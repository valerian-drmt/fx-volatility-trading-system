# Runbooks — debug guide via la stack obs

3 incidents typiques + chemin de diagnostic via Grafana / LGTM.
Compagnon de `CONVENTIONS.md`.

Pré-requis : profile `obs` démarré (`docker compose --profile obs up -d`),
Grafana ouvert sur http://localhost:3000.

---

## Runbook 1 — "Le cycle vol-engine n'avance plus"

### Symptômes
- Panel E (Portfolio) montre stress-grid / greeks-ladder figés
- `latest_vol_surface:EURUSD` dans Redis ne change plus
- Heartbeat vol_engine vert dans EngineHealth, donc le process tourne

### Diagnostic LGTM

**Étape 1 — Cycle rate sur Prometheus**

Grafana → Explore → Prometheus → query :
```promql
rate(engine_cycles_total{engine="vol_engine"}[5m])
```
- Si rate = 0 → le cycle ne se termine pas. Va à étape 2.
- Si rate > 0 → les cycles passent mais skip vite (no_spot / no_surface). Va à étape 4.

**Étape 2 — Quelle étape bloque ?**

Grafana → Explore → Tempo → Search → service.name=vol_engine → trier par duration desc → ouvrir le trace le plus long → flame graph.

Repérer le child span qui domine :
- `vol_read_spot` → souci Redis spot key
- `vol_compute_surface` → IB chain fetch lent (chain_fetcher.scan_all_tenors_concurrent)
- `vol_compute_regime` → DB lookup feature_history
- `vol_compute_pca` → recalc PCA model
- `vol_redis_publish` → Redis write lent

**Étape 3 — Logs du span**

Dans le détail du span lent, bouton "Logs for this span" → Loki s'ouvre filtré sur trace_id. Tu vois exactement ce qui a été tenté et ce qui a foiré.

**Étape 4 — Cycle skipped reasons**

Si rate > 0 mais panels figés, les cycles aboutissent à early-return. Grafana → Explore → Loki :
```logql
{engine="vol-engine"} |= "vol_cycle_skipped" | json
```
Filtre `reason` : `market_closed`, `no_spot`, `no_surface`. Si `no_spot` répétitif → market-data engine en panne (cf. runbook 3).

---

## Runbook 2 — "Les writes Postgres sont lents"

### Symptômes
- Panel E positions désync vs IB live (gap > 30s)
- `position_metric_history` count croît trop lentement
- db-writer container UP mais heartbeat délayé

### Diagnostic

**Étape 1 — Drain rate**

Grafana → Explore → Prometheus :
```promql
rate(engine_cycles_total{engine="db_writer"}[5m])
```
Cible normale : ~0.2 cycles/s (HEARTBEAT_INTERVAL_S=5s). Si plus bas → drainage queue lent.

**Étape 2 — Cycle duration**

```promql
histogram_quantile(0.99, sum by (le) (rate(engine_cycle_duration_seconds_bucket{engine="db_writer"}[5m])))
```
Si p99 > 1s sur un cycle heartbeat (qui devrait être < 5 ms) → DB lente ou queue saturée.

**Étape 3 — Traces de batch writes**

Grafana → Explore → Tempo → Search → service.name=db_writer → trace du `db_writer_cycle` lent. Le span n'a pas d'enfants (cycle = heartbeat ping, le drain bulk-insert vit dans `persistence.writer.AsyncDatabaseWriter.run()` non instrumenté). Donc tu auras seulement le timing global.

**Étape 4 — Erreurs DB explicites**

Grafana → Explore → Loki :
```logql
{engine="db-writer"} |~ "writer loop error|psycopg|asyncpg"
```
Cherche `connection refused`, `deadlock`, `unique constraint violation`. Trace via timestamp.

**Étape 5 — Postgres lui-même**

```powershell
docker compose exec postgres pg_isready -U fxvol
docker compose exec postgres psql -U fxvol -c "SELECT count(*) FROM position_metric_history;"
docker compose stats fxvol-postgres
```

Si CPU postgres > 80% → query plan à analyser (EXPLAIN ANALYZE sur un INSERT). Hors scope LGTM, problème métier.

---

## Runbook 3 — "IB Gateway s'est déconnecté"

### Symptômes
- Panel 5 Grafana "IB session uptime" rouge sur un ou plusieurs clientID
- Logs `ib_not_connected` répétitifs dans market-data / risk / execution
- Heartbeats engines OK (cycle loop tourne), mais leurs steps IB fail

### Diagnostic

**Étape 1 — Confirmer côté metrics**

Grafana → Explore → Prometheus :
```promql
ib_session_connected
```
0 = down sur ce clientID. Note les IDs concernés (1=market-data, 2=vol, 3=risk, 5=execution).

**Étape 2 — Quand est-ce arrivé ?**

Étendre la fenêtre de temps en haut à droite de Grafana → "Last 6 hours". Tu vois le flip 1→0. Note le timestamp.

**Étape 3 — Logs ib-gateway autour du timestamp**

Grafana → Explore → Loki :
```logql
{container="fxvol-ib-gateway"} |~ "Login|Logout|TrustedIPs|socat|2FA|Connecting"
```
Cherches `Login has completed`, `Logout`, `Pending Tasks`, `Connecting to server`. Si plusieurs `Connecting to server` consécutifs → flapping (= autre session IB ailleurs cf. memory `IB single session per userid`).

**Étape 4 — Logs engine côté client**

```logql
{engine=~"market-data|vol-engine|risk-engine|execution-engine"} |~ "Disconnect|Peer closed|API connection failed"
```
Si `Peer closed connection` = IB Gateway a kické. Si `API connection failed: TimeoutError` = TCP timeout au handshake.

**Étape 5 — Actions correctives**

| Cause | Fix |
|---|---|
| Autre login web/TWS/mobile actif | Fermer ces sessions ; `docker compose restart ib-gateway` puis engines |
| TrustedIPs perdues post-recreate | Reconnect VNC `127.0.0.1:5900`, Configure → API → Trusted IPs : 127.0.0.1 + 172.20.0.10/11/12/14, OK + Save Settings |
| 2FA expiré | Approuver via push mobile ou switch TOTP côté compte IB |
| Daily auto-restart 23:59 Paris | Normal, attendre 1-2 min puis vérifier reconnect |

---

## Quick reference — queries utiles

```promql
# Cycle rate par engine
sum by (engine) (rate(engine_cycles_total[5m]))

# Error rate par engine
sum by (engine) (rate(engine_cycles_total{status="error"}[5m]))

# Last cycle age
time() - engine_last_cycle_timestamp_seconds

# IB session aggregate
sum(ib_session_connected)
```

```logql
# Tous logs error/exception sur tous engines
{engine=~".+"} |~ "(?i)error|exception|traceback|failed"

# Logs d'un cycle précis
{engine=~".+"} | json | cycle_id="abc123..."

# Logs d'un trace précis (cross-link Tempo→Loki)
{engine=~".+"} | json | trace_id="d723131bd6..."
```

```traceql
# Traces > 1 seconde
{ duration > 1s }

# Traces vol-engine avec n_pillars=0 (chain fetch vide)
{ resource.service.name = "vol_engine" && span.n_pillars = 0 }
```
