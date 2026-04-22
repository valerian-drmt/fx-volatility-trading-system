# Plan de refonte du pipeline de trading vol — EUR/USD FOP

Liste structurée de prompts à passer à Claude Code, par ordre de priorité
d'exécution. Chaque item est un prompt indépendant, auto-contenu.

Cible : passer d'un dashboard "signal" à un moteur de research-to-trade
avec edge théoriquement fondé sur la vraie mesure risk-neutral.

---

## Phase 0 — Corriger les incohérences bloquantes

### P0.1 — Unifier le seuil signal entre doc et code

> Dans `services/vol/engine.py` et `docs/VOL_MODEL.md`, il y a une
> incohérence : la doc dit threshold = 0.20% (20 bps), le code utilise
> `SIGNAL_ECART_THRESHOLD_PCT = 1.0` (100 bps). Identifie la source
> utilisée en production, puis aligne l'autre. Ajoute un test qui lit
> la constante du code et vérifie qu'elle match la valeur documentée
> dans VOL_MODEL.md section 4. Pas de magic number en dur, tout vient
> de `config/vol_config.json`.

### P0.2 — Sortir δ_book du signal

> δ_book (portfolio book adjustment) pollue σ_fair : la fair value ne
> doit pas dépendre de mon inventaire. Refactor :
>
> 1. Supprimer δ_book de la formule σ_fair dans `services/vol/engine.py`
> 2. Déplacer le mécanisme dans une nouvelle couche
>    `services/risk/position_sizer.py` qui prend le signal pur et
>    module la **taille** du trade via le book ratio.
>
> Formule :
> `size = base_size × (1 - α_book × |ratio|) × sign(signal)`
> avec refus du trade si ratio même signe que signal et |ratio| > 0.8.
>
> Teste que le signal pur reste invariant à mon vega actuel.

### P0.3 — Ajouter assertions mesure P vs Q

> Dans `services/vol/engine.py`, ajoute des assertions et docstrings
> explicites distinguant les grandeurs P (physique) et Q (risk-neutral).
> Convention :
>
> - Tout symbole préfixé `rv_` ou `garch_` est sous P
> - Tout symbole préfixé `iv_` ou `sigma_mid_` est sous Q
>
> Ajoute un commentaire en tête du module expliquant que comparer
> σ_fair (P-based) à σ_mid (Q-based) sans ajustement VRP est
> économiquement incorrect — c'est l'objet des phases suivantes.

---

## Phase 1 — Corriger la mesure Q : VRP conditionnel

### P1.1 — Estimateur VRP empirique par régime

> Crée `core/vol/vrp.py` avec :
>
> - `compute_realized_vrp(iv_history, rv_history, horizon) -> pd.Series`
>   calcule VRP_t = σ_IV_ATM(t, T) − σ_RV_réalisé(t → t+T). Vraie RV
>   sur fenêtre forward ex-post, pas de proxy.
> - `detect_regime(features_t) -> regime_label` utilisant clustering
>   GMM 3 composants sur features
>   `[vol_of_vol, vol_level, term_slope]`. Régimes =
>   {calme, stressé, pré-événement}.
> - `vrp_by_regime(vrp_series, regime_series) -> dict[regime, dict[tenor, (mean, std)]]`
>   retourne moyenne et std du VRP conditionnel.
> - `predict_vrp(current_features, tenor) -> float` utilise le régime
>   courant.
>
> Tests : sur EUR/USD, le VRP calme 1M doit tomber entre 0.3% et 1.5%
> ann (literature check : Bollerslev-Tauchen-Zhou 2009).

### P1.2 — Remplacer RP constant par VRP conditionnel

> Dans `services/vol/engine.py`, remplace la table RP constante par un
> appel à `predict_vrp(features_t, tenor)`. Garde l'ancienne table comme
> fallback si le VRP model n'est pas calibré (< 6 mois d'historique).
> Ajoute un log WARNING si fallback activé.

### P1.3 — Remplacer GARCH(1,1) par HAR-RV

> Crée `core/vol/har_rv.py` implémentant le modèle HAR de Corsi (2009) :
>
> ```
> RV_{t+1} = c + β_d · RV^{(d)}_t + β_w · RV^{(w)}_t + β_m · RV^{(m)}_t + ε_t
> ```
>
> où les RV composantes sont daily (1d), weekly (5d), monthly (22d).
> Estimation OLS, projection forward multi-step pour obtenir σ_HAR(T) à
> chaque tenor.
>
> Remplace l'appel GARCH dans `services/vol/engine.py` par HAR-RV. Garde
> le code GARCH comme module alternatif sélectionnable via config
> `vol_model: 'har' | 'garch'`.
>
> HAR devrait battre GARCH sur horizons mixtes 1M-6M d'après Corsi (2009)
> et Andersen et al. (2007).

