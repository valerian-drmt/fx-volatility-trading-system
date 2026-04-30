# Vol — architecture système

> Quels containers font quoi, quelles données circulent où, quelles tables
> sont alimentées. Réf rapide pour comprendre le pipeline vol côté
> infrastructure. Pour les formules math des indicateurs eux-mêmes,
> voir [`VOL_MODELS.md`](./VOL_MODELS.md).

---

## Vue d'ensemble

5 containers participent au pipeline vol :

```
                     ┌─────────────┐
                     │ ib-gateway  │  source IB (chain FOP + OHLC)
                     └──────┬──────┘
                            │
                            ▼
   ┌──────────────┐  ┌────────────┐
   │ market-data  │─▶│ vol-engine │  cycle 180s : compute surface
   │ (spot tick)  │  └─────┬──────┘
   └──────────────┘        │
                           ├──────▶ Redis (latest_vol_surface, latest_signals,
                           │        vol_update channel, heartbeat:vol_engine)
                           │
                           └──────▶ Redis db_events ──▶ db-writer ──▶ Postgres
                                                                      (vol_surfaces,
                                                                       signals, svi_params,
                                                                       ssvi_params)
                ┌──────────────┐
                │     api      │  endpoints /vol/*, /signals, /admin/config
                └──────┬───────┘  + WS bridge /ws/vol
                       │
                       ▼
                  ┌─────────────┐
                  │ frontend +  │  consume via REST/WS
                  │ risk-engine │  (risk lit la surface pour greeks portfolio)
                  └─────────────┘
```

---

## vol-engine — l'acteur principal

Container `fxvol-vol-engine`, image `fx-options-vol-engine:local`, clientId IB
**2**, IP statique `172.20.0.11`. Code : `src/services/vol/`.

### Cycle de 180s

```
1. read latest_spot:EURUSD                          (Redis, lu market-data)
2. fetch FOP chain via IB (~30s, 6 tenors × 36)     (chain_fetcher.py)
3. fetch OHLC daily 1Y (cached 30 min)              (historical_fetcher.py, whatToShow=TRADES)
4. compute Yang-Zhang RV → _rv_full_pct
5. fit GARCH(1,1) per tenor → _garch
6. fit HAR-RV per tenor → _har
7. convert P → Q via VRP → _fair_q
8. fit SVI per tenor → _svi
9. fit SSVI surface-wide → _ssvi
10. derive signals CHEAP/FAIR/EXPENSIVE par tenor
11. SET latest_vol_surface + PUBLISH vol_update
12. fan db_events → db-writer (vol_surfaces, signals, svi_params, ssvi_params)
13. SET heartbeat:vol_engine
```

Si une étape échoue : cycle continue (fallback gracieux). Voir §Failure modes.

### Inputs

| Donnée | Source | Format |
|---|---|---|
| Spot EURUSD | Redis `latest_spot:EURUSD` (str float) | scalaire `1.17xxx` |
| FOP chain (greeks IB) | `ib.reqMktData(genericTickList="100")` sur ~216 contrats EUU | `{tenor: [(delta, iv, strike)]}` |
| OHLC daily 1Y | `ib.reqHistoricalDataAsync(CONTFUT EUR, "1 Y", "1 day", "TRADES")` | `pd.DataFrame[date, open, high, low, close]` |
| Config hot-reload | Redis `config:changed` (pub/sub) | JSON `{version, config}` |

### Outputs

**Redis** :

| Key | TTL | Contenu | Consumer |
|---|---|---|---|
| `latest_vol_surface:EURUSD` | 600s | JSON `{symbol, timestamp, surface}` (cf. §Schéma) | api `/vol/*`, frontend, risk-engine |
| `latest_signals:EURUSD` | 600s | JSON `{symbol, timestamp, signals[]}` | api `/signals`, frontend |
| `heartbeat:vol_engine` | 300s | ISO timestamp | healthcheck, EngineHealth |
| `vol_update` (channel) | — | Duplicate du surface payload | api WS bridge → `/ws/vol` |

**Postgres** (via `db_events` Redis → db-writer) :
- `vol_surfaces` (1 row / cycle) — payload complet
- `signals` (N rows / cycle, 1 par tenor)
- `svi_params` (N rows / cycle, 1 par tenor)
- `ssvi_params` (1 row / cycle)

---

## market-data — fournisseur de spot

