# Étape 2 — Signal detection (Panel 2, PCA factor model)

> Spec de la deuxième étape du workflow trading vol.
>
> **Objectif fonctionnel** : à partir d'une surface IV courante, identifier dans quelle direction la surface est mispricée vs son comportement historique, et exposer 3 signaux orthogonaux (PC1=level, PC2=slope, PC3=smile) qui informent quelle structure de trade est justifiée.
>
> **Prérequis** : étape 1 livrée (`gate_decision.authorized = True`). Sinon panel 2 grayed-out.
>
> **Pas dans cette étape** : choix de structure spécifique, pricing leg-by-leg, sizing, exécution. Ces étapes consomment les signaux PCA mais ne les produisent pas.
>
> **Choix d'option** : Option A (PCA factor model). Décision documentée dans `DECISIONS.md` avec argument signaling pro. Option B (per-tenor signals) explicitement rejetée.

---

## 1. Système formel

| Élément | Spec |
|---|---|
| Agents | Vol-engine cycle (producer), PCA fit job (background), utilisateur (consumer) |
| États | `pca_state ∈ {bootstrap, stable, unstable, refit_in_progress}` |
| Inputs requis | Matrice historique X (T, 30) où T = nombre de snapshots horaires, 30 = (6 tenors × 5 deltas) IVs |
| Outputs | 3 z-scores par cycle : `pc1_z, pc2_z, pc3_z` + flags de qualité `loadings_stable, variance_explained_ok` |
| Contrainte stationnarité | T ≥ 300 obs minimum (10× p), T ≥ 1500 obs idéal (50× p). En attendant : reconstruire historique IB |
| Latence acceptable | signal disponible dans le cycle suivant le snapshot (< 360s end-to-end) |

---

## 2. Décision logic — quand un signal est trade-able

```python
def signal_is_actionable(pc_id: int, pc_state: PcaState, signal: PcaSignal) -> ActionableFlag:
    # Stability gate : ne pas trader sur loadings instables
    if not pc_state.loadings_stable[pc_id]:
        return ActionableFlag(
            actionable=False, 
            reason=f"loadings_unstable_pc{pc_id}",
            flag_in_ui="instability_warning"
        )
    
    # Variance gate : PC doit expliquer suffisamment de variance pour être signal
    if pc_state.variance_explained[pc_id] < MIN_VARIANCE_EXPLAINED[pc_id]:
        return ActionableFlag(
            actionable=False,
            reason=f"low_variance_pc{pc_id}",
            flag_in_ui="low_signal_quality"
        )
    
    # Signal magnitude gate
    if abs(signal.z_score) < THRESHOLDS["weak"]:  # < 1.0
        return ActionableFlag(actionable=False, reason="signal_below_threshold")
    
    # Cohérence gate : signaux contradictoires entre PCs
    if signals_contradict(signal, other_pc_signals):
        return ActionableFlag(
            actionable=False,
            reason="contradictory_signals",
            flag_in_ui="cohérence_warning"
        )
    
    # Stabilité du signal lui-même : au moins 2-3 cycles avec |z| > seuil
    if not signal_is_persistent(signal, history, n_cycles=3):
        return ActionableFlag(actionable=False, reason="signal_not_persistent")
    
    # Tout est OK
    return ActionableFlag(
        actionable=True,
        signal_strength=classify_strength(abs(signal.z_score)),
    )
```

Constantes :
```python
MIN_VARIANCE_EXPLAINED = {1: 0.60, 2: 0.15, 3: 0.05}  # PC1 doit expliquer ≥60%, PC2 ≥15%, PC3 ≥5%
THRESHOLDS = {"weak": 1.0, "moderate": 1.5, "strong": 2.0, "extreme": 3.0}
```

---

## 3. Le panel (UI) — structure 3 colonnes + diagnostics

| Zone | Contenu | Source data | Statut implémentation |
|---|---|---|---|
| 1 — 3 colonnes PC | Pour chaque PC : nom, barre z-score, label CHEAP/FAIR/EXPENSIVE, sub-signals (PC3 only : skew, convex), recommended structure, "Arm trade" button | `pca_signals` table (1 row par PC par cycle) | À implémenter |
| 2 — Time series 3 mois | Mini-chart par PC : évolution z-score sur 90 jours | `pca_signals` historique | Calculable une fois data accumulée |
| 3 — Stability flags | Pour chaque PC : loadings stable yes/no, variance explained %, sign flips count last refit | `pca_state` table | À implémenter — **critique pour signaling pro** |
| 4 — Cohérence indicator | Badge global "signals coherent" / "contradictions detected" | computed cross-PC | Calculable une fois zone 1 dispo |
| 5 — Bootstrap progress (transitionnel) | Barre "Fit converging : T=145/300 obs collected" | `pca_state.bootstrap_progress` | Spécifique phase bootstrap, à retirer une fois stable |

---

## 4. Schema du payload `_pca_signals`

À ajouter dans `latest_vol_surface.surface._pca_signals`. Format JSON :