---

## Phase 2 — Passer de σ_ATM à la surface complète

### P2.1 — SVI fit historique + stockage paramètres

> Actuellement SVI est calculé à la demande dans
> `api/services/vol_service.py`. Refactor :
>
> - Dans vol-engine, fit SVI paramétrique (a, b, ρ, m, σ) pour chaque
>   tenor à chaque cycle (30s)
> - Stocke les 5 paramètres dans une nouvelle table Postgres
>   `svi_params (timestamp, underlying, tenor, a, b, rho, m, sigma, rmse_fit)`
> - Garde le fit au query pour compatibilité API, mais lis en priorité
>   la table `svi_params` si disponible
>
> Sanity check : butterfly arbitrage condition g(k) ≥ 0 sur grille fine
> après fit. Si violation, log WARNING avec tenor + min(g). Pas de
> blocage ni de re-projection pour l'instant.

### P2.2 — SSVI surface-level fit

> Crée `core/vol/ssvi.py` implémentant SSVI (Gatheral-Jacquier 2014) :
>
> ```
> w(k, θ_t) = (θ_t / 2) · (1 + ρ φ(θ_t) k + √((φ(θ_t) k + ρ)² + (1-ρ²)))
> ```
>
> avec `φ(θ) = η θ^(-γ)` et `θ_t = σ²_ATM(t) · t`.
>
> Fonction `fit_ssvi(surface_multi_tenor) -> (eta, gamma, rho)` :
> 3 paramètres pour toute la surface, garantit no-arb calendar. Stocke
> dans table `ssvi_params`.
>
> Compare qualité de fit SSVI global vs SVI tenor-by-tenor sur RMSE. Si
> écart < 20%, SSVI est préférable (moins de risque overfit, no-arb
> garanti).

### P2.3 — Fair smile historique

> Crée `core/vol/fair_smile.py` :
>
> - Charge historique des paramètres SVI par tenor depuis Postgres
> - Calcule moyenne mobile EWMA de chaque paramètre (λ configurable)
> - `fair_smile_params(tenor, t) -> (a_fair, b_fair, rho_fair, m_fair, sigma_fair)`
>   retourne le smile fair courant
> - `fair_iv(K, tenor, t) -> iv_fair` évalue le smile fair à n'importe
>   quel strike
>
> **Signal étendu** : au lieu de 1 scalar par tenor (σ_fair − σ_mid),
> génère 5 scalars par tenor (un par paramètre SVI). Chaque paramètre a
> sa propre distribution historique et son propre threshold. Un level
> cheap, un skew cher, une convexity fair → 2 trades orthogonaux.
>
> Ajoute à la table `signals` les colonnes : `level_signal, skew_signal,
> smile_signal, atm_shift_signal, convex_signal` (chacun z-score
> normalisé).

---

## Phase 3 — Réduction de dimensionnalité et covariance

### P3.1 — PCA sur surface IV historique

> Crée `core/vol/surface_pca.py` :
>
> - Construit vecteur 30D par snapshot : 6 tenors × 5 piliers
>   (10P, 25P, ATM, 25C, 10C)
> - Fit PCA incrémental sur historique 1 an minimum
> - Identifie les 3 premiers PCs et leur variance expliquée (attendu
>   ~85-95% cumulé)
> - Label manuel les PCs : PC1 ≈ level, PC2 ≈ term slope, PC3 ≈
>   smile/skew (à vérifier empiriquement sur loadings)
> - `project_surface(surface_t) -> (pc1_score, pc2_score, pc3_score)`
> - `surface_from_pcs(scores) -> reconstructed_surface`
>
> Stocke loadings dans `pca_loadings` table. Re-fit mensuel (cron).

### P3.2 — Signal sur facteurs PCA, pas sur tenors

> Remplace la logique de signal ternaire par tenor dans
> `services/vol/engine.py` par :
>
> - Calcul score PC1/PC2/PC3 du snapshot courant
> - Calcul distribution historique de chaque PC (mean, std, rolling
>   3 mois)
> - Signal : z-score de chaque PC. Si |z| > 1.5, signal actif sur ce
>   facteur.
> - 3 signaux maximum (vs 6 avant), mutuellement orthogonaux par
>   construction PCA.
>
> Mapping signal → trade structure :
>
> - PC1 cheap → long straddle portfolio (equi-weighted across tenors)
> - PC2 cheap (short tenors cheap relative to long) → long-short
>   calendar spread
> - PC3 cheap → smile/skew structure (risk reversal ou butterfly selon
>   loading sign)

