# container — `api`

**Image** : maison (`docker/api/Dockerfile`)
**Container** : `fxvol-api`, IP `172.20.0.13`
**État** : ✅ existe — refactor en cours (proxy stateless depuis 2026-04-29)
**Steps** : tous (façade frontend)

---

## Rôle

FastAPI request-driven. **Aucune boucle interne.** Pure façade :
- read endpoints : forward Postgres / Redis (cache)
- write endpoints : forward `execution-engine` via httpx
- WebSockets : multiplex Redis pubsub vers frontend

> Règle (cf. CLAUDE.md project rules) : « api c'est juste des endpoints qui fonctionnent
> comme un rond-point. » Aucun `asyncio.create_task` lifecycle ; tout calcul vit ailleurs.

## Routers (état actuel)

| Router | Endpoints | Step |
|---|---|---|
| `health` | `/health`, `/ready` | infra |
| `admin` | `/admin/config` (GET/PUT versioned) | tous |
| `vol` | `/vol/surface`, `/vol/svi`, `/vol/ssvi` | step 1 / 3 |
| `analytics` | `/estimators`, `/term`, `/smile` | step 1 |
| `pricing` | `/preview` (legs pricing skeleton) | step 3 |
| `portfolio` | `/positions/*`, `/trades/*` | step 5 |
| `orders` | `/orders/*` (proxy → execution-engine) | step 4 |
| `cockpit` | `/dashboard/*` (aggregated) | UI |
| `dev` | `/dev/*` (DB explorer, Redis tools) | dev only |
| `ws` | WebSocket multiplex Redis | step 2 / 5 |

## Endpoints à ajouter pour v1.0

| Step | Endpoint | Source |
|---|---|---|
| 1 | `GET /api/v1/regime/state` | Postgres `regime_states` latest + freshness |
| 1 | `GET /api/v1/regime/history?days=N` | timeseries |
| 2 | `GET /api/v1/signals/pca` | Postgres `signals_pca` latest |
| 2 | `GET /api/v1/pca/model` | active `pca_models` row |
| 2 | WS `/ws/signals/pca` | Redis `signal:pca` |
| 3 | `POST /api/v1/preview` | composes vol-engine + risk + checks |
| 5 | `GET /api/v1/positions/live` | Postgres + Redis greeks merge |
| 5 | `GET /api/v1/exits/recent` | `exit_decisions` |
| 5 | WS `/ws/exits` | Redis `exit:decision` |
| backtest | `POST /api/v1/backtest/runs` | trigger `backtest-runner` job |
| backtest | `GET /api/v1/backtest/runs/{id}` | poll run state + folds |

## Configuration

`api` lit `vol_config`, `risk_config`, `exec_config` au démarrage + sub `config:changed`
pour invalidation cache. Aucun write direct (l'admin endpoint forwarde la mutation à
la table versioned avec INSERT v+1).

## Failure modes

- Postgres down → 503 sur read endpoints, WS continue à pousser depuis Redis.
- `execution-engine` down → 503 sur `/orders/*` avec body explicite (pas timeout silencieux).

## À faire pour v1.0

- [ ] Endpoints listés ci-dessus (par step).
- [ ] WS multiplex pour topics step 2 / 5.
- [ ] Versioned response schema (header `x-api-version`).
