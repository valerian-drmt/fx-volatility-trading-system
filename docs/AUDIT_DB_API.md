# Audit DB + API — fx-volatility-trading-system

> **Objectif** : maximiser la reproductibilité ex-post d'un signal et d'un trade, sous contrainte de coût d'écriture borné par le throughput de chaque engine.
> **Méthode** : pour chaque PnL réalisé à `t+Δ`, le dataset doit permettre de reconstruire (a) état marché à `t`, (b) état modèle à `t`, (c) décision prise à `t`, (d) paramètres système à `t`. Tout ce qui rate ce critère = manque.
> **Source d'audit** : `postgres-architecture.md` (10 tables) + `API_ENDPOINTS.md` (30 endpoints).
> **Verdict global** : schéma cohérent sur le happy-path (un fill → on retrouve son contexte). Faille structurelle : tout ce qui n'est pas un fill ou un signal CHEAP/EXPENSIVE/FAIR est invisible.

---

## 1. Diagnostic synthétique

### Ce qui est correct (à conserver tel quel)

| Composant | Justification |
|---|---|
| `vol_config` versionnée append-only | PK = version, hot-reload via Redis pub/sub. Pattern propre, pas de UPDATE destructif. |
| Cycles d'écriture batchés par `AsyncDatabaseWriter` | Découplage engine ↔ PG via queue async, batch 50 rows / 500ms. Limite l'I/O pression. |
| Idempotence `ON CONFLICT DO NOTHING` sur `(timestamp, underlying[, tenor])` | Retry-safe. Critique pour engines qui peuvent rejouer un cycle. |
| Séparation Redis (TTL court live) vs PG (audit historique) | Bonne ligne de partage. Ticks tick-by-tick en PG = inutile et coûteux. |
| Endpoints cockpit (`regime`, `pca-signals`, `trade-preview`, `model-health`) | C'est là que vit la thèse trading. Bien découpé en read-time vs compute-time. |
| WebSocket à 3 channels (`/ws/ticks`, `/ws/vol`, `/ws/risk`) | Granularité minimale suffisante. Pas de sur-ingénierie. |
| FK `position_snapshots → positions` cascade vs `trades → positions` nullable sans cascade | Préserve l'audit trail des trades même en cas de purge positions. |
| GIN sur `vol_surfaces.surface_data` | Bon index pour requêtes JSONB structurées. |

### Ce qui pose problème (à corriger ou compléter)

Classification par **type de réfutation impossible aujourd'hui** :

1. **Couche exécution incomplète** → impossible de mesurer le slippage, les rejets, les annulations
2. **Couche stratégie absente** → impossible d'attribuer le PnL multi-stratégie
3. **Lien signal → décision absent** → impossible de calculer le hit rate live
4. **Diagnostics modèle insuffisants** → impossible d'exclure les trades pris sur modèles dégradés
5. **Contexte marché absent** → impossible de conditionner l'edge par régime/event
6. **Risk limits + breaches non versionnés** → la thèse "greeks bornés" sans audit trail

---

## 2. À AJOUTER

### 2.1 Tables PG manquantes

#### `orders` (critique — bloquant trading-system v1)

`trades` ne capture que les fills. Toute la couche pré-fill est invisible : ordres rejetés, cancelled avant fill, partial fills, slippage par rapport au mid au moment du submit.

```sql
orders {
    int id PK
    varchar(50) ib_order_id UK
    int position_id FK NULL              -- une position peut requérir N ordres
    int signal_id FK NULL                -- traçabilité signal → ordre
    int strategy_id FK NULL              -- multi-strat
    varchar(20) symbol
    enum order_type "MKT|LMT|STP|STP_LMT"
    enum side "BUY|SELL"
    numeric quantity (15,4)
    numeric limit_price NULL
    numeric stop_price NULL
    enum status "PENDING|SUBMITTED|ACCEPTED|PARTIAL|FILLED|CANCELLED|REJECTED"
    varchar(200) reject_reason NULL
    timestamptz submitted_at
    timestamptz last_event_at
    numeric mid_at_submission NULL       -- pour calcul slippage
    numeric spread_at_submission NULL
}
```

**Conséquence** : `trades.order_id FK → orders.id` au lieu de `trades.position_id` direct (preserver `trades.position_id` est OK pour requêtes rapides, mais ajouter `trades.order_id` comme nouvelle FK).

#### `signal_decisions` (critique — calcul hit rate live)

1:1 avec `signals`. Sans cette table, impossible de distinguer signal vu-et-tradé / vu-et-bloqué-risk / vu-et-ignoré-filtre.

```sql
signal_decisions {
    int id PK
    int signal_id FK UNIQUE
    enum decision "TRADED|BLOCKED_RISK|BLOCKED_LIQUIDITY|IGNORED_FILTER|NO_ACTION"
    varchar(200) reason NULL
    int order_id FK NULL                 -- si decision == TRADED
    timestamptz timestamp
}
```