---

## Phase 4 — Calibration des hyperparamètres

### P4.1 — Calibration walk-forward W₁ (si toujours pertinent post-P1)

> Si après P1.2 on garde encore la combinaison convexe
> σ_fair = W₁·A + (1−W₁)·G : implémente la calibration closed-form dans
> `core/vol/calibration.py` :
>
> ```
> W₁* = Σ (A_t − G_t)(R_{t+τ} − G_t) / Σ (A_t − G_t)²
> ```
>
> Walk-forward : W₁ re-calibré mensuellement sur 12 mois glissants. Clip
> à [0, 1].
>
> **Note** : si P1.2 a remplacé RP par VRP conditionnel et HAR-RV fait
> déjà le job de forecast, W₁ peut devenir redondant. Mesure-le : si
> `|Anchor − σ_HAR| < 0.1%` en moyenne, supprime la combinaison, garde
> un seul estimateur.

### P4.2 — Calibration VRP model

> Le module VRP de P1.1 a besoin de validation OOS :
>
> - Split historique en 70% calibration / 30% holdout
> - Calibre `predict_vrp` sur 70%
> - Évalue MAE sur 30% : VRP prédit vs VRP réalisé ex-post
> - Baseline : VRP constant par tenor (= ancien RP). Le modèle
>   conditionnel doit battre la constante de >20% en MAE, sinon la
>   complexité supplémentaire n'est pas justifiée.
>
> Si échec : fallback automatique sur le constant model, log l'événement.

---

## Phase 5 — Structures de trade adaptées

### P5.1 — Exécuteur multi-structure

> Crée `services/execution/structures.py` avec factory pattern pour
> structures standard :
>
> - `StraddleATM(tenor)` : long/short straddle delta-hedgé
> - `RiskReversal25d(tenor, direction)` : long 25dc / short 25dp (ou
>   inverse)
> - `Butterfly25d(tenor)` : long 25dc + long 25dp - 2×ATM
> - `CalendarSpread(tenor_near, tenor_far)` : short ATM near, long ATM far
>
> Chaque structure expose :
>
> - `compute_greeks(surface) -> dict`
> - `build_orders(ib, F, surface) -> list[Order]`
> - `pnl_decomp(entry, exit) -> dict[source, pnl]`
>
> Fonction `signal_to_structure(signal) -> Structure` mappe le signal
> sur le bon instrument :
>
> - PC1 → StraddleATM
> - PC2 → CalendarSpread
> - PC3 skew → RiskReversal
> - PC3 convexity → Butterfly

### P5.2 — Delta hedge dynamique

> Actuellement : delta-hedge statique à l'entrée. Upgrade : delta-hedge
> rebalance conditionnel.
>
> Crée `services/execution/delta_hedger.py` avec :
>
> - Mode `static` : hedge à l'entrée seulement (comportement actuel)
> - Mode `threshold` : rebalance si `|delta_net| > threshold` (défaut
>   0.05 par contrat)
> - Mode `scheduled` : rebalance toutes les N minutes
>
> Ajoute tracking du P&L de hedging séparément du P&L des options.

---

## Phase 6 — Refonte frontend : panels opérationnels

Le frontend actuel (Term Structure, Smile, Vol Scanner) est un dashboard
passif. Refonte en **cockpit de trading** avec 6 panels organisés
hiérarchiquement : macro-contexte → signaux → décision → exécution →
suivi → diagnostic.

### P6.1 — Panel 1 : Regime Detector

> Nouveau composant React `RegimeDetectorPanel.tsx` en haut de la vue
> principale (toujours visible, sticky). Affiche :
>
> - **Régime courant** : label grand format (CALME / STRESSÉ / PRÉ-ÉVÉNEMENT)
>   avec code couleur (teal / amber / red)
> - **Probabilités GMM** : 3 barres horizontales empilées sommant à 100%
> - **Features live** : 3 métriques `vol_of_vol`, `vol_level`, `term_slope`
>   avec leur z-score vs historique 3 mois
> - **Prochain événement macro** : calendar feed ECB/FOMC/NFP avec
>   countdown (jours + heures)
> - **VRP attendu** : table 6 tenors × VRP prédit pour le régime courant
> - **Flag event_dampener** : badge rouge "SIZING HALVED" si actif
>
> Backend : endpoint `GET /api/v1/vol/regime` (nouveau).
> Source : `core/vol/vrp.py::detect_regime` + `core/vol/vrp.py::predict_vrp`.
> Refresh : 30s (même cycle que vol-engine).

