# Architecture des services

Vue détaillée des containers du projet : ports, IPs, IB clientIds, cadence, dépendances.
Complète `project_architecture.md` (vue topologique) et `data_flow.md` (vue runtime).

---

## 1. Topologie réseau

Réseau Docker bridge : `fxvol_default` (subnet `172.20.0.0/24`).

```
                            ┌──────────────────────────┐
                            │   Internet / IB Servers  │
                            └─────────────┬────────────┘
                                          │
                            ┌─────────────▼────────────┐
                            │ ib-gateway (172.20.0.50) │
                            │  paper :4002 / live :4001│
                            └─┬───┬───┬───┬─────┬──────┘
                  clientId=1  │   │2  │3  │5    │
                              │   │   │   │     │
                              ▼   ▼   ▼   ▼     │
   ┌─────────────┐  ┌──────────────┐  ┌──────┐  │  ┌──────────────────┐
   │market-data  │  │vol-engine    │  │ risk │  │  │execution-engine  │
   │172.20.0.10  │  │172.20.0.11   │  │ .12  │  │  │172.20.0.14       │
   │             │  │              │  │      │  │  │  port 8001 priv  │
   └──┬──────┬───┘  └──┬───────┬───┘  └─┬─┬──┘  │  └────────┬──┬──────┘
      │      │         │       │        │ │     │           │  │
      │      └────┬────┘       │        │ │     │           │  │
      │           │            │        │ │     │           │  │
      ▼           ▼            ▼        ▼ ▼     ▼           ▼  ▼
   ┌──────────┐ ┌──────────────────────────────────────────────────┐
   │  Redis   │ │             Postgres (172.20.0.20)               │
   │.21 (pub) │ │  source de vérité — toutes tables                │
   │     K/V  │ │                                                  │
   └────┬─────┘ └────────┬─────────────────────────────────────────┘
        │                │
        ▼                ▼
   ┌──────────────────────────────────────────────────┐
   │ db-writer (sub Redis → batch INSERT Postgres)    │
   └──────────────────────────────────────────────────┘
        │
        │ (read-only)
        ▼
   ┌──────────────────────────────────────────────────┐
   │ api (172.20.0.13)  port 8000                     │
   │   stateless proxy — REST + WS                    │
   └────────────────────┬─────────────────────────────┘
                        │
                        ▼
                   nginx :80/443  ──►  frontend (vite build static)
```

---

## 2. Inventaire détaillé

| Container | IP | Port exposé | IB clientId | Cadence | Dépend de |
|---|---|---|---|---|---|
| `postgres` | 172.20.0.20 | 5432 (host only) | — | — | — |
| `redis` | 172.20.0.21 | 6379 (host only) | — | — | — |
| `ib-gateway` | 172.20.0.50 | 4002 (host only, VNC :5900) | — | — | — |
| `market-data` | 172.20.0.10 | — | 1 | continu (200ms throttle) | redis, ib-gateway |
| `vol-engine` | 172.20.0.11 | — | 2 | cycle 180s | redis, postgres, market-data, ib-gateway |
| `pca-fitter` | tbd | — | — | cron hebdo (Sun 22:00 UTC) | postgres |
| `snapshot-collector` | tbd | — | — | cron horaire (HH:00 UTC) | redis, postgres |
| `risk` | 172.20.0.12 | — | 3 | cycle 60s | redis, postgres, ib-gateway |
| `execution-engine` | 172.20.0.14 | 8001 (network only) | 5 | sync 1s + event-driven | redis, postgres, ib-gateway |
| `db-writer` | tbd | — | — | event-driven (batch 100 / 1s) | redis, postgres |
| `api` | 172.20.0.13 | 8000 (via nginx) | — | request-driven | redis, postgres, execution-engine |
| `backtest-runner` | tbd | — | — | job-driven | postgres |
| `frontend` | tbd | — | — | static | (built once) |
| `nginx` | tbd | 80, 443 (host) | — | continu | api, frontend |