```jsonc
{
  "_pca_signals": {
    "model_version": "pca_v1_2026_05_03",       // identifiant du modèle PCA fitté
    "fit_timestamp": "2026-05-03T00:00:00Z",    // dernière date de refit
    "fit_window_start": "2025-05-01T00:00:00Z", // début fenêtre fit
    "fit_window_end":   "2026-04-30T23:00:00Z", // fin fenêtre fit
    "n_obs_in_fit": 1456,                        // T réel utilisé
    
    "state": "stable",                           // bootstrap | stable | unstable | refit_in_progress
    
    "variance_explained": {
      "pc1": 0.72,
      "pc2": 0.19,
      "pc3": 0.06,
      "cumulative": 0.97
    },
    
    "loadings_stable": {
      "pc1": true,
      "pc2": true,
      "pc3": false                                // exemple : PC3 instable, ne pas trader
    },
    
    "signals": {
      "pc1": {
        "z_score": 1.8,
        "raw_score": 0.0234,                      // score brut (avant z)
        "label": "CHEAP",                         // CHEAP | FAIR | EXPENSIVE
        "actionable": true,
        "actionable_reason": null,
        "recommended_structure": "straddle_atm_3m"
      },
      "pc2": {
        "z_score": -0.4,
        "raw_score": -0.0012,
        "label": "FAIR",
        "actionable": false,
        "actionable_reason": "signal_below_threshold",
        "recommended_structure": null
      },
      "pc3": {
        "z_score": 2.3,
        "raw_score": 0.0089,
        "label": "CHEAP",
        "actionable": false,
        "actionable_reason": "loadings_unstable_pc3",
        "recommended_structure": null,
        "sub_signals": {
          "skew_z":   0.8,
          "convex_z": 2.5
        }
      }
    },
    
    "coherence": {
      "all_coherent": true,
      "contradictions": []                        // liste de tuples (pc_a, pc_b) en conflit si applicable
    }
  }
}
```

---

## 5. Tables Postgres nécessaires

### 5.1 `surface_snapshots_hourly` — données pour fit PCA

Snapshot horaire de la full surface (30 dim) pour fit PCA. Distinct du payload cycle 180s qui est consommé temps réel et non persisté en wide format.

```sql
CREATE TABLE surface_snapshots_hourly (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    symbol          TEXT NOT NULL DEFAULT 'EURUSD',
    
    -- 30 colonnes IV : 6 tenors × 5 deltas
    iv_1m_10dp      DOUBLE PRECISION,  iv_1m_25dp DOUBLE PRECISION,  iv_1m_atm DOUBLE PRECISION,  iv_1m_25dc DOUBLE PRECISION,  iv_1m_10dc DOUBLE PRECISION,
    iv_2m_10dp      DOUBLE PRECISION,  iv_2m_25dp DOUBLE PRECISION,  iv_2m_atm DOUBLE PRECISION,  iv_2m_25dc DOUBLE PRECISION,  iv_2m_10dc DOUBLE PRECISION,
    iv_3m_10dp      DOUBLE PRECISION,  iv_3m_25dp DOUBLE PRECISION,  iv_3m_atm DOUBLE PRECISION,  iv_3m_25dc DOUBLE PRECISION,  iv_3m_10dc DOUBLE PRECISION,
    iv_4m_10dp      DOUBLE PRECISION,  iv_4m_25dp DOUBLE PRECISION,  iv_4m_atm DOUBLE PRECISION,  iv_4m_25dc DOUBLE PRECISION,  iv_4m_10dc DOUBLE PRECISION,
    iv_5m_10dp      DOUBLE PRECISION,  iv_5m_25dp DOUBLE PRECISION,  iv_5m_atm DOUBLE PRECISION,  iv_5m_25dc DOUBLE PRECISION,  iv_5m_10dc DOUBLE PRECISION,
    iv_6m_10dp      DOUBLE PRECISION,  iv_6m_25dp DOUBLE PRECISION,  iv_6m_atm DOUBLE PRECISION,  iv_6m_25dc DOUBLE PRECISION,  iv_6m_10dc DOUBLE PRECISION,
    
    -- métadonnées source
    source          TEXT NOT NULL,                -- 'live_engine' | 'ib_historical_backfill'
    spot_at_snapshot DOUBLE PRECISION,            -- pour normalisation moneyness éventuelle
    
    -- qualité du snapshot (pour exclure obs corrompues du fit)
    n_strikes_present  INTEGER,                   -- ≤ 30, doit être 30 pour inclusion
    has_no_arb_violation BOOLEAN DEFAULT false,   -- exclude butterfly violations du fit
    
    UNIQUE (symbol, timestamp)
);

CREATE INDEX ix_snapshots_hourly_symbol_ts ON surface_snapshots_hourly (symbol, timestamp DESC);
CREATE INDEX ix_snapshots_hourly_clean ON surface_snapshots_hourly (timestamp DESC) 
    WHERE n_strikes_present = 30 AND has_no_arb_violation = false;
```

**Cardinalité** : 24 rows / jour × 365 = ~8800 / an. Sur 5 ans : ~44k rows. Trivial.

**Pattern de mise à jour** : 
- Live : background job toutes les heures qui prend le `latest_vol_surface` courant et persiste en wide format
- Bootstrap : script one-off qui consomme historique IB reconstruit, fillte cette table

---

### 5.2 `pca_models` — versioning des modèles PCA fittés

Une row par refit. Permet rollback, audit, comparaison entre modèles.