### P6.2 — Panel 2 : PCA Signal Dashboard

> Nouveau composant `PCASignalPanel.tsx` remplaçant l'ancien Vol Scanner.
> Layout 3 colonnes :
>
> Colonne 1 — **PC1 (Level)** :
> - Gauge horizontale z-score [-3, +3] avec zones colorées
>   (rouge |z|>2, orange 1.5<|z|<2, vert |z|<1.5)
> - Signal status : CHEAP / FAIR / EXPENSIVE
> - Historique PC1 score sur 3 mois (sparkline)
> - Structure recommandée : "Straddle ATM 3M"
>
> Colonne 2 — **PC2 (Term slope)** :
> - Même format
> - Structure recommandée : "Calendar 1M/3M"
>
> Colonne 3 — **PC3 (Smile)** :
> - Même format + breakdown sub-signaux (skew z-score, convex z-score)
> - Structure recommandée : "Risk Reversal 25d" ou "Butterfly 25d"
>   selon le sub-signal dominant
>
> Bouton **"Arm trade"** sous chaque colonne → navigue vers Panel 3
> avec paramètres pré-remplis.
>
> Backend : endpoint `GET /api/v1/vol/pca-signals`.
> Source : `core/vol/surface_pca.py::project_surface` +
> z-score vs distribution historique stockée en Redis
> (`pca_rolling_stats:3M`).

### P6.3 — Panel 3 : Trade Preview

> Nouveau composant `TradePreviewPanel.tsx`, modal ou full-page
> triggered by "Arm trade" button. Affiche :
>
> **Section A — Structure détaillée** :
> Table des legs avec colonnes : Leg | Contract | Strike | DTE | Qty |
> Side (BUY/SELL) | IV mid | Vega | Gamma | Theta | Delta
>
> **Section B — Greeks net** :
> Card avec vega total, gamma total, theta total (daily), delta total
> (pre-hedge), delta net (post-hedge sur EUR future)
>
> **Section C — Pricing** :
> Premium payé/reçu, breakeven move spot, maximum loss/gain, ratio
> edge/risk
>
> **Section D — P&L decomposition forecast** :
> Simulation sur 3 scenarios :
> - Scenario A (thèse correcte) : IV reprice, RV match model
> - Scenario B (neutral) : nothing moves
> - Scenario C (adverse) : IV diverge, RV contrarian
>
> **Section E — Sizing** :
> Recommended quantity basée sur :
> - Base size depuis config
> - Multiplier par |z-score| (plus conviction = plus de taille)
> - Divider par book concentration (depuis P0.2)
> - Divider par 2 si `event_dampener` actif
>
> Bouton "Submit to execution queue" en bas (grisé jusqu'à ce que
> toutes les checks passent).
>
> Backend : endpoint `POST /api/v1/trade/preview` (body = signal + tenor + structure).
> Source : `services/execution/structures.py` + Black-Scholes pricing
> local (ne pas toucher IB à ce stade).

### P6.4 — Panel 4 : Active Positions Monitor

> Nouveau composant `PositionsMonitorPanel.tsx` remplaçant le
> BookPanel actuel. Focus trading-centric, pas accounting :
>
> **Section A — Open structures** (table) :
> Structure ID | Entry date | Days to expiry | Entry signal | Current
> signal | P&L $ | P&L vega | P&L gamma/theta | P&L alpha | Action
>
> La colonne "Current signal" indique si le signal d'entrée est
> toujours valide. Si flipped, badge rouge "EXIT TRIGGERED".
>
> **Section B — Aggregate Greeks** :
> Total vega par tenor (bar chart horizontal), total gamma,
> total theta (daily bleed), total delta.
>
> **Section C — Delta hedge status** :
> Current delta imbalance, hedge threshold, next rebalance trigger
> (if mode=threshold), history of recent hedge trades (last 20).
>
> **Section D — Exit alerts** :
> Liste des positions à sortir selon R5 du guide utilisateur :
> - Signal flipped
> - 50% du time to expiry écoulé
> - Stop loss vega atteint
>
> Backend : endpoint `GET /api/v1/positions/vol-structures`.
> Source : jointure entre positions IB et `signals` historique.

### P6.5 — Panel 5 : Surface Diagnostic