Volume : ~3.4k rows/jour (idem signals). Trivial.

#### `strategies` + `strategy_runs` (haute — avant multi-strat)

Aucune attribution PnL par stratégie aujourd'hui. `vol_config` est globale. Bloque tout passage multi-strat.

```sql
strategies {
    int id PK
    varchar(50) name UK
    enum status "ACTIVE|PAUSED|RETIRED"
    jsonb params                         -- snapshot config au start
    timestamptz created_at
    timestamptz retired_at NULL
}

strategy_runs {                          -- 1 row par session live
    int id PK
    int strategy_id FK
    timestamptz started_at
    timestamptz ended_at NULL
    enum status "RUNNING|STOPPED|CRASHED"
    int vol_config_version FK            -- traçabilité config exacte
}
```

#### `model_runs` (haute — exclusion trades sur modèle dégradé)

`vol_surfaces.scan_duration_s` est le seul diagnostic modèle. Manque tout le reste.

```sql
model_runs {                             -- 1 row par cycle VolEngine
    int id PK
    timestamptz timestamp UK1
    varchar(20) underlying UK1
    int n_options_chain                  -- options lues depuis IB
    int n_options_filtered               -- après filtres liquidité/spread
    numeric svi_rmse_avg NULL
    numeric ssvi_rmse NULL
    bool butterfly_arb_free
    bool calendar_arb_free
    bool garch_converged NULL
    int garch_n_iter NULL
    numeric garch_loglik NULL
    numeric pca_var_pc1 NULL
    numeric pca_var_pc2 NULL
    numeric w1_distance_to_prev NULL     -- distance Wasserstein vs surface t-1
    enum quality "HEALTHY|DEGRADED|UNRELIABLE"
    jsonb warnings                       -- non bloquants
}
```

Volume : ~480 rows/jour (1 par cycle). Trivial.

#### `risk_limits` + `risk_breaches` (haute — la thèse trading)

Greeks bornés = thèse centrale. Aujourd'hui les limites vivent dans `vol_config` JSONB, sans table de breaches séparée.

```sql
risk_limits {                            -- snapshot config-derived
    int id PK
    int config_version FK
    numeric delta_max_usd
    numeric vega_max_usd
    numeric gamma_max_usd
    numeric theta_max_usd
    numeric notional_max_usd
    numeric drawdown_max_pct
    timestamptz effective_from
}

risk_breaches {                          -- 1 row à chaque dépassement
    int id PK
    timestamptz timestamp
    varchar(20) metric                   -- "delta_usd", "vega_usd", ...
    numeric value
    numeric limit_value
    numeric breach_pct
    enum action "WARNING|BLOCK_NEW_ORDERS|FORCE_LIQUIDATION"
    int order_id FK NULL                 -- si l'ordre a été bloqué
}
```

#### `market_events` + `regime_snapshots` (moyenne — analyse conditionnelle)

FX vol mean-reverts autour des events macro. Tu ne peux pas conditionner ton signal sur "T-2 days avant FOMC" aujourd'hui. Le régime est calculé par `core/vol/vrp.py:detect_regime` mais rien n'est persisté → pas d'analyse conditionnelle d'edge par régime.

```sql
market_events {                          -- ingéré offline depuis ForexFactory/BBG
    int id PK
    timestamptz timestamp
    varchar(10) currency                 -- USD, EUR, GBP, ...
    enum impact "LOW|MEDIUM|HIGH"
    varchar(200) name                    -- "FOMC Rate Decision", "NFP", ...
    numeric forecast NULL
    numeric actual NULL
    numeric previous NULL
}

regime_snapshots {                       -- écrit par VolEngine à chaque cycle
    int id PK
    timestamptz timestamp UK
    varchar(20) underlying UK
    enum regime "LOW_VOL|NORMAL|STRESS|CRISIS"
    numeric vix_level NULL               -- ou DXY ou autre proxy
    numeric realized_vol_5d NULL
    numeric realized_vol_20d NULL
    numeric vol_of_vol NULL
}
```

### 2.2 Endpoints API manquants

