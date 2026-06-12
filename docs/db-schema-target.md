# DB schema — état actuel et cible

Aujourd'hui : 33 tables ORM dans `src/persistence/models.py`.
Cible : ~12 tables réparties en 4 thèmes.

---

## Thème 1 — Vol / Data / Indicators

### Cible (3 tables)

| Table | Rôle | Cadence write | Writer |
|---|---|---|---|
| `vol_surface_history` | Surface IV calibrée (SVI/SSVI params + grid de pillars) | 180s | vol-engine |
| `feature_history` | Indicators time-series (VRP, regime z-score, PCA components, HAR-RV, GARCH params) | 180s | vol-engine |
| `regime_snapshot` | État régime courant + lookup (label, proba, transition matrix) | 60s | vol-engine |

### Mapping depuis l'existant

| Actuel | Devient | Action |
|---|---|---|
| `VolSurface` | `vol_surface_history` | rename |
| `SurfaceSnapshotHourly` | fold dans `vol_surface_history` (rétention hourly via partitionnement ou view) | merge |
| `FeatureHistory` | `feature_history` | rename |
| `RegimeSnapshot` | `regime_snapshot` | rename |
| `RegimeLookup` | fold dans `regime_snapshot` (colonne `lookup_data` JSONB) | merge |
| `VrpTableDefault` | fold dans `feature_history` (feature_name='vrp') | merge |
| `PcaModel` | fold dans `feature_history` (feature_name='pca_components', payload JSONB) | merge |
| `PcaSignal` | fold dans `feature_history` (feature_name='pca_signal') | merge |
| `SignalRecommendationsMap` | → table `config` (référentiel statique) | move |
| `Event` (calendrier macro FRED/ECB) | `event_calendar` (séparé, écriture rare) | rename + isole |

Résultat thème : 10 tables → 3 tables (+ `event_calendar` à part).

---

## Thème 2 — Portfolio (state + history)

### Cible (3 tables)

| Table | Rôle | Cadence write | Writer |
|---|---|---|---|
| `position` | State courant des positions IB (1 row par contrat ouvert, DELETE quand qty=0) | UPSERT 1s | execution-engine + risk-engine UPDATE greeks |
| `position_metric_history` | Snapshot horodaté des greeks + pnl + iv par position | INSERT 2s × N positions ouvertes | risk-engine |
| `account_history` | Snapshot horodaté du compte (NetLiq, cash, margin, cushion) | INSERT 1s | execution-engine |

### Mapping depuis l'existant

| Actuel | Devient | Action |
|---|---|---|
| `Position` (IB-live) | `position` | rename |
| `TradePosition` (booked) | dépréciée → fold dans `position` + référence vers `structure` (thème 3) | merge + refactor |
| `PositionSnapshot` | `position_metric_history` | rename |
| `PositionMtmHistory` | fold dans `position_metric_history` (colonnes pnl_gross/net/vega/gamma/theta) | merge |
| `PositionSignalTracking` | fold dans `position_metric_history` (colonnes signal_z_score, current_label) | merge |
| `AccountSnap` | `account_history` | rename |
| `BookStateSnapshot` | dépréciée (= snapshot agrégé recalculable depuis `account_history` + `position_metric_history`) | drop |

Résultat thème : 7 tables → 3 tables.

---

## Thème 3 — Trade / Order (decision + execution journal)

### Cible (4 tables)

| Table | Rôle | Cadence write | Writer |
|---|---|---|---|
| `structure` | Structure trade décidée (N-leg combo + signal + state machine pending/open/closing/closed) | INSERT à l'ouverture + UPDATE state | execution-engine |
| `order` | State courant des ordres actifs IB (Submitted/PartiallyFilled, DELETE quand Filled/Cancelled) | UPSERT event-driven | execution-engine |
| `trade_event` | Journal append-only de tous les events trade : new_order, fill, cancel, hedge, exit_alert | INSERT event-driven | execution-engine |
| `order_history` | Archive des ordres complets (Filled/Cancelled) — pour query historique sans bloater `order` | INSERT quand order finalise | execution-engine |