**Conventions IPs** : `.10–.14` engines applicatifs, `.20–.21` data plane, `.50` IB gateway.

---

## 3. Matrice de dépendances (à l'exécution)

| Service | Postgres | Redis | IB Gateway | Autres |
|---|:-:|:-:|:-:|---|
| market-data | ⚪ | ✅ | ✅ | — |
| vol-engine | ✅ | ✅ | ✅ | dépend de market-data (Redis) |
| pca-fitter | ✅ | ⚪ | ⚪ | lit `vol_snapshots_30d` |
| snapshot-collector | ✅ | ✅ (read) | ⚪ | lit `latest_vol_surface` |
| risk | ✅ | ✅ | ✅ | dépend vol-engine + execution |
| execution-engine | ✅ | ✅ | ✅ | — |
| db-writer | ✅ | ✅ (sub) | ⚪ | — |
| api | ✅ | ✅ | ⚪ | proxy → execution-engine |
| backtest-runner | ✅ | ⚪ | ⚪ | offline |

> ⚪ = pas requis. Indispensable pour partir d'une stack vide :
> postgres + redis + ib-gateway + market-data + vol-engine + db-writer + api + frontend + nginx.

---

## 4. Healthchecks & heartbeats

| Service | Mécanisme | TTL |
|---|---|---|
| postgres | `pg_isready` interval 5s | — |
| redis | `redis-cli ping` interval 10s | — |
| ib-gateway | TCP probe :4002 | — |
| market-data | Redis SET `heartbeat:market-data` | 5s |
| vol-engine | Redis SET `heartbeat:vol-engine` | 600s (cycle 180s × 3) |
| risk | Redis SET `heartbeat:risk` | 180s |
| execution-engine | Redis SET `heartbeat:execution` | 5s |
| db-writer | Redis SET `heartbeat:db-writer` | 30s |
| api | endpoint `/health` (Postgres + Redis ping) | request-driven |
| backtest-runner | row state in `backtest_runs` (`running / done / failed`) | — |

`api /ready` agrège tous les heartbeats Redis et renvoie `503` si l'un manque.

---

## 5. Ordre de démarrage (compose `depends_on`)

```
postgres ──┐
redis    ──┼──► db-writer ──┐
           │                ├──► api ──► nginx ──► frontend
ib-gw   ──┴──► market-data │
                │           │
                ├──► vol-engine ────────┘
                ├──► risk
                └──► execution-engine
```

Postgres + Redis + IB Gateway sont **prérequis durs**. Tout le reste démarre en parallèle
une fois ces 3 healthy. Les services applicatifs gèrent leurs propres reconnects (ne
crash pas si IB tombe en cours de run).

---

## 6. Configuration partagée

Toutes les configs vivent en table Postgres (versioned, append-only) :

| Table | Owner | Hot-reload via | Consommée par |
|---|---|---|---|
| `vol_config` | api admin | `config:changed` | vol-engine |
| `risk_config` | api admin (à créer) | `config:changed` | risk |
| `exec_config` | api admin (à créer) | `config:changed` | execution-engine |
| `pca_models` | pca-fitter | `pca:refit` | vol-engine |

Les services subscribent au topic `config:changed` et invalident leur cache local
(reload best-effort, jamais de `os.exit()`). Cf. ADR-008.

---

## 7. Ports résumés

| Port | Service | Visible depuis |
|---|---|---|
| 80 / 443 | nginx | host (Internet via reverse proxy) |
| 8000 | api | réseau Docker uniquement (proxifié par nginx) |
| 8001 | execution-engine | réseau Docker uniquement (privé) |
| 4002 | ib-gateway | host (loopback only, jamais exposé) |
| 5900 | ib-gateway VNC | host (loopback only) |
| 5432 | postgres | host (loopback only) |
| 6379 | redis | host (loopback only) |

**Aucun port métier exposé Internet.** Seul `nginx` est accessible depuis l'extérieur ;
il proxify api + frontend.