Container `fxvol-market-data`, clientId IB **1**, IP `172.20.0.10`. Subscribe
au tick stream EURUSD via IB et publie `latest_spot:EURUSD` sur Redis (TTL 30s).

vol-engine en dépend : si `latest_spot` absent au début du cycle → cycle skip
silencieux (logging only).

---

## ib-gateway — source IB unique

Container `fxvol-ib-gateway`, image `gnzsnz/ib-gateway`. Expose port 4002
(API) accessible uniquement aux IPs whitelistées dans Trusted IPs. Liste
courante : `127.0.0.1, 172.20.0.10/.11/.12/.13/.14`.

vol-engine fait 2 types d'appels :
- `reqMktData` (greeks IB sur chain options) — temps réel
- `reqHistoricalDataAsync` (OHLC futures CONTFUT EUR) — historique

---

## db-writer — pont Redis → Postgres

Container `fxvol-db-writer`. Pas d'IB connection. Subscribe au channel Redis
`db_events`, batch les events par table, INSERT bulk en Postgres avec
`ON CONFLICT DO NOTHING` sur les contraintes uniques (timestamp + underlying
+ tenor).

vol-engine émet 4 types d'events vol :

| `frame.table` | Source | Cardinalité |
|---|---|---|
| `vol_surfaces` | Cycle complet | 1/cycle |
| `signals` | Par tenor | 6/cycle |
| `svi_params` | Par tenor | 6/cycle |
| `ssvi_params` | Surface-wide | 1/cycle |

---

## api — endpoints REST/WS

Container `fxvol-api`, IP `172.20.0.13`. Pas d'IB direct (split en R9 vers
`execution-engine` pour les orders). Endpoints vol-related :

### Lecture surface

| Endpoint | Source | Retour |
|---|---|---|
| `GET /api/v1/vol/surface?symbol=EURUSD` | Redis `latest_vol_surface` | Surface JSON complète |
| `GET /api/v1/vol/surface/at/{ts}` | Postgres `vol_surfaces` | Surface historique |
| `GET /api/v1/vol/term-structure?symbol=EURUSD` | Redis | `{pillars: [{tenor, dte, sigma_atm_pct, sigma_fair_pct, ...}]}` |
| `GET /api/v1/vol/smile/{tenor}?symbol=EURUSD` | Redis | `{points: [{strike, iv_pct, delta_label}], svi_curve, sigma_fair_pct, rv_pct}` |

### Signals

| Endpoint | Filtres | Retour |
|---|---|---|
| `GET /api/v1/signals` | `underlying, tenor, signal_type, limit, latest_per_tenor` | `[SignalRow]` (incluant `sigma_fair_p`, `vrp_vol_pts`) |

### Configuration admin

| Endpoint | Action |
|---|---|
| `GET /api/v1/admin/config` | Latest version + dump complet |
| `GET /api/v1/admin/config/schema` | JSON Schema de `VolTradingConfig` |
| `PUT /api/v1/admin/config` | INSERT v_n+1 avec deep-merge patch + PUBLISH `config:changed` |
| `GET /api/v1/admin/config/history?limit=N` | Versions précédentes (audit) |
| `POST /api/v1/admin/config/revert/{version}` | Revert à une version antérieure |

### WebSocket

`ws://localhost/ws/vol` — bridge qui forward le channel Redis `vol_update`
vers les clients connectés. Payload identique au cache `latest_vol_surface`.

---

## risk-engine — consumer

Container `fxvol-risk`, clientId IB **3**, IP `172.20.0.12`. Lit la surface
vol pour calculer les greeks portfolio :
- Reads `latest_vol_surface:EURUSD` au début de chaque cycle (2s)
- Reads `latest_spot:EURUSD`
- Computes `latest_greeks:portfolio` (delta/gamma/vega/theta agrégés)

Pas writeur de tables vol — read-only consumer.

---

## Schémas DB

### `vol_surfaces`

| Col | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `timestamp` | timestamptz | UTC |
| `underlying` | varchar(20) | "EURUSD" |
| `spot` | numeric(15,8) | F au moment du cycle |
| `forward` | numeric(15,8) | = spot pour FX |
| `surface_data` | JSONB | Payload complet (cf. §Schéma payload ci-dessous) |
| `scan_duration_s` | numeric(6,2) | Réservé (pas wired) |

