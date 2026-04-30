# project_architecture — vue finale

Vue topologique du projet **tel qu'il sera v1.0** (MVP backtest validé + 5 steps live).
Source de vérité : code actuel + `docs/finale_project/STEP*_*.md` + `BACKTEST_WALK_FORWARD.md`.

> ⚠️ Le README de ce dossier liste encore `execution-engine` et `position-monitor` comme « à créer ».
> En réalité (état au 2026-04-30) `execution-engine` **existe** déjà (cf. `src/services/execution/`).
> `position-monitor` n'a pas de container dédié — la logique vivra dans `risk` (déjà présent) +
> extensions de `execution-engine` pour les exit rules. Voir tableau ci-dessous.

---

## 1. Containers — état réel vs cible

| Container | Image | État aujourd'hui | Cible v1.0 | Step(s) |
|---|---|---|---|---|
| `postgres` | postgres:16 | ✅ source de vérité | idem | tous |
| `redis` | redis:7 | ✅ pubsub + K/V | idem | tous |
| `ib-gateway` | ibcalpha/ibc | ✅ paper account | live possible post v1.0 | 1, 4, 5 |
| `market-data` | maison | ✅ ticks FUT/spot | + OHLC daily writer | 1, 2 |
| `vol-engine` | maison | ✅ cycle 180s, surface + signaux | + regime gating, + PCA z-scores | 1, 2 |
| `pca-fitter` | maison | ❌ à créer | cron hebdo refit modèle PCA | 2 |
| `snapshot-collector` | maison | ❌ à créer | cron horaire surface 30-dim | 2 |
| `execution-engine` | maison | ✅ submit + sync 1s + 6 tables | + multi-leg, rollback partial | 4, 5 |
| `risk` | maison | ✅ greeks + delta hedge calc | + 5 exit rules + MTM live | 5 |
| `db-writer` | maison | ✅ sink async batch | idem (ajout topics) | tous |
| `api` | maison | ✅ proxy stateless | + endpoints steps 2/3/5 | tous (frontend) |
| `backtest-runner` | maison | ❌ à créer | job batch walk-forward | backtest |
| `frontend` | nginx + vite | ✅ dev console 8 tabs | + 6 step tabs | tous |
| `nginx` | nginx | ✅ reverse proxy | idem | — |

**Décision** : pas de container `position-monitor` séparé. La logique de monitoring (MTM live,
5 exit rules, calcul delta hedge) appartient à `risk` qui tourne déjà à 60s. Les
actions (submit hedge order, close position) descendent vers `execution-engine` via Redis topic.
Cf. ADR à ajouter dans `DECISIONS.md`.

---

## 2. Matrice container × step

|  | Step 1 régime | Step 2 PCA | Step 3 preview | Step 4 exec | Step 5 monitoring | Backtest |
|---|---|---|---|---|---|---|
| `market-data` | spot + OHLC | OHLC daily | spot live | — | spot live | OHLC histo |
| `vol-engine` | smile + RV/HAR + 3-state régime | publie 30-dim surface | σ_fair_q par tenor | — | σ_mid live | replay offline |
| `snapshot-collector` | — | persiste surfaces horaires | — | — | — | feed historique |
| `pca-fitter` | — | refit hebdo, écrit `pca_models` | — | — | — | refit par fold |
| `risk` | — | — | greeks preview | — | greeks live + MTM + exits | greeks replay |
| `execution-engine` | — | — | — | submit + fills + 6 tables | hedge orders + close | — |
| `db-writer` | sink régime | sink z-scores + snapshots | sink trade_previews | sink orders/fills | sink position_snapshots | sink fold results |
| `api` | `/regime` | `/signals/pca` | `/preview` | `/orders` (proxy) | `/positions/live` | `/backtest/results` |
| `backtest-runner` | — | — | — | — | — | walk-forward orchestrator |
| `frontend` | tab Step 1 | tab Step 2 | tab Step 3 | tab Step 4 | tab Step 5 | tab Backtest |

---

## 3. Pipeline runtime (vue séquence)