> Refonte du panel "Smile" actuel en outil diagnostic plus complet.
> Remplace `SmilePanel.tsx` par `SurfaceDiagnosticPanel.tsx`.
>
> **Tab 1 — Live Smile** (par tenor sélectionnable) :
> - Points observés (5 pillars)
> - SVI fit courant (courbe solide)
> - SVI fit fair (courbe pointillée, = moyenne EWMA historique des params)
> - Shaded area : bande ±1σ historique autour du fair smile
> - Highlighted points : piliers hors bande (opportunités ou outliers)
>
> **Tab 2 — Parameter Dynamics** :
> Time series 3 mois pour chaque paramètre SVI (a, b, ρ, m, σ) par
> tenor. Identifie visuellement les régimes et les outliers de fit.
>
> **Tab 3 — Surface Heatmap** :
> Heatmap 2D tenor × delta pillar, couleur = z-score IV vs fair smile.
> Vue synoptique : où sont les opportunités sur toute la surface ?
>
> **Tab 4 — No-arb Health** :
> - Butterfly check g(k) ≥ 0 : red cell si violation
> - Calendar check ∂w/∂T ≥ 0 : red cell si violation
> - SSVI fit RMSE vs SVI par tenor
> - Last violation events log (last 50)
>
> Backend : endpoints existants + nouveau
> `GET /api/v1/vol/surface-diagnostic/{tenor}`.

### P6.6 — Panel 6 : Model Health

> Nouveau composant `ModelHealthPanel.tsx` dans un onglet séparé
> "Diagnostic" (pas main view, pour debug + audit).
>
> **Section A — VRP Validation** :
> Scatter plot VRP prédit vs VRP réalisé ex-post. R², MAE, bias affichés.
> Ligne y=x pour référence. Points coloriés par régime.
>
> **Section B — Signal/Residual Health** :
> - Autocorrélation du résidu σ_mid − σ_fair lag 1 à 20 (bar chart)
> - Distribution des z-scores PC1/PC2/PC3 (histogramme, doit ressembler
>   à N(0,1) si bien calibré)
> - Cumulative signal count par type sur 3 mois
>
> **Section C — PCA Health** :
> - Variance expliquée par PC (scree plot)
> - Loadings des 3 premiers PCs (heatmap 30×3)
> - Rolling stability : variance des loadings sur les 3 derniers fits
>   mensuels (si trop instable → PCA pas fiable)
>
> **Section D — Data Quality** :
> - Nombre de piliers valides par tenor (24h)
> - Taux de validation failure (BF25 négatif, etc.)
> - Latency end-to-end (IB tick → signal output)
> - Heartbeats des 3 services (market-data, vol-engine, db-writer)
>
> Backend : endpoint `GET /api/v1/vol/model-health`.

---

## Ordre d'exécution recommandé

```
P0.1 → P0.2 → P0.3              (nettoyage, 1 jour)
  ↓
P1.1 → P1.2 → P1.3              (VRP + HAR-RV, 4 jours)
  ↓
P2.1 → P2.2 → P2.3              (surface, 1 semaine)
  ↓
P3.1 → P3.2                     (PCA signal, 3 jours)
  ↓
P4.1 → P4.2                     (calibration, 3 jours)
  ↓
P5.1 → P5.2                     (execution, 1 semaine)
  ↓
P6.1 → P6.2 → P6.3              (frontend core, 1 semaine)
  ↓
P6.4 → P6.5 → P6.6              (frontend advanced, 1 semaine)
```

---

## Ce que ce plan ne couvre pas (volontairement)

- **Day-weights framework** : pas critique tant que tu ne trades pas
  < 1M. À considérer seulement si extension vers short-dated.
- **Stochastic volatility Heston/SABR** : trop lourd pour buy-side,
  overkill vs SSVI.
- **Jump diffusion** : pertinent seulement si short-dated pré-event.
- **ML deep learning** : pas de signal clair avant 3-5 ans de données
  intraday propres.

## Limitations à garder visibles

- Pas d'exécution bid/ask individuelle (mids seulement) → estimation
  des coûts d'exécution approximative, sous-estime probablement le
  vrai coût de 10-30%
- Historique DB probablement < 1 an → PCA et VRP conditionnel fragiles
  avant 12+ mois de données
- Pas de detector explicite de news/event jumps → sous-performance
  probable aux FOMC/ECB/NFP days
- Continuous future OHLC introduit du bruit aux rolls → HAR-RV hérite
  de ce biais tant que tu ne nettoies pas
