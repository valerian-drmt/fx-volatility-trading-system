# Étape 1 — Regime gating (Panel 1)

> Spec de la première étape du workflow trading vol.
>
> **Objectif fonctionnel** : produire une décision binaire `trade_authorized ∈ {True, True_with_size_penalty, False}` avant que l'utilisateur ne consulte un seul autre panel.
>
> **Pas dans cette étape** : sizing, signal detection, structure choice, position monitoring. Ces étapes consomment le résultat d'étape 1 mais ne le produisent pas.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Utilisateur (décideur), regime detector (signal source backend), event calendar (signal source backend) |
| États possibles | `regime ∈ {calm, stressed, pre_event}` × `event_dampener ∈ {on, off}` |
| Inputs requis | `vol_level_pct`, `vol_of_vol_pct`, `term_slope_pct`, `days_to_next_major_event` |
| Output | `trade_authorized ∈ {True, True_with_size_penalty, False}` |
| Contrainte stationnarité | régime stable ≥ 3 cycles consécutifs (= 9 minutes à 180s/cycle) avant action |
| Latence acceptable | régime affiché < 200s après le dernier cycle vol-engine |

---

## 2. Décision logic (à implémenter côté UI ou backend selon préférence)

```python
def gate_decision(regime_state: RegimeState, history: list[RegimeState]) -> Decision:
    # Stationnarité : exiger 3 cycles cohérents
    if not is_stable(regime_state, history, n_cycles=3):
        return Decision(authorized=False, reason="regime_unstable", size_mult=0.0)
    
    # Event dampener override
    if regime_state.event_dampener:
        return Decision(authorized=True, reason="event_dampener_active", size_mult=0.5)
    
    # Régime principal
    if regime_state.label == "pre_event":
        return Decision(authorized=False, reason="regime_pre_event", size_mult=0.0)
    elif regime_state.label == "stressed":
        return Decision(authorized=True, reason="regime_stressed", size_mult=0.7)
    elif regime_state.label == "calm":
        return Decision(authorized=True, reason="regime_calm", size_mult=1.0)
```

Aucune autre étape ne s'exécute si `Decision.authorized = False`.

---

## 3. Le panel (UI) — 6 zones

| Zone | Contenu | Source data | Statut implémentation |
|---|---|---|---|
| 1 — Badge régime | Label string + couleur | `_regime.label` | Calculable aujourd'hui (label existe dans `_fair_q[3M].regime`, juste à exposer top-level) |
| 2 — Probabilités GMM | 3 barres % | `_regime.probabilities` | À implémenter (cf. C1.4 du discrepancy report). MVP : ne pas afficher la zone tant que `probabilities is None`. |
| 3 — Features live | Table 3 lignes (value + z-score + qualifier) | `_regime.features.{vol_level, vol_of_vol, term_slope}` | `vol_level` et `term_slope` triviaux ; `vol_of_vol` et z-scores nécessitent table `feature_history` |
| 4 — Prochain event | Type + countdown | `_regime.next_event` | Nécessite `events` table + reader |
| 5 — VRP attendu | 3 valeurs (1M, 3M, 6M) du régime courant | `_regime.vrp_expected` (lookup `vrp_table_default`) | Calculable aujourd'hui (table hardcodée existe). À flagger "placeholder, not estimated". |
| 6 — Event dampener | Badge ON/OFF | `_regime.event_dampener` | Dépend zone 4 |

---

## 4. Schema du payload `_regime`

À ajouter dans `latest_vol_surface.surface._regime`. Format JSON :

```jsonc
{
  "_regime": {
    "label": "calm",                       // enum: calm | stressed | pre_event
    "method": "threshold_heuristic",       // enum: threshold_heuristic | gmm_v1
    "probabilities": null,                 // null tant que GMM non implémenté ; sinon [p_calm, p_stressed, p_pre_event]
    "features": {
      "vol_level":   { "value": 6.05, "z": -0.5, "qualifier": "bas" },
      "vol_of_vol":  { "value": 0.12, "z": -0.3, "qualifier": "faible" },
      "term_slope":  { "value": 0.18, "z":  0.2, "qualifier": "plat" }
    },
    "next_event": {
      "type": "ECB_meeting",
      "datetime_utc": "2026-05-08T12:45:00Z",
      "days_remaining": 11.13
    },
    "event_dampener": false,
    "vrp_expected": {                       // pour le régime courant uniquement
      "1M": 0.6, "2M": 0.7, "3M": 0.8,
      "4M": 0.9, "5M": 1.0, "6M": 1.1
    }
  }
}
```