| # | Endpoint | Justification | Coût | Priorité |
|---|---|---|---|---|
| 1 | `POST /api/v1/orders` + `GET /api/v1/orders` | Aucun moyen de placer un ordre depuis l'UI (système read-only). | 1-2j | **Bloquant v1** |
| 2 | `GET /api/v1/orders/{id}/lifecycle` | Suivre PLACED → ACCEPTED → PARTIAL → FILLED/CANCELLED/REJECTED | 1j | Haute |
| 3 | `GET /api/v1/signals/{id}/decision` | Hit rate live mesurable | 1j | Haute |
| 4 | `GET /api/v1/risk/limits` + `GET /api/v1/risk/breaches` | Audit trail des dépassements | 1j | Haute |
| 5 | `GET /api/v1/vol/model-diagnostics?since=...` | Détail RMSE/n_obs/arb par cycle | 1j | Moyenne |
| 6 | `GET /api/v1/strategies` + attribution PnL | Bloque le multi-strat | 2j | Haute (avant multi-strat) |
| 7 | `GET /api/v1/replay?ts=...&horizon=...` | Reconstituer état complet à t (debug post-mortem) | 2j | Moyenne |
| 8 | `GET /api/v1/calendar` | Conditional analysis sur events macro | 1j | Haute pour alpha |
| 9 | `POST /api/v1/positions/{id}/close` | Fermeture manuelle d'urgence | 0.5j | Moyenne |
| 10 | `GET /api/v1/data-quality` | Fraîcheur + intégrité (différent de `/health/extended` qui est liveness) | 1j | Moyenne |

---

## 3. À OPTIMISER

### 3.1 `account_snaps.currencies` JSONB

Pour des requêtes time-series par devise (ex: balance EUR sur 30j), le JSONB est inadapté. Deux options :

- **Option A (recommandée)** : table fille `account_currency_snaps(id, snap_id FK, currency, balance)`. Volume estimé : 24 snaps/h × 24h × 8 currencies ≈ 4.6k rows/jour. Négligeable.
- **Option B** : GIN index sur `currencies` JSONB. Permet `WHERE currencies @> '{"EUR": ...}'` mais syntaxe SQL plus lourde côté analytics.

### 3.2 `positions` — ajout colonne `strategy_id FK NULL`

Sans cette colonne, l'attribution PnL par stratégie passe par des heuristiques fragiles (matching sur timestamps + paramètres). FK directe = trivial à requêter.

### 3.3 `signals` — ajout colonne `model_run_id FK`

Lien direct entre un signal et le cycle modèle qui l'a produit. Permet de filtrer les signaux issus de modèles dégradés au backtest sans re-jointure complexe.

### 3.4 Index manquant : `(strategy_id, timestamp DESC)` sur `positions`, `trades`, `position_snapshots`

Une fois `strategy_id` ajouté, ces index sont nécessaires pour les requêtes "PnL de la strat X sur les 30 derniers jours". Sans eux = full scan.

### 3.5 Migration `254fc54bb36f_add_col_x` à squasher

Migration auto-générée placeholder. Soit la supprimer, soit la fusionner dans une migration nommée. Sinon dérive nominale du schéma vs intention.

### 3.6 `surface_data` JSONB — schéma JSON contractualisé

Aujourd'hui `surface_data jsonb "ATM + smile pillars"` mais la structure interne n'est ni validée ni documentée formellement. Risque : un changement silencieux dans VolEngine casse le frontend ou les notebooks de recherche.

→ Ajouter un JSON Schema versionné dans `core/vol/surface_schema.py`, validé à l'écriture par Pydantic v2, exposé via `/api/v1/admin/schemas/surface_data`.

### 3.7 Procédure de rollback Alembic non testée (angle mort)

Aucune mention de tests de migration `downgrade()` dans la doc. Sur une chaîne 001 → 010 sans rollback testé, un bug en prod = restore from backup avec downtime. À ajouter au CI : `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` sur DB de test.

### 3.8 Coût de coordination multi-engine non visible

3 engines + writer + executor + API = 5 agents async qui partagent Redis et PG. Aucune table de heartbeat persistée (heartbeats vivent en Redis avec TTL). En cas de crash discret d'un engine, l'historique des outages n'est pas reconstituable.

→ Ajouter `engine_heartbeats(id, engine_name, timestamp, status, latency_ms)` insertée toutes les 60s par chaque engine. Volume négligeable (3 engines × 1440 min = 4.3k rows/jour).

---

## 4. À SUPPRIMER

| Élément | Raison | Action |
|---|---|---|
| `position_snapshots.spot` | Redondant avec `vol_surfaces.spot` au même `timestamp` (ou très proche). Joinable. | Supprimer la colonne, jointure côté requête sur `(timestamp, underlying)` |
| `position_snapshots.iv` | Idem : récupérable via `signals` ou `svi_params` au même tenor. | Supprimer après vérification d'usage frontend |
| Migration `254fc54bb36f_add_col_x` | Placeholder auto-généré, pas de PR référencée | Squasher dans la migration suivante |

**Attention** : avant suppression de `spot`/`iv` dans `position_snapshots`, vérifier l'usage côté `BookPanel` detail view. Si la jointure sur 2 tables coûte trop côté UI, garder en tant que **denormalization volontaire** mais l'expliciter dans le schéma (commentaire `-- denorm: copie pour lecture rapide BookPanel`).