UNIQUE `(timestamp, underlying)`.

### `signals`

| Col | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `timestamp` | timestamptz | UTC |
| `underlying` | varchar(20) | |
| `tenor` | varchar(5) | "1M".."6M" |
| `dte` | int | days to expiry approx |
| `sigma_mid` | numeric(8,5) | % Q-measure (IV ATM IB × 100) |
| `sigma_fair` | numeric(8,5) | % Q-measure (fair forecast) |
| `sigma_fair_p` | numeric(8,5) | % P-measure (HAR ou GARCH) |
| `vrp_vol_pts` | numeric(8,5) | Q − P spread |
| `ecart` | numeric(8,5) | sigma_mid − sigma_fair |
| `signal_type` | varchar(15) | CHEAP / FAIR / EXPENSIVE |
| `rv` | numeric(8,5) | % Yang-Zhang RV (P-measure, full window) |

UNIQUE `(timestamp, underlying, tenor)`. CHECK signal_type ∈ {CHEAP, FAIR, EXPENSIVE}.

### `svi_params`

| Col | Type | Notes |
|---|---|---|
| `id`, `timestamp`, `underlying`, `tenor` | std | |
| `a, b, rho, m, sigma` | numeric(10,7) | 5 params SVI raw |
| `rmse_fit` | numeric(10,7) | erreur dans l'espace total variance |
| `butterfly_g_min` | numeric(10,7) | < 0 = densité négative ⚠ |

UNIQUE `(timestamp, underlying, tenor)`.

### `ssvi_params`

| Col | Type | Notes |
|---|---|---|
| `id`, `timestamp`, `underlying`, `spot` | std | |
| `eta, gamma, rho` | numeric(10,7) | 3 params SSVI |
| `rmse_fit` | numeric(10,7) | erreur sur tous les obs cross-tenors |
| `calendar_arb_free` | bool | True si `2γ ≥ 1 − ρ²` |

UNIQUE `(timestamp, underlying)`.

### `vol_config`

| Col | Type | Notes |
|---|---|---|
| `version` | int PK | append-only, jamais update |
| `config` | JSONB | `VolTradingConfig.model_dump()` complet |
| `updated_at`, `updated_by`, `comment` | std | audit |

INDEX `(version DESC)` pour latest fetch O(log N).

---

## Schéma du payload `latest_vol_surface`

```jsonc
{
  "symbol": "EURUSD",
  "timestamp": "2026-04-30T08:30:01Z",
  "surface": {
    // Tenors publics — pillars δ PCHIP-interpolés
    "1M": {
      "10dp": { "iv": 0.062, "strike": 1.155 },
      "25dp": { "iv": 0.060, "strike": 1.165 },
      "atm":  { "iv": 0.059, "strike": 1.171 },
      "25dc": { "iv": 0.060, "strike": 1.180 },
      "10dc": { "iv": 0.063, "strike": 1.195 }
    },
    // ... 2M-6M

    // Estimateurs P-measure (vol points = %)
    "_rv_full_pct": 7.23,
    "_har":   { "1M": {"sigma_har_pct": 6.85}, ... },
    "_garch": { "1M": {"sigma_model_pct": 6.92}, ... },

    // Conversion P → Q
    "_fair_q": {
      "1M": {
        "sigma_fair_p_pct": 6.85,
        "vrp_vol_pts": 0.6,
        "sigma_fair_q_pct": 7.45,
        "regime": "calm"
      },
      ...
    },

    // Smile fits (1 par tenor)
    "_svi": {
      "1M": { "a": 0.0001, "b": 0.012, "rho": -0.73, "m": 0.001,
              "sigma": 0.031, "rmse_fit": 1.1e-05, "butterfly_g_min": 0.0023 },
      ...
    },

    // Surface fit (cross-tenors)
    "_ssvi": {
      "eta": 1.69, "gamma": 0.41, "rho": -0.11,
      "rmse_fit": 1.6e-05, "calendar_arb_free": true
    }
  }
}
```

---

## Configuration `VolTradingConfig`

Définie dans `src/core/config/vol_params.py`. 8 sections :