### Mapping depuis l'existant

| Actuel | Devient | Action |
|---|---|---|
| `TradeStructure` | `structure` | rename |
| `StructureDefinition` | `structure` (colonne `definition_json`) | merge |
| `StructureOrder` | `order` (avec FK vers `structure`) | merge |
| `StructureFill` | `trade_event` (event_type='fill') | merge |
| `Order` | split en `order` (active) + `order_history` (terminé) | split |
| `OrderEvent` | `trade_event` (event_type='order_status_change') | merge |
| `Trade` (fills IB) | `trade_event` (event_type='fill') | merge |
| `HedgeOrder` | `trade_event` (event_type='hedge') avec FK position | merge |
| `ExecutionAuditLog` | `trade_event` (event_type='audit') | merge |
| `TradePreviewRow` | dépréciée (preview = computed in-memory côté api, pas besoin de persister) | drop |

Résultat thème : 10 tables → 4 tables.

---

## Thème 4 — Settings / Others

### Cible (2 tables)

| Table | Rôle | Cadence write | Writer |
|---|---|---|---|
| `config` | Toutes les configs versionnées (vol_config, exit_rules, delta_hedge, risk_limits) en JSONB par `key + version` | INSERT sur edit GUI | api/settings router |
| `system_event` | Logs système structurés (engine_started, ib_disconnect, alert_triggered, healthcheck_failed) | INSERT event-driven | tous engines |

### Mapping depuis l'existant

| Actuel | Devient | Action |
|---|---|---|
| `VolConfig` | `config` (key='vol_calibration') | merge |
| `ExitRulesConfig` | `config` (key='exit_rules') | merge |
| `DeltaHedgeConfig` | `config` (key='delta_hedge') | merge |
| `RiskLimit` | `config` (key='risk_limits') | merge |
| `ExitAlert` | `trade_event` (event_type='exit_alert', cf. thème 3) | merge → thème 3 |
| `IbConnectionState` | `system_event` (event_type='ib_connection') | merge |

Résultat thème : 6 tables → 2 tables (+ `ExitAlert` migré vers thème 3).

---

## Résumé global

| Thème | Avant | Après |
|---|---|---|
| Vol / Data / Indicators | 10 | 4 (incluant `event_calendar`) |
| Portfolio | 7 | 3 |
| Trade / Order | 10 | 4 |
| Settings / Others | 6 | 2 |
| **Total** | **33** | **13** |

---

## Naming convention

| Type | Convention | Exemples |
|---|---|---|
| Current-state (UPSERT/UPDATE) | singulier, sans suffixe | `position`, `order`, `structure` |
| Time-series append-only | suffixe `_history` | `position_metric_history`, `account_history`, `vol_surface_history`, `feature_history`, `order_history` |
| Journal event-driven | suffixe `_event` | `trade_event`, `system_event` |
| Snapshot ponctuel single-row-evolving | suffixe `_snapshot` | `regime_snapshot` |
| Configuration versionnée | nom métier ou table générique `config` | `config` (JSONB par key+version) |
| Référentiel statique | suffixe `_ref` ou `_calendar` | `event_calendar` |

---

## Migration — ordre suggéré (par PR)

1. **PR1 — Thème 1 (Vol / Indicators)** : merger `VolSurface` + `SurfaceSnapshotHourly`, fold les sous-tables PCA/VRP dans `feature_history`. Bas risque (data analytics, pas trading-critical).
2. **PR2 — Thème 4 (Settings)** : merger les 4 tables config vers `config` JSONB versionné. Bas risque (lectures peu fréquentes).
3. **PR3 — Thème 3 (Trade / Order)** : refactor le journal trade vers `trade_event` unifié. Risque moyen (touche execution-engine).
4. **PR4 — Thème 2 (Portfolio)** : dernière étape, plus risquée (touche les panels live + risk-engine writer). À faire en sandbox dédiée avec rollback testé.

Chaque PR comporte : alembic migration + refactor models.py + refactor writer + refactor router + tests + smoke notebook valide la pipeline.