---

## 5. Plan d'exécution séquencé

### Étape 1 (1-2 semaines) — Bloquant v1.0

```
[1] orders table + 2 endpoints (POST/GET)         ~ 2j
[2] trades.order_id FK + backfill                  ~ 0.5j
[3] signal_decisions table + endpoint              ~ 1j
[4] risk_limits + risk_breaches tables + endpoint  ~ 1j
[5] POST /positions/{id}/close                     ~ 0.5j
[6] Tests Alembic upgrade/downgrade au CI          ~ 0.5j
```

### Étape 2 (1 semaine) — Multi-strat + diagnostics

```
[7] strategies + strategy_runs tables              ~ 1j
[8] positions.strategy_id FK + backfill            ~ 0.5j
[9] model_runs table + endpoint                    ~ 1j
[10] signals.model_run_id FK                       ~ 0.5j
[11] engine_heartbeats persistés                   ~ 0.5j
[12] surface_data JSON Schema versionné            ~ 1j
```

### Étape 3 (1 semaine) — Contexte + analyse conditionnelle

```
[13] market_events table + ingestion script        ~ 1.5j
[14] regime_snapshots table + écriture VolEngine   ~ 0.5j
[15] /api/v1/calendar endpoint                     ~ 0.5j
[16] /api/v1/replay endpoint                       ~ 2j
[17] /api/v1/data-quality endpoint                 ~ 1j
[18] account_currency_snaps table fille            ~ 0.5j
```

**Total estimé** : ~18-20 jours-homme. Compatible avec un tag v1.0 dans 4 semaines en exécution stricte.

---

## 6. Sensibilité aux hypothèses

Le plan ci-dessus suppose :

| Hypothèse | Si fausse |
|---|---|
| trading-system v1 = placement d'ordres depuis l'UI (pas read-only) | Étape 1 [1][2][5] chutent — gain ~3j |
| Multi-stratégie visé à terme | Étape 2 [7][8] chutent — gain ~1.5j |
| Hit rate live = critère d'évaluation HF | `signal_decisions` chute — gain ~1j (mais perte massive sur la trajectoire HF) |
| Backtest sur tes propres données live capturées | `model_runs` + `regime_snapshots` chutent — gain ~2j |
| Tests de migration acceptables hors CI (manuel) | Test CI chute — gain ~0.5j (risque rollback en prod) |

Recommandation : ne pas trader [3] `signal_decisions` ni [9] `model_runs` contre du temps. Ce sont les deux tables qui transforment ce repo de "projet perso fonctionnel" en "système auditable par un quant senior".

---

## 7. Externalités notées séparément

- **Coût d'écriture supplémentaire total** : ~50 inserts/cycle vol au lieu de ~30. Hardware (RTX 5070, DDR5) = négligeable.
- **Coût schéma migrations** : 5-6 migrations Alembic supplémentaires. Risque opérationnel si pas de procédure rollback testée → atténué par [6] dans étape 1.
- **Risque sur règle "tag v1.0 avant step suivant"** : ces ajouts repoussent v1.0 de ~3-4 semaines. Décision binaire à prendre maintenant : (a) inclure dans v1.0 (cohérent avec "jamais de repo à 70%"), (b) tag v1.0 maintenant et v1.1 ensuite (perte de cohérence narrative).
- **Coût de coordination multi-agent ignoré** : avec 5+ agents async, tu sous-estimes potentiellement les modes de défaillance partielle. Le `engine_heartbeats` persisté ([11]) est le minimum pour rendre ces défaillances auditables ex-post.

---

## 8. Distance à la frontière (HF alpha researcher)

Pour qu'un quant senior auditant ce repo en 30min reconnaisse de la rigueur :

| Manque | Impact évaluation |
|---|---|
| Attribution PnL par stratégie/régime/tenor | Invisible aujourd'hui → "ce système n'isole pas son edge" |
| Reproductibilité ex-post d'un trade (replay + model_runs) | "Comment vérifier que ce backtest n'est pas overfitté ?" |
| Cohérence backtest vs live (hit rate, slippage mesuré, latence) | "Ton edge live converge-t-il avec ton backtest ?" |
| Risk audit trail (limits + breaches versionnés) | "Que s'est-il passé pendant le drawdown du 15 mars ?" |
| Events macro pour conditional analysis | "Ton edge tient-il aussi en jour FOMC ?" |
| Tests stress sur pipeline (crash mid-cycle VolEngine) | "Que se passe-t-il si la valuation des positions OPEN bloque ?" |

Schéma actuel = niveau projet perso fonctionnel. Avec les 6 tables + 10 endpoints ci-dessus = niveau système auditable. ~18-20 jours de travail bien spec'é, pas un refactor.