| Section | Contenu | Wired runtime ? |
|---|---|---|
| `signal` | `threshold_vol_pts`, `model_p` ("har"/"garch"), z_thresholds, etc. | ✅ 2 fields hot-reloadable |
| `regime` | GMM components, event_dampener, vol_of_vol_window_days, sizing multiplier | ❌ pas wired |
| `sizing` | base_size, conviction_scaling, book_penalty, etc. | ❌ pas wired |
| `exit_rules` | signal_reverse, time_based, vega_stop_loss | ❌ pas wired |
| `surface` | tenors_days, delta_pillars, svi_rmse_max_warn, butterfly_check_grid | partiellement wired (tenors hardcodés) |
| `calibration` | har_components, ewma_lambda_fair_smile, walk-forward windows | ❌ pas wired |
| `delta_hedge` | static / threshold / scheduled rehedge | ❌ pas wired (risk-engine) |
| `structures` | factory mappings straddle/calendar/RR/butterfly | ❌ pas wired |

**Hot-reload mechanism** :

1. Admin appel `PUT /api/v1/admin/config` avec patch JSON
2. `config_service.update()` deep-merge sur version courante, valide via Pydantic
3. INSERT row v_n+1 dans `vol_config`
4. PUBLISH `config:changed` sur Redis avec payload `{version, config}`
5. `vol_engine._watch_config_changes` (main.py:28) consume → `engine.apply_config(cfg)`
6. Au prochain cycle : nouveaux paramètres effectifs

Les 6 sections "pas wired" sont définies pour ne pas casser le schéma quand
les phases de refactor ultérieures les brancheront.

---

## Failure modes

| Étape qui fail | Impact | Détection |
|---|---|---|
| `latest_spot:EURUSD` absent | Cycle skip silencieux | log `vol_cycle_skipped reason=no_spot` |
| FOP chain IB vide / TrustedIPs KO | Surface sans tenors publics | `surface = {}` post step, SVI/SSVI skipped |
| OHLC fetch fail (Error 162, etc.) | Pas de RV/HAR/GARCH | `_rv_full_pct = None` → `_fair_q = {}` → signaux fallback legacy GARCH-as-Q |
| GARCH non-cv | `_garch[tenor]` manquant | log `garch_projection_failed` |
| HAR `<` 42 bars | `_har = {}` | log info |
| SVI fit fail | tenor skipped dans `_svi` | row `svi_params` pas écrite |
| Butterfly violation | warning seulement, fit retourné | `butterfly_g_min < 0` flag |
| SSVI insufficient data | `_ssvi = None` | row `ssvi_params` pas écrite |
| Publish Redis fail | Cycle retourne False, retry next tick | log `publish_vol_update_failed` |
| Publish DB event fail | log error, **cycle continue** | row `vol_surfaces` manque ce cycle |

**Pas de circuit breaker** — le moteur continue. Détection via âge du
heartbeat (>300s = bloqué).

---

## Quick-reference pour debug

| Symptôme | Action |
|---|---|
| Heartbeat vol_engine > 300s | `docker logs fxvol-vol-engine --tail 100` |
| Pas de signaux dans `latest_signals` | Check `latest_spot` (market-data up ?), check chain fetch (IB Gateway TrustedIPs OK ?) |
| `signals` table vide en DB | db-writer down ? `docker logs fxvol-db-writer` |
| `_har`/`_garch` absents en surface | OHLC fetch fail — check `whatToShow=TRADES`, perms historical IB |
| `_fair_q` vide alors que `_har` présent | bug `vrp.detect_regime` — check inputs vol_level/vol_of_vol/term_slope |
| Tous signaux = FAIR | `signal.threshold_vol_pts` trop large — éditer via `/dev/vol` config editor |
| Butterfly arb sur un tenor | dataset chain pourri ce cycle (3 strikes alignés, IV bruitées) — réessayer prochain cycle |

---

## Liens code

- `src/services/vol/{main.py, engine.py, chain_fetcher.py, historical_fetcher.py}`
- `src/core/vol/{yang_zhang.py, garch.py, har_rv.py, svi.py, ssvi.py, vrp.py, pchip_smile.py}`
- `src/core/pricing/bs.py`
- `src/core/config/vol_params.py`
- `src/persistence/models.py` (VolSurface, Signal, SviParam, SsviParam, VolConfig)
- `src/api/routers/{vol.py, analytics.py, admin.py}`
- `src/api/services/config_service.py`
- `src/bus/{publisher.py, channels.py, keys.py}`
- `scripts/smoke/vol/0[1-5]_test_*.ipynb`