```sql
CREATE TABLE pca_models (
    id                  BIGSERIAL PRIMARY KEY,
    version             TEXT NOT NULL UNIQUE,         -- ex: "pca_v1_2026_05_03"
    
    -- fit metadata
    fit_timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fit_window_start    TIMESTAMPTZ NOT NULL,
    fit_window_end      TIMESTAMPTZ NOT NULL,
    n_obs_used          INTEGER NOT NULL,             -- T effectif après filtrage qualité
    
    -- PCA outputs (stockés en JSONB pour flexibilité dimensionnelle)
    means               JSONB NOT NULL,                -- vecteur (30,) μ par feature
    stds                JSONB NOT NULL,                -- vecteur (30,) σ par feature (pour standardisation)
    loadings            JSONB NOT NULL,                -- matrice (n_components, 30)
    eigenvalues         JSONB NOT NULL,                -- vecteur (n_components,) variances expliquées en abs
    variance_explained_ratio JSONB NOT NULL,           -- vecteur (n_components,) en %
    
    n_components_kept   INTEGER NOT NULL DEFAULT 6,    -- on garde 6 mais on n'utilise que 3
    
    -- diagnostics
    is_active           BOOLEAN NOT NULL DEFAULT false,  -- un seul modèle actif à la fois
    superseded_by       BIGINT REFERENCES pca_models(id),
    
    -- comparison vs previous model (pour stability check)
    cosine_similarity_pc1   DOUBLE PRECISION,         -- vs version précédente
    cosine_similarity_pc2   DOUBLE PRECISION,
    cosine_similarity_pc3   DOUBLE PRECISION,
    sign_flip_pc1           BOOLEAN,                  -- true si eigenvector a flippé
    sign_flip_pc2           BOOLEAN,
    sign_flip_pc3           BOOLEAN,
    
    notes               TEXT
);

CREATE UNIQUE INDEX ix_pca_models_active ON pca_models (is_active) WHERE is_active = true;
CREATE INDEX ix_pca_models_fit_ts ON pca_models (fit_timestamp DESC);
```

**Cardinalité** : 1 row / refit. Si refit hebdo : ~52 / an. Stockage négligeable.