---

## 5. Tables Postgres nécessaires

Convention : snake_case, IDs UUID, timestamps en `TIMESTAMPTZ`, indices nommés `ix_<table>_<col>`.

### 5.1 `regime_snapshots` — historique des décisions régime

Une row par cycle vol-engine. Permet (a) audit a posteriori, (b) calcul de la "stabilité" (3 cycles cohérents requis).

```sql
CREATE TABLE regime_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL DEFAULT 'EURUSD',
    
    label           TEXT NOT NULL,                -- 'calm' | 'stressed' | 'pre_event'
    method          TEXT NOT NULL,                -- 'threshold_heuristic' | 'gmm_v1' | 'gmm_v2' ...
    
    -- features brutes (utiles pour debug régime change)
    vol_level_pct       DOUBLE PRECISION,
    vol_of_vol_pct      DOUBLE PRECISION,
    term_slope_pct      DOUBLE PRECISION,
    
    -- z-scores correspondants
    vol_level_z         DOUBLE PRECISION,
    vol_of_vol_z        DOUBLE PRECISION,
    term_slope_z        DOUBLE PRECISION,
    
    -- probabilités GMM (nullable, populated quand C1.4 implémenté)
    p_calm              DOUBLE PRECISION,
    p_stressed          DOUBLE PRECISION,
    p_pre_event         DOUBLE PRECISION,
    
    -- event context au moment du snapshot
    event_dampener      BOOLEAN NOT NULL DEFAULT false,
    days_to_next_event  DOUBLE PRECISION,         -- nullable si pas d'event connu
    next_event_type     TEXT,                     -- 'ECB' | 'FOMC' | 'NFP' | ...
    
    CONSTRAINT chk_label   CHECK (label IN ('calm', 'stressed', 'pre_event')),
    CONSTRAINT chk_probs   CHECK (
        (p_calm IS NULL AND p_stressed IS NULL AND p_pre_event IS NULL)
        OR (p_calm + p_stressed + p_pre_event BETWEEN 0.99 AND 1.01)
    )
);

CREATE INDEX ix_regime_snapshots_timestamp ON regime_snapshots (timestamp DESC);
CREATE INDEX ix_regime_snapshots_symbol_ts ON regime_snapshots (symbol, timestamp DESC);
```

**Cardinalité attendue** : 1 row / 180s = ~480 rows / jour / symbole. Sur 5 ans : ~880k rows. Trivial.

**Requêtes typiques** :
```sql
-- Régime des N derniers cycles (pour stability check côté UI)
SELECT timestamp, label FROM regime_snapshots
WHERE symbol = 'EURUSD' ORDER BY timestamp DESC LIMIT 5;

-- Distribution des régimes sur 30 derniers jours (pour calibration heuristique)
SELECT label, COUNT(*) FROM regime_snapshots
WHERE timestamp > NOW() - INTERVAL '30 days' GROUP BY label;
```

---

### 5.2 `feature_history` — séries temporelles features pour rolling stats

Permet de calculer `vol_of_vol` (std rolling 30j de IV ATM) et z-scores rolling 90j.

Deux options de design : (a) une row par (timestamp, feature_name) — long format, ou (b) une row par timestamp avec colonnes — wide format. Long format = plus flexible pour ajouter features, wide = plus rapide en lecture pour le cas Panel 1. Choix : **wide** parce que Panel 1 lit toutes les features ensemble.