```
                ┌─────────────────────────────────────────────────┐
                │ market-data (continuous)                        │
                │   ticks FUT 6E + spot EURUSD + chain FOP        │
                │   OHLC daily (Yang-Zhang inputs)                │
                └────────────┬────────────────────────────────────┘
                             │ Redis: ticks:eurusd, chain:eurusd
                             ▼
   ┌──────────────────────┐                  ┌─────────────────────┐
   │ vol-engine (180s)    │  hourly snapshot │ snapshot-collector  │
   │  smile + RV/HAR +    │ ───────────────► │  surfaces 30-dim    │
   │  3-state régime +    │                  └──────────┬──────────┘
   │  z-scores PC1/2/3    │                             │
   │  (publish + DB)      │             weekly refit    ▼
   └──┬───────────────────┘                  ┌─────────────────────┐
      │                                      │ pca-fitter (cron)   │
      │ Redis: vol:surface, signal:pca       │  loadings + z-stats │
      │                                      └─────────────────────┘
      ▼
   ┌─────────────────────────┐
   │ STEP 1 frontend tab     │  → autorise STEP 2 ?
   │ (regime gating)         │
   └─────────────────────────┘

   ┌─────────────────────────┐    ┌─────────────────────────┐
   │ STEP 2 frontend tab     │ ─► │ api /preview (POST)     │
   │ (PCA z-score actionable)│    │  → assemble structure   │
   └─────────────────────────┘    │  → risk: greeks + sizing│
                                  │  → 7 pre-submit checks  │
                                  └────────────┬────────────┘
                                               ▼
                                  ┌─────────────────────────┐
                                  │ STEP 3 frontend tab     │
                                  │ valid_for_submit ? OK   │
                                  └────────────┬────────────┘
                                               │ user click
                                               ▼
                                  ┌─────────────────────────┐
                                  │ api/orders → execution  │
                                  │  IB submit + fills sync │
                                  │  6 tables @ 1s          │
                                  └────────────┬────────────┘
                                               │ position created
                                               ▼
   ┌─────────────────────────────────────────────────────────┐
   │ risk (60s)                                              │
   │  MTM + 5 exit rules + delta hedge sizing                │
   │   → publish action:hedge / action:close to Redis        │
   └────────────┬────────────────────────────────────────────┘
                │
                ▼
       execution-engine (event-driven)
        submit hedge / close orders → fills → DB
```

---

## 4. Plan onglets frontend (`/dev`)

État actuel : 8 tabs orientés validation backend (Stack, WS, DB, Vol, Pricing, TradePreview, Signals, Orders).

Cible v1.0 : ajouter **6 tabs « step »** alignés sur le user-flow décrit dans `README.md` §Pipeline.

| # | Onglet | Composant | Backend | Step doc |
|---|---|---|---|---|
| 1 | 🚦 Step 1 — Regime | `Step1Regime.tsx` | `GET /api/v1/regime/state` | STEP1 |
| 2 | 📊 Step 2 — PCA Signals | `Step2Pca.tsx` | `GET /api/v1/signals/pca` + WS | STEP2 |
| 3 | 📦 Step 3 — Trade Preview | `Step3Preview.tsx` (existe partiel) | `POST /api/v1/preview` | STEP3 |
| 4 | 🚀 Step 4 — Execution | `Step4Execution.tsx` | `POST /api/v1/orders` (proxy) | STEP4 |
| 5 | 📈 Step 5 — Active Positions | `Step5Positions.tsx` | `/positions/live` + WS exits | STEP5 |
| 6 | 🧪 Backtest | `BacktestRunner.tsx` | `/api/v1/backtest/*` | BACKTEST |

Les 8 onglets actuels restent en sous-rubrique « Dev tools » (validation bas-niveau).

---

## 5. Sources de vérité par data domain

| Domaine | Owner write | Topic Redis | Tables Postgres |
|---|---|---|---|
| Spot/ticks | market-data | `ticks:*` | (cache mémoire seulement) |
| OHLC daily | market-data | — | `ohlc_daily` |
| Surface IV | vol-engine | `vol:surface` | `vol_surfaces`, `svi_params`, `ssvi_params` |
| Régime | vol-engine | `regime:state` | `regime_states` (à créer step 1) |
| PCA model | pca-fitter | `pca:refit` | `pca_models` (à créer step 2) |
| PCA signals | vol-engine (consume model) | `signal:pca` | `signals_pca` (à créer step 2) |
| Trade previews | api | — | `trade_previews` (à créer step 3) |
| Orders/fills/positions | execution-engine | `order:*`, `fill:*` | `orders`, `trades`, `positions`, `position_snapshots`, `order_events`, `account_snaps` |
| Risk/exits | risk | `action:hedge`, `action:close` | `position_pnl_snapshots`, `exit_decisions` (à créer step 5) |
| Backtest results | backtest-runner | — | `backtest_runs`, `backtest_folds` (à créer) |
| Config | api (admin) | `config:changed` | `vol_config`, `risk_config`, `exec_config` |

---

## 6. Index des docs container (ce dossier)

- [container_market_data.md](architecture/container_market_data.md)
- [container_vol_engine.md](architecture/container_vol_engine.md)
- [container_pca_fitter.md](architecture/container_pca_fitter.md)
- [container_snapshot_collector.md](architecture/container_snapshot_collector.md)
- [container_risk.md](architecture/container_risk.md)
- [container_execution_engine.md](architecture/container_execution_engine.md)
- [container_db_writer.md](architecture/container_db_writer.md)
- [container_api.md](architecture/container_api.md)
- [container_frontend.md](architecture/container_frontend.md)
- [container_backtest_runner.md](architecture/container_backtest_runner.md)

Steps & policy : `STEP1_*` … `STEP5_*`, `BACKTEST_WALK_FORWARD.md`, `DECISIONS.md`.