**Pattern** :
- Refit job (cron weekly) : 
  1. Lit `surface_snapshots_hourly` sur fenêtre rolling 12 mois
  2. Filtre obs qualité (n_strikes=30, pas d'arb violation)
  3. Standardise (z-score par colonne)
  4. Fit `sklearn.decomposition.PCA(n_components=6)`
  5. Calcule cosine_sim vs `is_active=true` row
  6. INSERT nouvelle row, UPDATE ancienne `is_active=false, superseded_by=new_id`
  7. SET nouvelle row `is_active=true`

---

### 5.3 `pca_signals` — signaux par cycle

Une row par PC par cycle. Permet historique pour panel chart et walk-forward backtest futur.

```sql
CREATE TABLE pca_signals (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL,
    symbol              TEXT NOT NULL DEFAULT 'EURUSD',
    
    pca_model_id        BIGINT NOT NULL REFERENCES pca_models(id),
    pc_id               INTEGER NOT NULL,             -- 1, 2, 3, ...
    
    -- valeurs
    raw_score           DOUBLE PRECISION NOT NULL,    -- projection brute
    z_score             DOUBLE PRECISION NOT NULL,    -- standardisé vs distribution historique
    label               TEXT NOT NULL,                -- 'CHEAP' | 'FAIR' | 'EXPENSIVE'
    
    -- actionability
    actionable          BOOLEAN NOT NULL,
    actionable_reason   TEXT,                          -- null si actionable=true
    
    -- sub-signals (pour PC3 essentiellement)
    sub_signals         JSONB,                         -- {skew_z, convex_z} ou null
    
    -- recommandation structure (computed)
    recommended_structure TEXT,                       -- ex: 'straddle_atm_3m', 'butterfly_25d_3m', 'calendar_1m_3m'
    
    UNIQUE (symbol, timestamp, pca_model_id, pc_id),
    CONSTRAINT chk_label CHECK (label IN ('CHEAP', 'FAIR', 'EXPENSIVE')),
    CONSTRAINT chk_pc_id CHECK (pc_id > 0)
);

CREATE INDEX ix_pca_signals_symbol_ts ON pca_signals (symbol, timestamp DESC);
CREATE INDEX ix_pca_signals_actionable ON pca_signals (timestamp DESC) WHERE actionable = true;
```

**Cardinalité** : 1 row / cycle / PC. À 180s par cycle, 3 PCs : ~1440 / jour. Sur 5 ans : ~2.6M rows. Toujours gérable, indexer correctement.

**Requêtes typiques** :
```sql
-- Signal actuel pour chaque PC
SELECT pc_id, z_score, label, actionable
FROM pca_signals
WHERE symbol = 'EURUSD' AND timestamp = (SELECT MAX(timestamp) FROM pca_signals);

-- Time series 90j pour mini-chart panel
SELECT timestamp, z_score
FROM pca_signals
WHERE symbol = 'EURUSD' AND pc_id = 1 
  AND timestamp > NOW() - INTERVAL '90 days'
ORDER BY timestamp;

-- Persistence check : signal stable sur 3 cycles ?
SELECT z_score FROM pca_signals
WHERE symbol = 'EURUSD' AND pc_id = 1
ORDER BY timestamp DESC LIMIT 3;
```

---

### 5.4 `pca_stability_log` — diagnostics de stabilité par refit

Log des comparaisons entre versions successives pour visualiser la stabilité dans le temps.

```sql
CREATE TABLE pca_stability_log (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    new_model_id        BIGINT NOT NULL REFERENCES pca_models(id),
    previous_model_id   BIGINT REFERENCES pca_models(id),       -- null pour le tout premier fit
    
    pc_id               INTEGER NOT NULL,
    
    cosine_similarity   DOUBLE PRECISION,                       -- entre loadings nouveau vs ancien
    sign_flipped        BOOLEAN,
    variance_change_pct DOUBLE PRECISION,                       -- (var_new - var_old) / var_old
    
    stability_verdict   TEXT NOT NULL,                          -- 'stable' | 'minor_drift' | 'unstable'
    
    CONSTRAINT chk_verdict CHECK (stability_verdict IN ('stable', 'minor_drift', 'unstable'))
);

CREATE INDEX ix_pca_stability_log_ts ON pca_stability_log (timestamp DESC);
CREATE INDEX ix_pca_stability_log_pc ON pca_stability_log (pc_id, timestamp DESC);
```

**Verdict logic** :
- `stable` si cosine_sim > 0.95 ET variance_change < 10% ET pas de sign flip
- `minor_drift` si cosine_sim ∈ [0.85, 0.95] OU sign_flipped=true OU variance_change ∈ [10%, 25%]
- `unstable` si cosine_sim < 0.85 OU variance_change > 25%

**Cardinalité** : 1 row / PC / refit. Si refit hebdo et 3 PCs surveillés : 156 / an. Trivial.

**Usage** : populates le diagnostic Zone 3 du panel (loadings stable yes/no).

---

### 5.5 `signal_recommendations_map` — mapping PC × strength → structure

Table de référence (lookup statique) pour mapper un signal vers une structure recommandée. Externaliser permet hot-reload + audit.

```sql
CREATE TABLE signal_recommendations_map (
    id                  SERIAL PRIMARY KEY,
    
    pc_id               INTEGER NOT NULL,
    signal_label        TEXT NOT NULL,              -- 'CHEAP' | 'EXPENSIVE'
    recommended_structure TEXT NOT NULL,
    default_tenor       TEXT NOT NULL,              -- '1M' | '2M' | ... | '6M'
    
    description         TEXT,
    rationale           TEXT,                       -- pourquoi cette structure pour ce PC ?
    
    is_active           BOOLEAN NOT NULL DEFAULT true,
    
    UNIQUE (pc_id, signal_label, is_active),
    CONSTRAINT chk_label CHECK (signal_label IN ('CHEAP', 'EXPENSIVE'))
);

-- Seed initial
INSERT INTO signal_recommendations_map (pc_id, signal_label, recommended_structure, default_tenor, description, rationale) VALUES
    (1, 'CHEAP',     'straddle_atm',    '3M', 'Long straddle ATM',  'PC1 CHEAP = vol level low → buy vol via ATM straddle, max convexity to vol moves'),
    (1, 'EXPENSIVE', 'short_strangle',  '3M', 'Short OTM strangle', 'PC1 EXPENSIVE = vol level high → sell vol via OTM strangle, contained tail risk'),
    (2, 'CHEAP',     'calendar_long',   '1M_3M', 'Calendar buying long tenor', 'PC2 CHEAP = term slope inverted → buy long tenor relative to short'),
    (2, 'EXPENSIVE', 'calendar_short',  '1M_3M', 'Calendar selling long tenor', 'PC2 EXPENSIVE = term slope steep → sell long tenor relative to short'),
    (3, 'CHEAP',     'long_butterfly_25d', '3M', 'Long 25d butterfly', 'PC3 CHEAP = wings cheap relative to ATM → buy butterfly to capture smile reversion'),
    (3, 'EXPENSIVE', 'short_butterfly_25d', '3M', 'Short 25d butterfly', 'PC3 EXPENSIVE = wings rich relative to ATM → sell butterfly');
```

**Cardinalité** : 6 rows. Statique.

---

## 6. Schéma relationnel

```
┌─────────────────────────┐      ┌──────────────────────────┐
│ surface_snapshots_      │      │   IB historical          │
│   hourly                │◄────│   (one-off backfill)     │
│  (1 / hour live)        │      │                          │
└──────────┬──────────────┘      └──────────────────────────┘
           │
           │  reads window
           ▼
┌─────────────────────────┐
│   PCA refit job         │
│   (weekly cron)         │
└──────────┬──────────────┘
           │  writes new version
           ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│   pca_models            │─────►│  pca_stability_log       │
│  (1 / refit)            │      │  (cosine sim vs prev)    │
└──────────┬──────────────┘      └──────────────────────────┘
           │ active model
           │ read each cycle
           ▼
┌─────────────────────────┐      ┌──────────────────────────┐
│ vol-engine cycle 180s   │─────►│  pca_signals             │
│ projects current        │      │  (1 / cycle / PC)        │
│ snapshot on loadings    │      └──────────────────────────┘
└──────────┬──────────────┘                  │
           │                                  │  reads recommendation
           │  publishes _pca_signals          ▼
           │  in latest_vol_surface     ┌────────────────────────┐
           │                            │ signal_recommendations │
           ▼                            │   _map (lookup)        │
   Frontend Panel 2                     └────────────────────────┘
```

---

## 7. Pipeline backend par étape de cycle

### 7.1 Background job : PCA refit (hebdomadaire)

```python
# src/services/pca_fitter/main.py — cron weekly
def refit_pca(db: Session) -> None:
    """Refit PCA model on rolling 12-month window."""
    
    # 1. Lecture fenêtre rolling
    window_end = datetime.utcnow()
    window_start = window_end - timedelta(days=365)
    
    snapshots_df = pd.read_sql(
        f"""
        SELECT * FROM surface_snapshots_hourly
        WHERE symbol = 'EURUSD'
          AND timestamp BETWEEN '{window_start}' AND '{window_end}'
          AND n_strikes_present = 30
          AND has_no_arb_violation = false
        ORDER BY timestamp
        """,
        db.bind
    )
    
    # Garde-fou
    if len(snapshots_df) < 100:
        log.warning(f"PCA refit aborted: only {len(snapshots_df)} obs available, need ≥100")
        return
    
    # 2. Extraction matrice X (T, 30)
    iv_columns = [c for c in snapshots_df.columns if c.startswith("iv_")]
    X = snapshots_df[iv_columns].values  # shape (T, 30)
    
    # 3. Standardisation
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    X_std = (X - means) / stds
    
    # 4. PCA fit
    pca = PCA(n_components=6)
    pca.fit(X_std)
    
    loadings = pca.components_              # shape (6, 30)
    eigenvalues = pca.explained_variance_   # shape (6,)
    var_ratio = pca.explained_variance_ratio_  # shape (6,)
    
    # 5. Comparaison vs modèle actif précédent (sign correction)
    previous = db.query(PcaModel).filter_by(is_active=True).first()
    
    if previous is not None:
        prev_loadings = np.array(previous.loadings)  # JSONB → array
        cosine_sims = []
        sign_flips = []
        
        for i in range(3):
            cos_sim = np.dot(loadings[i], prev_loadings[i]) / (
                np.linalg.norm(loadings[i]) * np.linalg.norm(prev_loadings[i])
            )
            sign_flipped = cos_sim < 0
            
            if sign_flipped:
                # Correct le sign : flip eigenvector pour préserver consistance temporelle
                loadings[i] = -loadings[i]
                cos_sim = abs(cos_sim)
            
            cosine_sims.append(cos_sim)
            sign_flips.append(sign_flipped)
    else:
        cosine_sims = [None, None, None]
        sign_flips = [False, False, False]
    
    # 6. Persistence nouvelle version
    new_version = f"pca_v1_{datetime.utcnow():%Y_%m_%d}"
    new_model = PcaModel(
        version=new_version,
        fit_window_start=window_start,
        fit_window_end=window_end,
        n_obs_used=len(snapshots_df),
        means=means.tolist(),
        stds=stds.tolist(),
        loadings=loadings.tolist(),
        eigenvalues=eigenvalues.tolist(),
        variance_explained_ratio=var_ratio.tolist(),
        n_components_kept=6,
        is_active=False,  # set true after stability check
        cosine_similarity_pc1=cosine_sims[0],
        cosine_similarity_pc2=cosine_sims[1],
        cosine_similarity_pc3=cosine_sims[2],
        sign_flip_pc1=sign_flips[0],
        sign_flip_pc2=sign_flips[1],
        sign_flip_pc3=sign_flips[2],
    )
    db.add(new_model)
    db.flush()  # need ID for stability log
    
    # 7. Stability log
    for pc_id in [1, 2, 3]:
        verdict = compute_stability_verdict(
            cos_sim=cosine_sims[pc_id - 1],
            sign_flipped=sign_flips[pc_id - 1],
            var_change_pct=compute_var_change(var_ratio, previous, pc_id - 1) if previous else None
        )
        db.add(PcaStabilityLog(
            new_model_id=new_model.id,
            previous_model_id=previous.id if previous else None,
            pc_id=pc_id,
            cosine_similarity=cosine_sims[pc_id - 1],
            sign_flipped=sign_flips[pc_id - 1],
            variance_change_pct=compute_var_change(var_ratio, previous, pc_id - 1) if previous else None,
            stability_verdict=verdict,
        ))
    
    # 8. Activation : seulement si tous les PC sont au moins minor_drift
    overall_stability = check_overall_stability(db, new_model.id)
    if overall_stability != "unstable":
        if previous:
            previous.is_active = False
            previous.superseded_by = new_model.id
        new_model.is_active = True
        log.info(f"PCA refit complete: {new_version} now active")
    else:
        log.warning(f"PCA refit {new_version} kept inactive due to instability")
        # Modèle précédent reste actif
    
    db.commit()


def compute_stability_verdict(cos_sim, sign_flipped, var_change_pct):
    if cos_sim is None:
        return "stable"  # premier fit
    if cos_sim > 0.95 and not sign_flipped and abs(var_change_pct or 0) < 0.10:
        return "stable"
    if cos_sim < 0.85 or abs(var_change_pct or 0) > 0.25:
        return "unstable"
    return "minor_drift"
```

---

### 7.2 Cycle vol-engine : projection snapshot courant + signal generation

À ajouter dans `src/engines/vol/engine.py` après le step `_step_regime_snapshot` :

```python
def _step_pca_signals(self, surface: dict, db: Session) -> dict:
    """Project current snapshot on active PCA loadings → 3 z-scores."""
    
    # 1. Lecture modèle actif
    pca_model = db.query(PcaModel).filter_by(is_active=True).first()
    
    if pca_model is None:
        return {
            "model_version": None,
            "state": "bootstrap",
            "signals": {},
            "diagnostics": {"reason": "no_active_pca_model"}
        }
    
    # 2. Construction du vecteur courant (30 dim) depuis le payload surface
    current_iv = []
    for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
        for delta in ["10dp", "25dp", "atm", "25dc", "10dc"]:
            iv = surface.get(tenor, {}).get(delta, {}).get("iv")
            if iv is None:
                # Surface incomplète — skip ce cycle
                return {"signals": {}, "diagnostics": {"reason": "incomplete_surface"}}
            current_iv.append(iv * 100)  # en %
    
    x = np.array(current_iv)  # shape (30,)
    
    # 3. Standardisation avec means/stds du modèle
    means = np.array(pca_model.means)
    stds = np.array(pca_model.stds)
    x_std = (x - means) / stds
    
    # 4. Projection : raw_scores = X @ loadings.T
    loadings = np.array(pca_model.loadings)  # shape (6, 30)
    raw_scores = x_std @ loadings.T          # shape (6,) — prend tous, on use top 3
    
    # 5. Z-scores : standardiser raw_scores vs distribution historique des projections
    # On a besoin des projections historiques pour calculer μ, σ
    historical_projections = db.execute(
        select(PcaSignal.raw_score, PcaSignal.pc_id)
        .where(PcaSignal.pca_model_id == pca_model.id)
        .where(PcaSignal.timestamp > datetime.utcnow() - timedelta(days=90))
    ).all()
    
    z_scores = {}
    labels = {}
    for pc_id in [1, 2, 3]:
        idx = pc_id - 1
        raw = raw_scores[idx]
        
        hist = [p.raw_score for p in historical_projections if p.pc_id == pc_id]
        if len(hist) < 30:
            # Pas assez d'historique pour z-score — fallback : z=0
            z = 0.0
            label = "FAIR"
        else:
            mu = np.mean(hist)
            sigma = np.std(hist)
            z = (raw - mu) / sigma if sigma > 0 else 0.0
            label = classify_label(z, threshold=1.5)
        
        z_scores[pc_id] = z
        labels[pc_id] = label
    
    # 6. Stability check (lecture pca_stability_log)
    last_stability = {pc: get_last_stability_verdict(db, pca_model.id, pc) for pc in [1, 2, 3]}
    loadings_stable = {pc: last_stability[pc] in ("stable", "minor_drift") for pc in [1, 2, 3]}
    
    # 7. Signal generation
    var_ratio = np.array(pca_model.variance_explained_ratio)
    signals_payload = {}
    
    for pc_id in [1, 2, 3]:
        actionable_flag = check_actionable(
            pc_id=pc_id,
            z_score=z_scores[pc_id],
            loadings_stable=loadings_stable[pc_id],
            variance_explained=var_ratio[pc_id - 1],
            label=labels[pc_id]
        )
        
        # Recommandation structure
        rec_structure = None
        if actionable_flag.actionable and labels[pc_id] != "FAIR":
            rec = db.query(SignalRecommendationsMap).filter_by(
                pc_id=pc_id, signal_label=labels[pc_id], is_active=True
            ).first()
            rec_structure = f"{rec.recommended_structure}_{rec.default_tenor}" if rec else None
        
        # Sub-signals pour PC3
        sub_signals = None
        if pc_id == 3:
            sub_signals = compute_pc3_sub_signals(x_std, loadings[2])
        
        signals_payload[f"pc{pc_id}"] = {
            "z_score": round(z_scores[pc_id], 2),
            "raw_score": round(raw_scores[pc_id - 1], 4),
            "label": labels[pc_id],
            "actionable": actionable_flag.actionable,
            "actionable_reason": actionable_flag.reason,
            "recommended_structure": rec_structure,
            "sub_signals": sub_signals,
        }
        
        # Persistence
        db.add(PcaSignal(
            timestamp=datetime.utcnow(),
            symbol="EURUSD",
            pca_model_id=pca_model.id,
            pc_id=pc_id,
            raw_score=raw_scores[pc_id - 1],
            z_score=z_scores[pc_id],
            label=labels[pc_id],
            actionable=actionable_flag.actionable,
            actionable_reason=actionable_flag.reason,
            sub_signals=sub_signals,
            recommended_structure=rec_structure,
        ))
    
    # 8. Coherence check
    coherence = check_coherence(signals_payload)
    
    db.commit()
    
    return {
        "model_version": pca_model.version,
        "fit_timestamp": pca_model.fit_timestamp.isoformat(),
        "fit_window_start": pca_model.fit_window_start.isoformat(),
        "fit_window_end": pca_model.fit_window_end.isoformat(),
        "n_obs_in_fit": pca_model.n_obs_used,
        "state": "stable" if all(loadings_stable.values()) else "unstable",
        "variance_explained": {
            "pc1": round(var_ratio[0], 3),
            "pc2": round(var_ratio[1], 3),
            "pc3": round(var_ratio[2], 3),
            "cumulative": round(var_ratio[:3].sum(), 3),
        },
        "loadings_stable": loadings_stable,
        "signals": signals_payload,
        "coherence": coherence,
    }
```

Branchement dans `vol_cycle` :
```python
surface["_pca_signals"] = self._step_pca_signals(surface, db_session)
```

---

### 7.3 Background job : snapshot horaire pour fit PCA

```python
# src/services/snapshot_collector/main.py — cron toutes les heures
def collect_hourly_snapshot(db: Session, redis: Redis) -> None:
    """Persist current latest_vol_surface to surface_snapshots_hourly."""
    
    payload_raw = redis.get("latest_vol_surface:EURUSD")
    if payload_raw is None:
        log.warning("No surface in Redis, skip snapshot")
        return
    
    payload = json.loads(payload_raw)
    surface = payload["surface"]
    
    # Extract 30 IVs
    iv_dict = {}
    n_present = 0
    for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
        for delta in ["10dp", "25dp", "atm", "25dc", "10dc"]:
            iv = surface.get(tenor, {}).get(delta, {}).get("iv")
            col_name = f"iv_{tenor.lower()}_{delta}"
            iv_dict[col_name] = iv * 100 if iv is not None else None
            if iv is not None:
                n_present += 1
    
    # Check no-arb violations across SVI fits
    has_violation = any(
        surface.get("_svi", {}).get(t, {}).get("butterfly_g_min", 0) < 0
        for t in ["1M", "2M", "3M", "4M", "5M", "6M"]
    )
    
    snapshot = SurfaceSnapshotHourly(
        timestamp=datetime.utcnow(),
        symbol="EURUSD",
        source="live_engine",
        spot_at_snapshot=get_current_spot(redis),
        n_strikes_present=n_present,
        has_no_arb_violation=has_violation,
        **iv_dict
    )
    db.add(snapshot)
    db.commit()
```

---

### 7.4 One-off : reconstruction historique IB

```python
# scripts/backfill_ib_historical.py — script à lancer une fois
def backfill_from_ib(start_date: date, end_date: date) -> None:
    """Reconstruct surface_snapshots_hourly from IB historical FOP data."""
    
    ib = IBClient()
    
    # Liste des dates business à reconstruire
    business_days = pd.bdate_range(start_date, end_date)
    
    for day in tqdm(business_days):
        # Pour chaque tenor, identifier les contracts qui existaient ce jour
        for tenor in ["1M", "2M", "3M", "4M", "5M", "6M"]:
            target_dte = TENOR_TO_DTE[tenor]  # 30, 60, 90, 120, 150, 180
            target_expiry = day + timedelta(days=target_dte)
            
            # Roundez à l'expiry IB la plus proche (3rd Friday)
            actual_expiry = nearest_third_friday(target_expiry)
            
            # Get spot historical
            spot = ib.get_historical_spot("EURUSD", day)
            forward = compute_forward(spot, day, actual_expiry)
            
            # Pour chaque delta pillar, calculer le strike et fetcher l'IV historique
            for delta_pillar in ["10dp", "25dp", "atm", "25dc", "10dc"]:
                strike = solve_strike_for_delta(
                    forward=forward, 
                    target_delta=DELTA_VALUES[delta_pillar],
                    iv_initial_guess=0.07
                )
                
                contract = create_fop_contract(
                    symbol="EURUSD", 
                    expiry=actual_expiry, 
                    strike=strike, 
                    right="C" if "dc" in delta_pillar else "P"
                )
                
                # Fetch historical IV pour ce contract à cette date
                iv = ib.get_historical_iv(contract, day)
                
                # ... store dans snapshot_buffer ...
        
        # Une fois tous les tenors × deltas collectés pour le jour
        persist_snapshot(snapshot_buffer, source="ib_historical_backfill")
        time.sleep(1)  # rate limit
```

**Réalisme** : ce script va prendre 1-2 jours à debug + 4-12h à tourner. Beaucoup d'edge cases (expiry rolls, market closures, trous de data sur les wings).

---

## 8. Estimation effort par sous-tâche

| Sous-tâche | Effort | Bloquant pour MVP étape 2 ? |
|---|---|---|
| Migration Postgres : 5 tables + indices | 0.5 j | Oui |
| Seed `signal_recommendations_map` | 10 min | Oui |
| Snapshot collector (background job hourly) | 0.5 j | Oui — sans ça pas de data |
| Script backfill IB historical | 2 j dev + 1 j run/debug | Oui pour avoir T initial |
| PCA refit job (hebdo cron) | 2 j | Oui |
| Stability log + verdict logic | 1 j | Oui — point différenciateur |
| `_step_pca_signals` cycle integration | 1.5 j | Oui |
| Helper `check_actionable`, `check_coherence`, `classify_label` | 0.5 j | Oui |
| Frontend Panel 2 component | 2-3 j | Oui |
| Stability diagnostic visible dans Panel 2 | 1 j | Oui — point différenciateur |
| Bootstrap progress UI (transitionnel) | 0.5 j | Non |
| Tests : PCA fit determinism, stability detection, sign correction | 1.5 j | Oui |
| **Total MVP fonctionnel** | **~13 jours dev** | |

Avec backfill IB en parallèle (1-2 sem au total clock-time si bien parallélisé).

---

## 9. Stratégie de bootstrap (avant T suffisant)

Trois phases successives selon le T accumulé :

| Phase | T disponible | Comportement engine | Affichage Panel 2 |
|---|---|---|---|
| Pre-bootstrap | T < 100 | Pas de fit PCA. `_pca_signals.state = "bootstrap"` | "PCA model bootstrapping. T=X/100 obs collected." Pas de signaux affichés. |
| Bootstrap viable | 100 ≤ T < 300 | Fit PCA mais marqué `low_confidence`. Variance explained probablement bruitée | Signaux affichés mais grayed-out, label "LOW CONFIDENCE — collecting more data" |
| Stable | T ≥ 300 | Fit PCA standard, refit hebdo | Panel pleinement fonctionnel |

**Recommandation** : combiner backfill IB historical (ramène T initial à ~300 instantanément) + collection live (continue d'enrichir).

---

## 10. Tests à écrire (acceptance criteria)

```python
# test_pca_pipeline.py

def test_pca_fit_deterministic():
    """Same X → same loadings (sklearn PCA is deterministic if random_state set)"""
    X = generate_synthetic_surface(n_obs=500, seed=42)
    pca1 = fit_pca(X)
    pca2 = fit_pca(X)
    assert np.allclose(pca1.loadings, pca2.loadings)

def test_sign_correction_handles_flip():
    """Eigenvector flip should be corrected to preserve temporal consistency"""
    X1 = generate_synthetic_surface(n_obs=500, seed=42)
    pca1 = fit_pca(X1)
    
    # Generate X2 such that PCA fit naturally produces flipped PC1
    X2 = generate_perturbed_surface(X1)
    pca2 = fit_pca_with_sign_correction(X2, reference=pca1)
    
    cos_sim = np.dot(pca1.loadings[0], pca2.loadings[0])
    assert cos_sim > 0  # corrected, no flip

def test_unstable_loadings_blocks_signals(db):
    """If PC2 loadings cosine_sim < 0.85 vs previous, signal must be flagged not actionable"""
    create_pca_model(version="v1", loadings_pc2=[0.1]*30, is_active=False)
    create_pca_model(version="v2", loadings_pc2=[-0.1]*30, is_active=True)  # totally different
    
    payload = engine._step_pca_signals(mock_surface, db)
    assert payload["loadings_stable"]["pc2"] is False
    assert payload["signals"]["pc2"]["actionable"] is False
    assert "loadings_unstable" in payload["signals"]["pc2"]["actionable_reason"]

def test_low_variance_explained_blocks_pc3():
    """If PC3 explains < 5% variance, it cannot be actionable"""
    create_pca_model(variance_explained_ratio=[0.7, 0.2, 0.03], is_active=True)
    
    payload = engine._step_pca_signals(mock_surface, db)
    if payload["signals"]["pc3"]["z_score"] > 2.0:
        assert payload["signals"]["pc3"]["actionable"] is False
        assert payload["signals"]["pc3"]["actionable_reason"] == "low_variance_pc3"

def test_coherence_check_detects_contradiction():
    """PC1 says CHEAP and PC2 says EXPENSIVE on same general direction → contradiction"""
    signals = {
        "pc1": {"z_score": 2.0, "label": "CHEAP", ...},
        "pc2": {"z_score": -1.8, "label": "EXPENSIVE", ...},
    }
    coherence = check_coherence(signals)
    # spec exact contradictions à définir avec utilisateur

def test_signal_persistence_3_cycles():
    """Signal must hold for 3 consecutive cycles before actionable=True"""
    # Cycle 1, 2, 3 with z_pc1 = 2.0, 2.1, 2.0
    for i in range(3):
        payload = engine._step_pca_signals(mock_surface, db)
    
    last_signal = payload["signals"]["pc1"]
    assert last_signal["actionable"] is True

def test_bootstrap_progress_reported():
    """Pre-bootstrap : panel shows progress, no signals"""
    db.query(SurfaceSnapshotHourly).delete()
    # Insert 50 snapshots
    for i in range(50):
        db.add(SurfaceSnapshotHourly(timestamp=datetime.utcnow() - timedelta(hours=i), ...))
    
    refit_pca(db)
    
    # No active model
    assert db.query(PcaModel).filter_by(is_active=True).count() == 0
    
    payload = engine._step_pca_signals(mock_surface, db)
    assert payload["state"] == "bootstrap"
```

---

## 11. Ce qui n'est PAS dans cette étape (et où ça ira)

| Concept | Étape future |
|---|---|
| Pricing leg-by-leg de la structure recommandée | Étape 3 (Trade Preview) |
| Calcul des greeks nets | Étape 3 |
| Sizing final | Étape 3 |
| Walk-forward backtest pour valider Sharpe | Étape backtest (post étapes 1-5) |
| Cost model intégré aux signaux | Étape 3 (réduit les signaux marginaux) |
| Capacity analysis (à quel notional le signal s'évanouit) | Étape backtest |

Étape 2 ne fait que : **"où est la mispricing dans l'espace factoriel"**, et **"est-ce que le signal est trade-able méthodologiquement"**. Pas de quantification du P&L attendu, ça vient en étape 3.

---

## 12. Definition of done — étape 2

L'étape 2 est livrée quand :

- [ ] 5 tables Postgres créées (surface_snapshots_hourly, pca_models, pca_signals, pca_stability_log, signal_recommendations_map)
- [ ] `signal_recommendations_map` seedée (6 rows)
- [ ] Backfill IB historical exécuté avec succès → T ≥ 300 obs
- [ ] Snapshot collector tourne en background, persiste 1 row / heure
- [ ] PCA refit job tourne, 1er fit produit `pca_models` row avec `is_active=true`
- [ ] Cycle vol-engine appelle `_step_pca_signals` et publie `_pca_signals` dans payload
- [ ] Panel 2 frontend affiche 3 colonnes PC + diagnostics stability (zone 3) + coherence indicator
- [ ] Stability diagnostic est **visible** dans le panel, pas caché dans un menu (point différenciateur signaling pro)
- [ ] Aucun signal n'apparaît `actionable=true` si stability=unstable OU variance_explained insuffisante
- [ ] Tests unitaires passent : déterminisme PCA fit, sign correction, blocking unstable loadings, coherence check
- [ ] Heartbeat + freshness check : panel grayed-out si `_pca_signals.timestamp` > 360s

---

## 13. Décisions de design notables (pour `DECISIONS.md`)

1. **PCA standardisé sur IV en %** plutôt que log(IV) ou variance. Rationale : interprétation directe des loadings en termes de mouvements de surface. Variance aurait des unités² peu lisibles, log(IV) compresserait les wings utilement mais ajoute complexité.

2. **Fenêtre rolling 12 mois pour fit PCA**, refit hebdomadaire. Trade-off : assez de data pour fit stable, pas trop ancien pour rater régime change. Alternative : fenêtre adaptative selon volatilité du régime (plus court en stressed, plus long en calm) — laissé pour itération future.

3. **6 PCs stockés mais 3 utilisés**. Stocker plus permet (a) analyser ex-post si PC4-6 contiennent du signal exploitable, (b) calculer reconstruction error pour détection régime change.

4. **Sign correction obligatoire à chaque refit**. Sans ça, eigenvectors flippent aléatoirement entre refits → z-scores discontinus côté frontend, panel cassé pour user.

5. **Stability log séparé du model log**. Permet de retracer l'évolution de la stabilité même si le model log est purgé. Audit facilité.

6. **Recommended structure dans table externe** plutôt que hardcodée. Permet hot-reload + audit du mapping signal→trade. Découplage concerns.

7. **Z-scores calculés vs distribution rolling 90j des projections**, pas vs distribution théorique N(0,1). Rationale : les projections empiriques peuvent avoir tails plus lourdes que normal, biases mean-reverting → z-scores empiriques sont plus honnêtes.

---

## 14. Backtest walk-forward (étape future, mentionné pour mémoire)

Une fois étapes 1-5 livrées et système live tourne pendant 3-6 mois, walk-forward backtest devient pertinent :

```
Pour chaque date t dans historique:
    fit_window = [t - 12 mois, t]
    test_window = [t, t + 1 mois]
    
    1. Refit PCA sur fit_window
    2. Project chaque snapshot de test_window sur loadings
    3. Generate signals → trades (selon règles step 3 sizing)
    4. Compute P&L net (avec cost model)
    5. Stocker dans backtest_results table

À la fin :
    Sharpe IS vs Sharpe OOS — gap = overfitting
    Sharpe par PC — quel facteur est le vrai alpha
    Drawdown distribution — robustesse
    Capacity — à quel notional Sharpe se dégrade
```

Schéma table dédiée (`backtest_results`) à spec'er dans le doc backtest. Pas dans le scope étape 2.