```sql
CREATE TABLE feature_history (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL DEFAULT 'EURUSD',
    
    -- features de niveau
    iv_atm_1m_pct           DOUBLE PRECISION,
    iv_atm_3m_pct           DOUBLE PRECISION,
    iv_atm_6m_pct           DOUBLE PRECISION,
    rv_yz_pct               DOUBLE PRECISION,
    
    -- features dérivées (calculées au cycle ou en background job)
    vol_of_vol_30d_pct      DOUBLE PRECISION,
    term_slope_pct          DOUBLE PRECISION,        -- iv_6m - iv_1m
    
    -- z-scores rolling 90j (calculées en background, peuvent être null si historique insuffisant)
    vol_level_z90           DOUBLE PRECISION,
    vol_of_vol_z90          DOUBLE PRECISION,
    term_slope_z90          DOUBLE PRECISION,
    
    UNIQUE (symbol, timestamp)
);

CREATE INDEX ix_feature_history_symbol_ts ON feature_history (symbol, timestamp DESC);
```

**Cardinalité** : 1 row / 180s = ~480 / jour. Sur 5 ans : ~880k rows. Trivial.

**Pattern de mise à jour** :
- À chaque cycle vol-engine : INSERT row avec features de niveau (iv_atm_*, rv_yz_pct) directement disponibles.
- Background job (toutes les 5 min ou à chaque cycle) : UPDATE pour calculer features dérivées + z-scores rolling. Permet de découpler latence cycle de la lourdeur des stats.

**Pourquoi pas calculer z-scores au cycle ?** Parce que le rolling 90j nécessite query sur 90 × 480 = 43k rows précédents, indexée mais pas instantanée. Mieux vaut stocker dénormalisé.

---

### 5.3 `events` — calendrier économique

Source de vérité pour `event_dampener` et zone 4 du panel.

```sql
CREATE TABLE events (
    id              BIGSERIAL PRIMARY KEY,
    
    event_type      TEXT NOT NULL,                  -- 'ECB' | 'FOMC' | 'NFP' | 'BOE' | 'BOJ' | 'CPI_US' | 'GDP_US' ...
    impact          TEXT NOT NULL,                  -- 'high' | 'medium' | 'low'
    region          TEXT NOT NULL,                  -- 'EU' | 'US' | 'UK' | 'JP' ...
    
    scheduled_at    TIMESTAMPTZ NOT NULL,           -- date/heure officielle de release
    description     TEXT,                            -- ex : "ECB Main Refinancing Rate decision + press conf"
    
    -- métadonnées source (pour traçabilité)
    source          TEXT NOT NULL DEFAULT 'manual', -- 'manual' | 'forexfactory' | 'tradingeconomics_api' | ...
    source_url      TEXT,
    inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_impact CHECK (impact IN ('high', 'medium', 'low'))
);

CREATE INDEX ix_events_scheduled_at ON events (scheduled_at);
CREATE INDEX ix_events_high_impact ON events (scheduled_at) WHERE impact = 'high';
```

**Cardinalité** : ~30-50 events high impact / mois pour majors EUR/USD = ~600/an. Trivial.

**Source de données — options** :
1. **MVP manuel** : INSERT à la main les 5 prochains events high-impact connus (ECB, FOMC, NFP). 5 minutes par mois. Suffit pour démarrer.
2. **ICS feed** : import scheduled depuis calendar feeds publics ECB/FED.
3. **API tier** : Trading Economics, ForexFactory scrape, FRED API.

**Requête typique** :
```sql
-- Prochain event high-impact pertinent EUR/USD
SELECT event_type, scheduled_at, 
       EXTRACT(EPOCH FROM (scheduled_at - NOW())) / 86400 AS days_remaining
FROM events
WHERE impact = 'high' 
  AND region IN ('EU', 'US')
  AND scheduled_at > NOW()
ORDER BY scheduled_at ASC LIMIT 1;
```

---

### 5.4 `vrp_table_default` — table VRP hardcodée (placeholder C1.5)

Aujourd'hui dans `core/vol/vrp.py` Python. Migration en table = trivial et permet hot-reload + audit des changements de placeholder.

