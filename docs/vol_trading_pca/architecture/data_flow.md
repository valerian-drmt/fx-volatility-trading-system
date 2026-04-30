# Flux de données end-to-end

Description des chemins que prennent les données dans le système, du tick IB jusqu'à
la fermeture d'une position. Vue runtime (pas topologique — pour ça voir `service.md`).

---

## 1. Vue d'ensemble

```
                              IB Gateway
                                  │
                                  ▼
           ┌───────────────────────────────────────────┐
           │  Ingestion (market-data)                  │
           │  ticks, OHLC, chain                       │
           └───────────────────────┬───────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Vol modeling (vol-engine, snapshot, pca) │
           │  surface, RV, signaux Q et P              │
           └───────────────────────┬───────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Decision (régime + PCA z-scores)         │
           │  publié vers frontend → user gate         │
           └───────────────────────┬───────────────────┘
                                   │ user click
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Trade preview (api + risk)               │
           │  pricing + greeks + sizing + 7 checks     │
           └───────────────────────┬───────────────────┘
                                   │ user click submit
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Execution (execution-engine + IB)        │
           │  orders → fills → position created        │
           └───────────────────────┬───────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Active monitoring (risk, 60s)            │
           │  MTM + 5 exit rules + delta hedge         │
           └───────────────────────┬───────────────────┘
                                   │ exit triggered
                                   ▼
           ┌───────────────────────────────────────────┐
           │  Close (execution-engine)                 │
           │  P&L finalisé, audit complet              │
           └───────────────────────────────────────────┘
```

---

## 2. Étape par étape

### 2.1 Ingestion (continu)

```
IB Gateway ──tick──► market-data ──Redis SET ticks:eurusd──► (consumers)
                                ──Redis PUB ticks:eurusd──► vol-engine, risk
                                ──INSERT ohlc_daily────────► postgres (1×/jour)
```

- **Topics Redis publiés** : `ticks:eurusd`, `ticks:6e:front`, `chain:eurusd:<tenor>`
- **TTL** des K/V `ticks:*` : 5s (stale = no spot disponible)
- **Throttle PUBLISH** : 200ms par symbole (toujours mettre à jour la SET, throttle juste la PUB)
- **OHLC daily** : flush 00:05 UTC → table `ohlc_daily`. Backfill via script `scripts/backfill_ib_historical.py`.

### 2.2 Vol modeling (3 acteurs en parallèle)

#### 2.2.1 vol-engine (cycle 180s)

```
spot Redis  ──┐
chain IB    ──┼──► BS-invert IV ──► SVI per-tenor ──► SSVI surface
                                                            │
ohlc_daily  ──► Yang-Zhang RV ──► HAR-RV ──► σ_fair_p ─────┤
                                                            ▼
                                       VRP table ──► σ_fair_q par tenor
                                                            │
                                                            ▼
                                       signal CHEAP/FAIR/EXPENSIVE
                                                            │
              ┌─────────────────────────────────────────────┤
              ▼                                             ▼
   INSERT vol_surfaces / svi_params / ssvi_params / signals
   PUBLISH vol:surface, signal:vol, regime:state (step 1 wired)
   SET     latest_vol_surface (full payload JSON)
```

#### 2.2.2 snapshot-collector (cron horaire HH:00)

```
Redis GET latest_vol_surface
   │
   ▼
project on canonical 30-dim grid (5 tenors × 6 strikes)
   │
   ▼
INSERT vol_snapshots_30d
```

#### 2.2.3 pca-fitter (cron hebdo Sun 22:00)

```
SELECT * FROM vol_snapshots_30d WHERE timestamp > now() - 12 months
   │
   ▼
PCA fit (3 components, sign correction)
   │
   ▼
INSERT pca_models (is_active = true, previous → false)
PUBLISH pca:refit (notify vol-engine de recharger)
```

#### 2.2.4 vol-engine consume PCA (chaque cycle 180s)

```
SELECT loadings FROM pca_models WHERE is_active = true LIMIT 1
   │
   ▼
project surface live 30-dim → 3 z-scores (PC1 / PC2 / PC3)
   │
   ▼
flag actionable = |z| > seuil ET stable depuis ≥ 3 cycles
   │
   ▼
INSERT signals_pca + PUBLISH signal:pca
```

### 2.3 Decision (frontend)

```
Frontend Step 1 tab ──GET /api/v1/regime/state──► api ──SELECT regime_states latest──► postgres
Frontend Step 2 tab ──WS /ws/signals/pca──────► api ──SUB signal:pca──► redis

User décide : si regime ≠ INSUFFICIENT_DATA et z-score actionable → ouvre Step 3.
```

### 2.4 Trade preview (request-driven)

```
Frontend POST /api/v1/preview {structure, qty}
   │
   ▼
api router pricing :
   │
   ├──► vol-engine RPC (read latest_vol_surface) ──► σ_mid + σ_fair_q par leg
   │
   ├──► risk module (BS pricing) ──► price + greeks par leg + aggregé
   │
   ├──► sizing rules (table risk_config) ──► qty proposée
   │
   └──► 7 pre-submit checks (cf. STEP3 §8)
            │
            ▼
   INSERT trade_previews (audit, optionnel)
   return TradePreviewResponse {valid_for_submit, reasons[], price, greeks, ...}
```

### 2.5 Execution (event-driven)

```
Frontend POST /api/v1/orders {preview_id, ...}
   │
   ▼
api router orders ──httpx──► execution-engine /internal/orders
                                       │
                                       ▼
                              IB place_order (clientId=5)
                                       │
                                       ├──► IB ack ──► INSERT order_events (ack)
                                       │
                                       ▼
                              poll fills @ 1s ──► INSERT trades
                                       │
                                       ▼
                              build position ──► INSERT positions, position_snapshots
                                       │
                                       ▼
                              return OrderResult to api → frontend
```