```sql
CREATE TABLE vrp_table_default (
    id              SERIAL PRIMARY KEY,
    
    regime          TEXT NOT NULL,
    tenor           TEXT NOT NULL,                  -- '1M' | '2M' | ... | '6M'
    vrp_vol_pts     DOUBLE PRECISION NOT NULL,
    
    -- métadonnées calibration (pour quand C1.5 estimera vraiment)
    calibration_method  TEXT NOT NULL DEFAULT 'hardcoded_placeholder',
    calibration_date    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes               TEXT,
    
    UNIQUE (regime, tenor),
    CONSTRAINT chk_regime CHECK (regime IN ('calm', 'stressed', 'pre_event'))
);

-- Seed initial : table 6×3 du engine reference §4.4
INSERT INTO vrp_table_default (regime, tenor, vrp_vol_pts) VALUES
    ('calm',      '1M', 0.6), ('calm',      '2M', 0.7), ('calm',      '3M', 0.8),
    ('calm',      '4M', 0.9), ('calm',      '5M', 1.0), ('calm',      '6M', 1.1),
    ('stressed',  '1M', 1.5), ('stressed',  '2M', 1.6), ('stressed',  '3M', 1.7),
    ('stressed',  '4M', 1.8), ('stressed',  '5M', 1.9), ('stressed',  '6M', 2.0),
    ('pre_event', '1M', 2.5), ('pre_event', '2M', 2.6), ('pre_event', '3M', 2.7),
    ('pre_event', '4M', 2.8), ('pre_event', '5M', 2.9), ('pre_event', '6M', 3.0);
```

**Cardinalité** : 18 rows. Statique pour le moment.

**Migration future (C1.5)** : ajouter table `vrp_estimated(timestamp, tenor, regime, vrp_estimated_vol_pts, model_version)` pour les valeurs sortant de la régression rolling. Le frontend lira `vrp_estimated` quand dispo, fallback sur `vrp_table_default` sinon.

---

### 5.5 `gate_decisions` — log audit des décisions trade/no-trade (optionnel mais recommandé)

Pas strictement nécessaire pour faire fonctionner le panel mais utile pour :
- Audit a posteriori : "pourquoi je n'ai pas tradé le 12 mai à 10h ?"
- Calibration : analyser le ratio (no-trade par cause) pour ajuster seuils

```sql
CREATE TABLE gate_decisions (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol          TEXT NOT NULL DEFAULT 'EURUSD',
    
    regime_snapshot_id  BIGINT REFERENCES regime_snapshots(id),
    
    authorized      BOOLEAN NOT NULL,
    reason          TEXT NOT NULL,                  -- 'regime_calm' | 'regime_stressed' | 'regime_pre_event' | 'regime_unstable' | 'event_dampener_active'
    size_mult       DOUBLE PRECISION NOT NULL,
    
    user_action     TEXT                            -- 'proceeded_to_step2' | 'closed_session' | NULL si pas d'action enregistrée
);

CREATE INDEX ix_gate_decisions_timestamp ON gate_decisions (timestamp DESC);
```

---

## 6. Schéma relationnel

```
┌─────────────────────┐       ┌──────────────────────┐
│  feature_history    │       │       events         │
│  (1 / cycle)        │       │  (manuel ou feed)    │
└──────────┬──────────┘       └──────────┬───────────┘
           │                              │
           │  reads                       │  reads
           ▼                              ▼
┌─────────────────────────────────────────────────────┐
│           vol-engine cycle (180s)                   │
│           computes _regime payload                  │
└──────────┬──────────────────────────────┬───────────┘
           │                              │
           │  writes                      │  reads (lookup)
           ▼                              ▼
┌─────────────────────┐       ┌──────────────────────┐
│  regime_snapshots   │       │ vrp_table_default    │
│  (1 / cycle)        │       │  (18 rows static)    │
└──────────┬──────────┘       └──────────────────────┘
           │
           │  referenced by
           ▼
┌─────────────────────┐
│   gate_decisions    │
│  (audit log)        │
└─────────────────────┘
```

---

## 7. Pipeline backend par étape de cycle

À ajouter dans `src/engines/vol/engine.py` après le step `_fair_q` :

```python
def _step_regime_snapshot(self, surface_partial: dict, db: Session) -> dict:
    """Computes _regime payload and persists snapshot."""
    
    # 1. Features brutes depuis surface courante
    iv_1m = surface_partial["1M"]["atm"]["iv"] * 100
    iv_3m = surface_partial["3M"]["atm"]["iv"] * 100
    iv_6m = surface_partial["6M"]["atm"]["iv"] * 100
    rv_yz = surface_partial.get("_rv_full_pct")
    
    vol_level_pct = iv_3m
    term_slope_pct = iv_6m - iv_1m
    
    # 2. vol_of_vol depuis feature_history (lecture)
    iv_3m_history = db.execute(
        select(FeatureHistory.iv_atm_3m_pct)
        .where(FeatureHistory.symbol == "EURUSD")
        .where(FeatureHistory.timestamp > datetime.utcnow() - timedelta(days=30))
        .order_by(FeatureHistory.timestamp)
    ).scalars().all()
    vol_of_vol_pct = float(np.std(iv_3m_history)) if len(iv_3m_history) >= 20 else None
    
    # 3. Z-scores depuis feature_history (lecture rolling 90j)
    z_scores = compute_rolling_zscores(db, window_days=90, current={
        "vol_level": vol_level_pct,
        "vol_of_vol": vol_of_vol_pct,
        "term_slope": term_slope_pct
    })
    
    # 4. Régime label (heuristique actuelle)
    regime_label = detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct)
    
    # 5. Event context
    next_event = db.execute(
        select(Event)
        .where(Event.impact == "high")
        .where(Event.region.in_(["EU", "US"]))
        .where(Event.scheduled_at > datetime.utcnow())
        .order_by(Event.scheduled_at)
        .limit(1)
    ).scalar_one_or_none()
    
    if next_event:
        days_to_event = (next_event.scheduled_at - datetime.utcnow()).total_seconds() / 86400
        event_type = next_event.event_type
        event_dampener = days_to_event < 5
    else:
        days_to_event = None
        event_type = None
        event_dampener = False
    
    # 6. VRP attendu pour régime courant (lookup)
    vrp_rows = db.execute(
        select(VrpTableDefault.tenor, VrpTableDefault.vrp_vol_pts)
        .where(VrpTableDefault.regime == regime_label)
    ).all()
    vrp_expected = {row.tenor: row.vrp_vol_pts for row in vrp_rows}
    
    # 7. Persistence
    snapshot = RegimeSnapshot(
        timestamp=datetime.utcnow(),
        symbol="EURUSD",
        label=regime_label,
        method="threshold_heuristic",
        vol_level_pct=vol_level_pct,
        vol_of_vol_pct=vol_of_vol_pct,
        term_slope_pct=term_slope_pct,
        vol_level_z=z_scores["vol_level"],
        vol_of_vol_z=z_scores["vol_of_vol"],
        term_slope_z=z_scores["term_slope"],
        event_dampener=event_dampener,
        days_to_next_event=days_to_event,
        next_event_type=event_type,
    )
    db.add(snapshot)
    
    feature_row = FeatureHistory(
        timestamp=datetime.utcnow(),
        symbol="EURUSD",
        iv_atm_1m_pct=iv_1m,
        iv_atm_3m_pct=iv_3m,
        iv_atm_6m_pct=iv_6m,
        rv_yz_pct=rv_yz,
        vol_of_vol_30d_pct=vol_of_vol_pct,
        term_slope_pct=term_slope_pct,
        vol_level_z90=z_scores["vol_level"],
        vol_of_vol_z90=z_scores["vol_of_vol"],
        term_slope_z90=z_scores["term_slope"],
    )
    db.add(feature_row)
    db.commit()
    
    # 8. Payload (pour Redis publish)
    return {
        "label": regime_label,
        "method": "threshold_heuristic",
        "probabilities": None,
        "features": {
            "vol_level":  {"value": round(vol_level_pct, 2),  "z": z_scores["vol_level"]},
            "vol_of_vol": {"value": round(vol_of_vol_pct, 2) if vol_of_vol_pct else None, 
                           "z": z_scores["vol_of_vol"]},
            "term_slope": {"value": round(term_slope_pct, 2), "z": z_scores["term_slope"]},
        },
        "next_event": {
            "type": event_type,
            "datetime_utc": next_event.scheduled_at.isoformat() if next_event else None,
            "days_remaining": round(days_to_event, 2) if days_to_event else None,
        },
        "event_dampener": event_dampener,
        "vrp_expected": vrp_expected,
    }
```