Boucle `position_sync_loop` (1s) tourne en parallèle :

```
loop @ 1s:
  IB.list_orders         → upsert orders
  IB.list_fills          → upsert trades
  IB.list_positions      → upsert positions, INSERT position_snapshots
  IB.account_summary     → INSERT account_snaps (currencies whitelist 6 tags)
  Redis SET heartbeat:execution
```

### 2.6 Active monitoring (cycle 60s)

```
risk loop @ 60s:
  SELECT positions WHERE status='open'
     │
     ▼
  pour chaque position :
     greeks live (BS sur surface courante)
     P&L decomp (spot, vol, theta, residual)
     check 5 exit rules (TP, SL, time-decay, mean-reversion, regime-flip)
     compute delta hedge size si |Δ_total| > deadband
     │
     ├──► INSERT position_pnl_snapshots
     ├──► si exit triggered : INSERT exit_decisions + PUBLISH action:close
     └──► si hedge needed   : PUBLISH action:hedge
```

### 2.7 Close (event-driven)

```
execution-engine sub action:close
   │
   ▼
build closing order (opposite side, same qty)
   │
   ▼
IB place_order ──► fills ──► positions.status = 'closed'
                                  │
                                  ▼
                          INSERT order_events (close_ack, close_fill)
                          P&L finalisé dans positions.realized_pnl
```

---

## 3. Topics Redis — annuaire complet

| Topic | Publisher | Subscribers | Cadence |
|---|---|---|---|
| `ticks:eurusd` | market-data | vol-engine, risk, frontend (WS) | 200ms |
| `ticks:6e:front` | market-data | vol-engine | 200ms |
| `chain:eurusd:*` | market-data | vol-engine | event |
| `latest_vol_surface` (SET) | vol-engine | api, snapshot-collector | 180s |
| `vol:surface` | vol-engine | db-writer, frontend (WS) | 180s |
| `signal:vol` | vol-engine | db-writer, frontend (WS) | 180s |
| `signal:pca` | vol-engine | db-writer, frontend (WS) | 180s |
| `regime:state` | vol-engine | db-writer, frontend (WS) | 180s |
| `pca:refit` | pca-fitter | vol-engine | hebdo |
| `risk:greeks` | risk | db-writer, frontend (WS) | 60s |
| `action:hedge` | risk | execution-engine | event |
| `action:close` | risk | execution-engine | event |
| `exit:decision` | risk | db-writer, frontend (WS) | event |
| `config:changed` | api admin | tous | event |
| `heartbeat:*` (SET) | chacun | api `/ready` | 1-30s |

---

## 4. Tables Postgres — annuaire write-paths

| Table | Owner write | Sync vs async |
|---|---|---|
| `ohlc_daily` | market-data | sync (1× / jour) |
| `vol_surfaces` | vol-engine | sync |
| `svi_params`, `ssvi_params` | vol-engine | sync |
| `signals` | vol-engine | sync |
| `vol_snapshots_30d` | snapshot-collector | sync |
| `pca_models` | pca-fitter | sync |
| `signals_pca` | vol-engine (consumer) | sync |
| `regime_states` | vol-engine | sync |
| `trade_previews` | api | sync (audit only) |
| `orders`, `trades`, `positions`, `position_snapshots`, `order_events`, `account_snaps` | execution-engine | sync (1s loop) |
| `position_pnl_snapshots`, `exit_decisions` | risk | async via db-writer |
| `vol_config`, `risk_config`, `exec_config` | api admin | sync |
| `backtest_runs`, `backtest_folds`, `backtest_trades` | backtest-runner | sync |

> Règle (ADR-004) : Postgres = source de vérité. Toute donnée *importante* est persistée
> AVANT publish Redis. Redis = cache + bus pubsub, jamais authoritative.

---

## 5. Latences cibles

| Hop | Cible p50 | Cible p99 |
|---|---|---|
| IB tick → Redis SET | < 50 ms | < 200 ms |
| vol-engine cycle complet | < 60 s | < 120 s (sinon skip) |
| /preview (request → response) | < 500 ms | < 2 s |
| order submit → IB ack | < 500 ms | < 3 s |
| risk loop complet | < 30 s | < 60 s |
| WS fan-out latency | < 100 ms | < 500 ms |

Si dépassement persistant, le service publie `degraded:<name>:1` et l'UI affiche un
banner. Aucun service ne crash sur dépassement (pas de hard timeout suicidaire).

---

## 6. Flux backtest (offline)

Le backtest **n'utilise pas Redis** (le pubsub serait artificiel en replay).

```
backtest-runner :
  pour chaque fold (walk-forward) :
    train_data = vol_snapshots_30d WHERE ts < fold_start
       │
       ▼
    pca-fitter CLI mode --as-of fold_start ──► pca_models_backtest
       │
       ▼
    test_data = vol_snapshots_30d WHERE fold_start <= ts < fold_end
       │
       ▼
    pour chaque snapshot test :
       project z-scores → simulate trade preview → simulate fill (cost model)
       run risk sim → exit rules → simulate close
       │
       ▼
    INSERT backtest_trades, backtest_folds
       │
       ▼
  aggregate → INSERT backtest_runs (verdict, metrics)
```

Strict cutoff (ADR-301) : aucune donnée future ne fuit dans le fold. Garanti par
la clause `WHERE ts < current_fold_time` partout.