Branchement dans `vol_cycle` :
```python
surface["_regime"] = self._step_regime_snapshot(surface, db_session)
```

---

## 8. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant pour MVP étape 1 ? |
|---|---|---|
| Migration Postgres : 4 tables + indices | 0.5 j | Oui |
| Seed `vrp_table_default` | 5 min | Oui |
| Seed `events` manuel (5 prochains events high-impact) | 15 min | Oui |
| `_step_regime_snapshot` backend implémentation | 1 j | Oui |
| `compute_rolling_zscores` helper | 0.5 j | Non (z-scores peuvent être null en MVP) |
| Frontend Panel 1 component (6 zones, gestion null/MOCK) | 1.5 j | Oui |
| Backfill `feature_history` historique (si données dispo) | 0.5 j | Non (peut bootstrap from now) |
| Tests : unit `detect_regime`, integration cycle | 0.5 j | Oui pour live |
| **Total MVP fonctionnel** | **~3.5 jours dev** | |

---

## 9. Stratégie de bootstrap (premier déploiement sans historique)

Au premier démarrage, `feature_history` est vide → impossible de calculer `vol_of_vol_30d` ni z-scores rolling 90j. Stratégie :

| Période depuis bootstrap | Comportement |
|---|---|
| Cycles 0 — 100 (~5h) | `vol_of_vol = null`, z-scores = null. Régime label calculable seulement si `detect_regime` accepte null pour vol_of_vol (à vérifier dans le code actuel). |
| Cycles 100 — 480 (~5h-1j) | `vol_of_vol` calculable (≥ 20 obs). Z-scores toujours null. |
| Jour 30+ | `vol_of_vol_30d` à pleine fenêtre. Z-scores ont 1 mois d'historique (sous-optimal mais utilisable). |
| Jour 90+ | Régime fully-featured. |

**Recommandation** : si historique IB / surface dispo en S3 ou ailleurs, backfill `feature_history` AVANT premier démarrage live. Sinon accepter période bootstrap dégradée.

---

## 10. Tests à écrire (acceptance criteria)

```python
# test_regime_pipeline.py

def test_regime_label_calm_normal_features():
    """Vol bas, vol_of_vol bas, slope plat → calm"""
    assert detect_regime(vol_level=6.0, vol_of_vol=0.10, term_slope=0.20) == "calm"

def test_regime_label_pre_event_high_vov():
    """Vol_of_vol élevé → pre_event"""
    # NB: threshold exact à confirmer dans code actuel
    assert detect_regime(vol_level=8.0, vol_of_vol=0.5, term_slope=-0.5) == "pre_event"

def test_regime_snapshot_persists_correctly(db):
    """Un cycle complet persiste 1 row dans regime_snapshots et 1 dans feature_history"""
    pre_count_rs = db.execute(select(func.count()).select_from(RegimeSnapshot)).scalar()
    pre_count_fh = db.execute(select(func.count()).select_from(FeatureHistory)).scalar()
    
    engine._step_regime_snapshot(mock_surface, db)
    
    assert db.execute(select(func.count()).select_from(RegimeSnapshot)).scalar() == pre_count_rs + 1
    assert db.execute(select(func.count()).select_from(FeatureHistory)).scalar() == pre_count_fh + 1

def test_event_dampener_triggers_5_days_before():
    """Event à J+4 → dampener ON ; à J+6 → OFF"""
    insert_event(scheduled_at=now + timedelta(days=4), impact="high")
    payload = engine._step_regime_snapshot(mock_surface, db)
    assert payload["event_dampener"] is True
    
    db.query(Event).delete()
    insert_event(scheduled_at=now + timedelta(days=6), impact="high")
    payload = engine._step_regime_snapshot(mock_surface, db)
    assert payload["event_dampener"] is False

def test_gate_decision_pre_event_blocks_trading():
    snapshot = RegimeSnapshot(label="pre_event", event_dampener=False, ...)
    decision = gate_decision(snapshot, history=[snapshot]*3)
    assert decision.authorized is False
    assert decision.size_mult == 0.0

def test_gate_decision_unstable_regime_blocks():
    """Régime flippe entre cycles → blocked"""
    history = [
        RegimeSnapshot(label="calm", ...),
        RegimeSnapshot(label="stressed", ...),
        RegimeSnapshot(label="calm", ...),
    ]
    decision = gate_decision(history[-1], history=history)
    assert decision.authorized is False
    assert decision.reason == "regime_unstable"
```

---

## 11. Ce qui n'est PAS dans cette étape (et où ça ira)

| Concept | Étape future |
|---|---|
| Détection signal CHEAP/EXPENSIVE | Étape 2 |
| Choix de structure (straddle, butterfly, calendar) | Étape 3 |
| Sizing final (formule `base × |z|/threshold × book_penalty × dampener`) | Étape 3 |
| Pricing leg-by-leg, greeks, scenarios | Étape 3 |
| Submit + execution | Étape 4 |
| Position monitoring, exit alerts | Étape 5 |

Étape 1 ne fait qu'une chose : autoriser ou bloquer les étapes suivantes.

---

## 12. Définition of done — étape 1

L'étape 1 est livrée quand :

- [ ] 4 tables Postgres créées (regime_snapshots, feature_history, events, vrp_table_default)
- [ ] `vrp_table_default` seedée (18 rows)
- [ ] `events` contient au moins 5 events high-impact futurs (manuel)
- [ ] Cycle vol-engine appelle `_step_regime_snapshot` et publie `_regime` dans le payload
- [ ] Panel 1 frontend affiche les 6 zones, gère gracieusement les nulls (probabilities, z-scores)
- [ ] `gate_decision` callable retourne `authorized` correctement pour 4 scénarios test
- [ ] Heartbeat & freshness check : panel devient grayed-out si `_regime.timestamp` > 200s
- [ ] Logs : chaque cycle log clairement `regime_label`, `event_dampener`, `gate_decision.reason`

---

## 13. GMM en shadow mode (politique post-2026-04-30)

État actuel : la méthode active dans Step 1 est `threshold_heuristic`. Le GMM (3 composantes sur `(vol_level, vol_of_vol)`) tourne **en parallèle** mais ses outputs ne pilotent **ni le label, ni le gate decision**. Le payload `_regime.probabilities` reste explicitement `null` (le panel zone 2 affiche "not available — heuristique seule").

### Pourquoi shadow

Sur les ~250 obs de bootstrap (1 an d'historique IB), 3 problèmes empilés se manifestent :

1. **Statistique** : N≈250 et la fenêtre couvre une période quasi-uniformément calm. Aucun cluster `stressed` ou `pre_event` au sens fonctionnel (vol > 10% ou |slope| > 2%). Le GMM force 3 composantes sur du bruit autour d'un mode unique.
2. **Géométrique** : le live point peut être OOD sur l'axe `vol_of_vol` (live = 0.36 vs training [0.79, 2.32]). Distances Mahalanobis 3-5σ partout → posteriors dégénérés (100/0/0).
3. **Sémantique** : le mapping `composantes → labels` se fait par tri sur `μ_vol_level` mais la définition fonctionnelle de `pre_event` est `vol_of_vol élevé`. Sur du training calm-only, les `μ_vol_level` des 3 composantes sont indistinguables (écart < 1σ) → labels essentiellement aléatoires.

Patches numériques (`reg_covar=0.5`) traitent les symptômes mais pas le fond. Tant qu'on n'a pas observé de vrais régimes distincts dans les données, promouvoir GMM = afficher de la confiance fabriquée.

### Données persistées (pour comparison J+30)

Les colonnes `regime_snapshots.{p_calm, p_stressed, p_pre_event}` sont **populées à chaque cycle** quand le GMM fitte (≥ MIN_OBS_GMM=50 obs). La colonne `method` reste `threshold_heuristic`. On accumule donc en shadow ce que le GMM aurait dit, sans l'utiliser.

### Critères de promotion `heuristic` → `gmm_v1`

Tous les 3 doivent être verts :

1. **Volume de shadow data** : ≥ 1000 snapshots avec probas non-null (~50h de cycles vol-engine à 180s).
2. **Agreement ratio** : `argmax(probas_GMM) == label_heuristic` ≥ 70 % sur la fenêtre de shadow.
3. **Coverage des régimes** : training set contient ≥ 1 event high-impact traversé (FOMC / NFP / ECB qui a déplacé vol_level > 8 % ou vol_of_vol > 3 %). Vérification manuelle.

Endpoint diagnostic : `GET /api/v1/regime/gmm/shadow` retourne les 3 valeurs + le boolean `ready_to_promote`.

Quand les 3 critères sont OK :

```python
# core/vol/regime_engine.py
# Switcher la branche label = detect_regime() vers
# label = max(gmm_probabilities, key=gmm_probabilities.get)
# method = "gmm_v1"
# payload["probabilities"] = gmm_probabilities  # plus null
```

### Pourquoi pas un BayesianGaussianMixture avec prior anchored

Considéré + rejeté pour MVP : le prior remplace le signal manquant par une heuristique déguisée en posterior bayésien. La `composante stressed` ancrée sur `(vol_level=12, vol_of_vol=3)` ne reflète pas une observation, juste une supposition. Mieux vaut afficher honnêtement "pas de GMM" que d'afficher un GMM qui mime un prior.

À reconsidérer **si** l'objectif passe à de la régime detection sur données HF (Step 4 alpha-research v2) où mixtures bayésiennes sont mainstream.

---

## 14. Dette technique (à attaquer post-Step 2)

> Liste consolidée + suivie dans **[`docs/vol_trading_pca/TODO.md`](../TODO.md)**.
> Cette section donne le résumé Step 1 ; les détails (cible, coût impl.,
> deadline) vivent dans le TODO.

Identifiée lors de la review v1.0, non-bloquante pour Step 1 done :

### 14.1 Dampener convexe au lieu de step-function binaire

État actuel : `event_dampener_active` → `size_mult = 0.5` constant tant que `days_to_event < 5`. Réalité desk : à J-2 d'un NFP, 0.5 reste agressif.

Cible :
```python
def event_size_mult(days_to_event: float) -> float:
    """Convex penalty around the release window."""
    if days_to_event >= 5:    return 1.0
    if days_to_event >= 3:    return 0.8
    if days_to_event >= 1:    return 0.5
    if days_to_event >= 0:    return 0.2   # release-day morning
    if days_to_event >= -1:   return 0.0   # release-day post + next session
    if days_to_event >= -2:   return 0.5   # back to half size
    return 1.0
```

Convexité = sortir trop vite est moins coûteux qu'entrer trop tard (asymétrie du payoff vol-trader).

### 14.2 Stability gate asymétrique

État actuel : 3 cycles cohérents requis dans toutes les directions → 9 min de latence à toute transition.

Cible :
```python
# Sortie → calm exige 3 cycles (filtre le bruit)
# Entrée → stressed/pre_event activée immédiatement (réactivité aux vrais events)
if new_label in {"stressed", "pre_event"} and history[0] != new_label:
    return GateDecision.from_label(new_label)  # immediate
# else : require 3 cohérents comme aujourd'hui
```

### 14.3 Parsers dynamiques pour ECB / BoE / FOMC / Eurostat / ONS

État actuel : 16 dates 2026-2027 hardcodées dans `sources/{ecb,boe,fomc}.py`. À chaque changement de calendrier de banque centrale, le système devient stale silencieusement.

Cible : parser HTML / RSS / ICS officiel par source. Voir `events_pipeline_spec.md` §4.

Deadline : Q4 2026 (avant que les dates 2027 ne deviennent obsolètes).

### 14.4 Métrique agreement qualifiée dans l'UI

État actuel : panel zone 2 affiche `Agreement ≥ 70% : 100%` même quand un seul label observé en shadow → 100% trivialement obtenu.

Cible (déjà en place) : afficher `100% — non-discriminant (1 seul label observé)` tant que `len(by_label.keys()) < 2`. Le gate compte comme rouge dans ce cas. Voir `Step1Regime.tsx::_hasMultipleLabelsObserved`.

### 14.5 Z-score min obs

État actuel (depuis v1.0) : `MIN_OBS_ZSCORE = 30`. Si tu as < 30 obs valides, les colonnes "z" du panel restent grises.

Cible (cosmétique, post-Step 2) : afficher `(z, IC95%)` au lieu de `z` seul ; si l'IC contient 0 → grayed-out automatiquement.
