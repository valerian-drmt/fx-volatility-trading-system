# Vol-system — Doc/code discrepancy report

> Audit comparant `VOL_TRADING_USER_GUIDE.md` (intent utilisateur, source : design product)
> vs `VOL_ENGINE_REFERENCE.md` (état du code actuel, source : généré par Claude Code).
>
> Objectif : pour chaque divergence, donner à l'agent code l'information
> exacte nécessaire pour soit (a) implémenter ce qui manque, soit (b) modifier
> le user guide pour qu'il reflète l'état réel, soit (c) flagger explicitement
> un panel comme MOCK dans le frontend.
>
> **Règle de lecture** : criticité décroissante (C1 = bloquant, C5 = cosmétique).
> Chaque écart suit le bloc : EXPECTED / ACTUAL / IMPACT / ACTION.
>
> Audience : agent code (Claude Code) qui doit produire des PR.

---

## TL;DR pour l'agent code

Le `user guide` décrit **6 panels** d'un cockpit de trading.
Le `engine reference` décrit **un moteur qui génère un sous-ensemble strict** des inputs nécessaires à ces panels.

**Panels supportés par le moteur actuel (au moins partiellement)** :
- Panel 5 (Surface Diagnostic) — supporté à ~70%, cf. C2.4
- Panel 6 D (Data Quality) — supporté via heartbeat/timestamps

**Panels NON supportés par le moteur actuel** :
- Panel 1 (Regime Detector) — heuristique 3-states existe ; GMM probabilités, event_dampener, prochain événement = **non implémentés**
- Panel 2 (PCA Signal Dashboard) — **0% implémenté**. Aucun fit PCA dans `core/vol/`. Le moteur produit `signals[]` (CHEAP/FAIR/EXPENSIVE par tenor), pas des z-scores PC1/PC2/PC3.
- Panel 3 (Trade Preview) — pricing leg-by-leg, greeks aggregation, scenarios, sizing : **0% implémenté côté engine**. Pas de pricer multi-leg, pas de scenario engine, pas de sizing pipeline.
- Panel 4 (Active Positions) — monitoring positions, exit alerts, delta hedge status : **0% implémenté côté vol-engine**. Probablement à charge de `risk-engine` ou `execution-engine` non documentés ici.
- Panel 6 A/B/C (VRP validation, signal/residual health, PCA stability) — **0% implémenté**.

**Conséquence opérationnelle** : un développeur frontend qui lit le user guide construit
des panels qui s'attendent à des données absentes. Risque de soit (a) frontend qui crash
sur clés JSON manquantes, soit (b) frontend qui affiche des données fabriquées (mock
hardcodé) que l'utilisateur croit réelles → perte de confiance dans le cockpit, ou pire,
trading sur fausses informations.

---

## C1 — Discrepancies bloquantes (système ne peut pas fonctionner comme décrit)

### C1.1 — Panel 2 PCA Signal Dashboard : panel central inexistant côté code

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 2) :
- 3 facteurs PCA orthogonaux (PC1=level, PC2=slope, PC3=smile)
- z-scores temps réel par PC, sub-signals pour PC3 (skew, convex)
- "Recommended structure" par PC (Straddle ATM 3M, Long BF25 3M, Calendar, etc.)
- Time series 3 mois des z-scores
- Bouton "Arm trade" qui pré-remplit Panel 3
- Règle de cohérence : ne pas trader si 2 signaux contradictoires
- Workflow user : **panel central**, le seul qui déclenche un trade

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.7) :
- Le moteur produit `signals[]` avec champs : `{tenor, sigma_mid, sigma_fair, ecart, signal_type ∈ {CHEAP, FAIR, EXPENSIVE}}`
- Comparaison **par tenor**, pas en espace PCA
- Aucun fit PCA mentionné dans `core/vol/` ; aucune table `pca_loadings` ou `pca_scores` dans la liste Postgres
- Aucune notion de "recommended structure" dans le payload

**IMPACT** :
- Le panel 2 ne peut pas être affiché tant que le PCA n'est pas implémenté.
- Le workflow décrit dans le guide (§"Workflow type — journée de trading", étapes 7-10) référence "|z| franchit 1.5 sur PC1/PC2/PC3" — n'est exécutable.
- Toute UI panel 2 doit nécessairement mock les valeurs PC × z-score.

**ACTION pour Claude Code** :
1. Décider explicitement entre les 2 options :
   - **Option A : Implémenter le PCA factor model**
     - Nouveau module `core/vol/pca_factors.py`
     - Inputs : historique des surfaces (table `vol_surfaces`, surface_data JSONB)
     - Construction matrice (T × 30) où colonnes = (6 tenors × 5 deltas) IV ATM-normalisée
     - PCA fit incrémental (sklearn IncrementalPCA) sur fenêtre rolling (proposer : 3-6 mois)
     - Stocker loadings dans nouvelle table `pca_state` (refit hebdomadaire)
     - À chaque cycle : projeter snapshot courant sur loadings → 3 z-scores
     - Stocker dans nouvelle table `pca_signals(timestamp, pc_id, z_score, structure_recommended)`
     - Mapping z-score → structure : règles à définir explicitement avec utilisateur (placeholder : PC1+ → straddle ATM, PC1- → strangle short, etc.)
     - Estimation effort : 2-3 semaines
   - **Option B : Réécrire user guide pour matcher le système actuel par-tenor**
     - Remplacer Panel 2 par "Per-tenor signals dashboard"
     - 6 colonnes au lieu de 3, une par tenor
     - Z-score = ecart / σ_ecart_rolling(tenor) à calculer (trivial, fenêtre 30 jours)
     - Plus simple à shipper, perd la décomposition factorielle (mais mieux que rien)
     - Estimation effort : 3-5 jours
2. Documenter le choix dans `docs/DECISIONS.md` avec date et raison.
3. Tant que non décidé : flagger Panel 2 comme `STATUS: MOCK — DO NOT TRADE` dans le frontend, surimpression rouge plein écran sur le panel.

---

### C1.2 — Panel 3 Trade Preview : pricing multi-leg + sizing pipeline absents

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 3) :
- Pour une structure recommandée (straddle, butterfly, calendar) :
  - Section A : legs détaillées (contract, strike, DTE, qty, side, IV)
  - Section B : greeks nets (vega, gamma, theta, delta) — calculés sur agrégation des legs
  - Section C : pricing (premium total, breakeven spot, max loss, vega edge attendu)
  - Section D : scenarios — table 3 colonnes (P&L pour spot move × IV reprice)
  - Section E : sizing avec formule explicite `final_qty = base × |z|/threshold × book_penalty × event_dampener`
- Bouton Submit avec 5 checks bloquants (régime, signal actif, max loss < 2% capital, vega limit book, IV freshness)

**ACTUAL** (`VOL_ENGINE_REFERENCE.md`) :
- `core/pricing/bs.py` mentionné mais pas détaillé — vraisemblablement un BS single-option pricer
- Aucun module `core/structures/` listé
- Aucun scenario engine mentionné
- Aucune logique de sizing dans `core/vol/`
- Pas de notion de "book state" (vega courant agrégé, delta courant) dans le payload `latest_vol_surface`
- Pas d'entité "structure" (groupe de legs) modélisée

**IMPACT** :
- Panel 3 entièrement non-fonctionnel.
- Le bouton "Arm trade" du Panel 2 (lui-même mock, cf. C1.1) n'a aucune cible.
- Aucun trade ne peut être armé / soumis.

**ACTION pour Claude Code** :
1. Définir entité `Structure` :
   ```python
   # src/core/structures/structure.py
   @dataclass
   class Leg:
       contract_type: Literal["call", "put", "future"]
       strike: float | None  # None pour future
       dte: int
       qty: int
       side: Literal["BUY", "SELL"]

   @dataclass
   class Structure:
       structure_type: Literal["straddle", "strangle", "butterfly", "calendar", "risk_reversal"]
       legs: list[Leg]
       reference_tenor: str
       signal_id: str  # foreign key vers pca_signals ou per_tenor_signals
   ```
2. Implémenter constructeurs canoniques (factory) :
   - `Structure.straddle_atm(tenor, qty, atm_strike)` → 2 legs
   - `Structure.butterfly_25d(tenor, qty, strikes)` → 4 legs
   - `Structure.calendar(tenor_short, tenor_long, strike, qty)` → 2 legs
   - etc.
3. Implémenter `core/pricing/structure_pricer.py` :
   - Input : Structure + surface SVI fittée
   - Output : `{premium, greeks_net: {vega, gamma, theta, delta}, max_loss, breakeven}`
   - Greeks calculés analytiquement (BS dérivées) ou par bump-and-revalue
4. Implémenter `core/scenarios/scenario_engine.py` :
   - Input : Structure + surface courante + scenarios définis
   - Scenarios par défaut : (spot ±2%, ±0%, ±0.5%) × (IV ±1%, 0, ∓1%)
   - Output : matrice P&L décomposée (vega P&L, gamma P&L, theta P&L)
5. Implémenter `core/sizing/sizer.py` :
   - Inputs : signal strength, structure, book state (à fournir par risk-engine), config
   - Formule textuelle du guide → fonction Python testée unitairement
6. Endpoint API `/api/v1/trade/preview` qui orchestre 1+3+4+5
7. Estimation totale : 4-6 semaines.

---

### C1.3 — Panel 4 Active Positions : suivi de positions absent

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 4) :
- Liste des structures ouvertes (T01, T02, T03 dans le mockup)
- Pour chacune : DTE, signal d'entrée, signal courant, P&L mark-to-market, vega résiduel
- Aggregate greeks (vega par tenor, gamma total, theta total, net delta)
- Delta hedge status (current imbalance, rebalance trigger, last hedge ago)
- Exit alerts si signal flipped, time-based, stop-loss vega, time to expiry < 7j

**ACTUAL** (`VOL_ENGINE_REFERENCE.md`) :
- Aucune table `positions` ou `structures_open` dans la liste Postgres §3
- Aucun module `core/positions/` mentionné
- `vol-engine` ne traite que le pricing théorique, pas l'état du book
- Note dans le doc utilisateur §Panel 4 : référence à `risk-engine` et `execution-engine` qui ne sont pas documentés dans `VOL_ENGINE_REFERENCE.md`

**IMPACT** :
- Panel 4 entièrement non-fonctionnel.
- Pas d'exit alerts possibles → règle critique de sortie systématique du guide §Panel 4 ("indépendamment du signal, sortie auto si...") inapplicable.
- Risque opérationnel majeur si trades effectués manuellement sans monitoring auto.

**ACTION pour Claude Code** :
1. Clarifier perimeter : Panel 4 dépend de `risk-engine` + `execution-engine`. Ces services existent-ils ? Demander à l'utilisateur leur état.
2. Si non : créer un service `position-tracker` minimal :
   - Souscrit aux fills IB via `execution-engine` (à créer ou vérifier existence)
   - Maintient table `positions(id, structure_id, leg_idx, contract, qty_open, entry_price, entry_signal_id, entry_timestamp)`
   - Recalcule à chaque cycle vol : signal_id_current, mtm_pnl, greeks_residual
   - Génère exit_alerts via 4 règles du guide §Panel 4 ("Règles de sortie systématique")
3. Endpoint `/api/v1/positions` + WS `position_update`
4. Estimation : 3-4 semaines (dépend de l'état execution-engine).

---

### C1.4 — Panel 1 GMM regime probabilities : heuristique vs modèle probabiliste

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 1) :
- Affichage : "Probabilités GMM : 78% CALME / 18% STRESSÉ / 4% PRÉ-ÉVÉNEMENT"
- Implique un Gaussian Mixture Model fitté sur features historiques
- Implique 3 composantes calibrées (means, covariances, weights)

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.4) :
- `detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct) → enum`
- C'est une fonction déterministe à seuils (heuristique)
- Aucun fit GMM mentionné, aucune table `regime_model_state`
- Output = label seul, pas une distribution de probabilités

**IMPACT** :
- Frontend Panel 1 ne peut pas afficher les probabilités → soit fabriquées, soit inexistantes.
- L'utilisateur prend des décisions (sizing × 0.7 si stressé, NO TRADE si pre_event) basées sur la sortie binaire d'une heuristique non calibrée.

**ACTION pour Claude Code** :
1. **Court terme (1-2 jours)** : flagger l'heuristique comme telle dans le payload :
   - Renommer `detect_regime` en `detect_regime_heuristic`
   - Ajouter dans le payload : `_regime = {label: "calm", method: "threshold_heuristic", probabilities: null}`
   - Modifier frontend Panel 1 : si `probabilities is null`, ne pas afficher la barre 78%/18%/4% — afficher uniquement le label avec mention "(heuristic)"
2. **Moyen terme (1-2 semaines)** : implémenter vrai GMM
   - Module `core/vol/regime_gmm.py`
   - Features : (vol_level, vol_of_vol_30d, term_slope_3M_1M, log_volume_ratio_5d_30d)
   - Fit `sklearn.mixture.GaussianMixture(n_components=3)` sur historique 2-5 ans
   - Persistence du modèle (joblib) avec versioning
   - Refit mensuel automatisé
   - Output : `predict_proba()` → array shape (3,)
   - Validation : matrice de transition empirique vs prédite, persistence des labels
3. Ajouter Panel 6 section dédiée à GMM diagnostics (log-likelihood OOS, BIC, transition matrix).

---

### C1.5 — VRP table hardcodée vs estimation

**EXPECTED implicite** (`VOL_TRADING_USER_GUIDE.md` §Panel 6 A) :
- "VRP Validation : scatter plot VRP prédit vs réalisé"
- Implique que VRP est estimé d'un côté (prédit), mesuré de l'autre (réalisé)
- Implique un processus de calibration

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.4) :
- Table 6×3 hardcodée dans `core/vol/vrp.py`
- Valeurs par défaut : (1M-6M) × (calm, stressed, pre_event)
- "hardcoded (P1.2 du refactor pas encore branché à `VolTradingConfig.signal.vrp_regime_override`)"
- Aucune estimation rolling, aucune mesure réalisée

**IMPACT** :
- Le terme **central** de la stratégie (transformation P→Q via VRP) est posé arbitrairement.
- Si la table biaise +0.3 vol pt sur tous les régimes → biais long vol systématique.
- Panel 6 A non-implémentable sans données "réalisées" (qui nécessitent de mesurer ex-post `IV_t - RV_{t,t+τ}` pour chaque tenor τ).
- Toute la chaîne `_fair_q → signals → trades` hérite du biais.

**ACTION pour Claude Code** :
1. **Court terme (3-5 jours)** : instrumenter la mesure ex-post
   - Pour chaque tenor τ ∈ {30, 60, 90, 120, 150, 180} jours :
     - À t-τ : enregistrer `iv_atm_t-τ`
     - À t : calculer `rv_realized_{t-τ, t}` (Yang-Zhang sur la fenêtre)
     - Stocker `vrp_realized = iv_atm_{t-τ}² - rv_realized_{t-τ,t}²` (variance VRP, convertir en vol pts si voulu)
   - Nouvelle table `vrp_realized(timestamp, tenor, regime_at_entry, vrp_realized_vol_pts)`
   - Backfill possible si historique IV + spot dispo
2. **Moyen terme (2-3 semaines)** : VRP estimator
   - Module `core/vol/vrp_estimator.py`
   - Modèle : `vrp_t = α + β·vol_level_t + γ·vol_of_vol_t + δ·term_slope_t + ε`
   - Régression rolling sur fenêtre 12-24 mois, par tenor
   - OOS validation walk-forward
   - Output : `predict(features) → vrp_pts` qui remplace la lookup table
3. Brancher dans `_fair_q` step du cycle.
4. Implémenter Panel 6 A (scatter prédit vs réalisé) une fois (1) et (2) en place.

---

## C2 — Discrepancies modérées (système fonctionne mais inexact)

### C2.1 — Cycle 30s annoncé en frontend vs 180s réel

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md`) :
- §Panel 4 : "Checks à chaque cycle 30s"
- §Limitations : "le régime est recalculé toutes les 30s"

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §1) :
- "vol-engine tourne en boucle infinie avec un cycle nominal de **180s**"

**IMPACT** :
- Faux sentiment de réactivité chez l'utilisateur.
- Si l'utilisateur attend qu'un signal change "dans 30s" et qu'il met 3 minutes, il peut prendre une décision sous fausse hypothèse.

**ACTION** :
- Soit modifier le user guide pour écrire 180s partout (trivial, 5 minutes).
- Soit décider de réduire la fréquence (cf. §9 du engine reference, 180s = bottleneck IB chain scan, sub-180s nécessite refonte du chain fetcher avec scans qui se chevauchent + cache strikes — effort 1-2 semaines).
- **Recommandation court terme** : aligner doc → 180s.

---

### C2.2 — Section A "Legs" du Panel 3 utilise contracts CME (EUU, 6E future)

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 3 Section A) :
- Mockup montre `EUU C Jul`, `EUU P Jul`, `6E future`
- Implique trading FOP CME (EUU = options sur 6E)

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §2) :
- IB chain fetched via `discover_chains()` sur EURUSD
- `reqHistoricalDataAsync(CONTFUT EUR, ...)` → CONTFUT EUR = continuous EUR future (oui, 6E)
- Le sous-jacent options n'est pas explicité — peut être EURUSD spot OTC ou EUU options sur 6E

**IMPACT** :
- Ambiguïté dangereuse : le pricing OTC vs CME utilise des conventions différentes (forward, smile reference, settlement).
- Si le moteur fit la surface en assumant spot et que les contracts tradés sont sur futures, l'écart forward-spot crée un biais.

**ACTION** :
- Documenter explicitement dans `VOL_ENGINE_REFERENCE.md` §2 le contract type exact :
  - Symbole IB (EUU vs EURUSD vs autre)
  - Sous-jacent (spot OTC, future 6E continuous, future expiry-specific)
  - Convention forward (interest rate parity ou direct fwd quote IB)
- Vérifier cohérence pricing : si options sur futures, utiliser BS-Black76 (F au lieu de S, pas de discount cash carry sur underlying), sinon Garman-Kohlhagen pour FX.
- Effort : 1 jour audit + correction si nécessaire.

---

### C2.3 — Convention VRP : vol points vs variance points

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 1) :
- "VRP attendu (calme) : 1M=+0.4% | 3M=+0.6% | 6M=+0.9%"
- Interprétation : vol points (delta de IV)

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.4) :
- Table en "vol points"
- Formule : `σ_fair^Q = σ_fair^P + VRP(tenor, regime)` — addition en vol pts

**IMPACT** :
- Standard académique : VRP s'exprime en **variance** (σ²_Q - σ²_P), pas en vol.
- Conversion vol-additive ≈ variance-additive **uniquement localement** autour du niveau courant. Erreur d'ordre 2 quand vol bouge significativement.
- Exemple numérique : VRP_var = 1 (var pts²), si σ_P = 5% alors σ_Q ≈ √(0.0025+0.0001) ≈ 5.1%, donc VRP_vol = 0.1. Si σ_P = 15%, alors σ_Q ≈ √(0.0225+0.0001) ≈ 15.03%, donc VRP_vol = 0.03 — pas 0.1.
- La table actuelle suppose VRP_vol constant → faux quand le niveau de vol bouge.

**ACTION** :
- Décider explicitement : VRP en variance ou en vol ?
- Recommandation : passer à variance (plus rigoureux, plus stable).
- Modifier `_fair_q` step :
  ```python
  variance_p = (sigma_fair_p_pct / 100) ** 2
  variance_q = variance_p + vrp_variance_pts[tenor][regime]
  sigma_fair_q_pct = sqrt(variance_q) * 100
  ```
- Recalibrer table en variance points.
- Mettre à jour user guide en conséquence.
- Effort : 1-2 jours code + recalibration table.

---

### C2.4 — Panel 5 Tab 3 : heatmap z-score vs surface fair-smile

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 5 Tab 3) :
- "Surface Heatmap : vue 2D tenor × delta_pillar, couleur = z-score vs fair"
- Implique un fair smile par tenor × delta_pillar (pas seulement ATM)

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.4 + §8 Tier 4) :
- `_fair_q` calcule fair Q **uniquement à l'ATM** (utilise σ_fair_p qui vient de HAR/GARCH = forecast scalaire par tenor)
- Note explicite §8 Tier 4 : "Heatmap z-score (tenor × delta) — à brancher quand fair-smile P1.4 sera live"
- Donc fair-smile non-ATM = TODO

**IMPACT** :
- Heatmap actuellement non-implémentable.
- L'utilisateur ne peut pas voir si la mispricing est sur l'ATM ou les wings.
- Trade structures qui exploitent le smile (butterflies, risk reversals) ne peuvent pas être validées par diagnostic.

**ACTION** :
1. Implémenter fair-smile (P1.4 mentionné dans le code) :
   - Pour chaque tenor : fair_smile = fair_atm_q + smile_shape
   - Smile shape estimé : moyenne historique du smile (IV(δ) - IV(atm)) sur fenêtre rolling, OU régression sur features (vol_level, term_slope)
   - Output : `_fair_smile[tenor][delta] = sigma_fair_q_pct`
2. Calculer z-score = (sigma_observed - sigma_fair_smile) / σ_residual_rolling[tenor, delta]
3. Brancher dans heatmap Tab 3.
- Effort : 1-2 semaines.

---

### C2.5 — SVI rmse non-informatif (5 obs, 5 params)

**EXPECTED implicite** (`VOL_ENGINE_REFERENCE.md` §4.5) :
- "rmse_fit < 0.003 = bon fit, > 0.01 = smile bruité ou ill-posed"
- Implique que rmse mesure quelque chose

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.5) :
- Input : 5 obs (5 deltas) par tenor
- 5 paramètres SVI (a, b, ρ, m, σ)
- Donc 0 degrés de liberté → fit exact possible → rmse ≈ 0 quasi-toujours

**IMPACT** :
- Le seuil "rmse_fit < 0.003" est cosmétique : la valeur sera toujours faible, ne discrimine pas un bon fit d'un mauvais.
- Le test "RMSE SVI par tenor < 0.003 = bon" du Panel 5 / Panel 6 est non-informatif.

**ACTION** :
- Soit augmenter le nombre d'observations en interpolant des deltas intermédiaires depuis le payload IB (mais on perd l'observable direct), soit accepter que SVI per tenor sert juste à interpoler proprement entre les 5 pillars (et pas à valider la qualité).
- Recommandation : remplacer le check "rmse" par "butterfly_g_min ≥ 0" + "monotonicity check sur le smile interpolé entre pillars".
- Mettre à jour user guide §Panel 5 Tab 4 pour refléter ce check pertinent.
- Effort : 1-2 jours.

---

## C3 — Discrepancies mineures (terminologie / cohérence interne)

### C3.1 — `event_dampener` non spécifié côté code

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md`) :
- §Panel 1 : "event_dampener : OFF/ON" affiché
- §Panel 3 Section E : "× event_dampener × 1.0 (not active)" dans formule sizing
- §Panel 1 : "ON, sizing auto-divisé par 2 sur tous les signaux"

**ACTUAL** (`VOL_ENGINE_REFERENCE.md`) :
- Section `regime` dans `VolTradingConfig` mentionnée comme "phases P1+ refactor — pas wired"
- Aucune logique d'event calendar mentionnée
- Pas de feed externe (ECB calendar, NFP, FOMC) intégré

**ACTION** :
- Implémenter `core/calendar/event_feed.py` qui consomme calendrier macro (source à choisir : ForexFactory scrape, Trading Economics API, ICS feed manuel)
- Logique : `event_dampener = ON if any major_event in next_5_days else OFF`
- Définir liste `major_events = {ECB, FOMC, NFP, BOE, BOJ, GDP_release, CPI_release}`
- Persister dans table `economic_events`
- Brancher dans regime detector et sizer.
- Effort : 1-2 semaines (selon source data).

---

### C3.2 — `vol_of_vol` feature mentionnée mais source non-documentée

**EXPECTED** (`VOL_TRADING_USER_GUIDE.md` §Panel 1) :
- "vol_of_vol = 0.12, z = -0.3" affiché

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §4.4) :
- `detect_regime(vol_level_pct, vol_of_vol_pct, term_slope_pct)` — input attendu mais source non documentée

**ACTION** :
- Documenter calcul exact de `vol_of_vol` :
  - Probable : std(IV_atm_3M) sur fenêtre rolling N jours
  - Alternative : std(IV_atm) cross-tenors
  - Alternative : abs returns of IV_atm
- Spécifier dans `VOL_ENGINE_REFERENCE.md` §4.4
- Effort : audit 0.5 jour + doc 0.5 jour.

---

### C3.3 — Hot-reload partiel : 2 fields sur 8 sections

**EXPECTED** (implicite) :
- Hot-reload via `config:changed` est mentionné comme mécanisme général
- L'utilisateur s'attend à pouvoir tweaker n'importe quel paramètre live

**ACTUAL** (`VOL_ENGINE_REFERENCE.md` §6) :
- Seuls `signal.threshold_vol_pts` et `signal.model_p` sont consommés au runtime
- Tous les autres : "pas wired"

**ACTION** :
- Soit étendre wiring à toutes les sections (effort modéré, ~1-2 semaines)
- Soit documenter explicitement quels fields sont hot-reloadables vs require restart, dans le user guide section "configuration"
- Recommandation : tableau explicite dans user guide.

---

## C4 — Inconsistances de design à clarifier

### C4.1 — Workflow user dit "10 trades/jour possibles", mais cycle 180s + signal stable 2-3 cycles = au mieux 20 trades/jour

**EXPECTED** : pas explicite mais le tone du user guide suggère trading actif.

**ACTUAL** : 180s × 3 cycles minimum pour stabilité = 9 minutes minimum entre signaux ≠ contradictoires. Sur 8h trading window = 53 fenêtres décisionnelles max.

**ACTION** : ajouter section "Capacity & frequency" au user guide qui clarifie ordre de grandeur attendu (ex : 1-5 trades / jour en régime calme, 0 en pre_event, 5-10 en stressed).

---

### C4.2 — "Pas de gestion multi-sous-jacent" (user guide §Limitations) vs absence de design pour extension

**ACTION** : ajouter ADR (Architecture Decision Record) qui documente le coût d'extension à GBPUSD/USDJPY :
- Per-symbol vol-engine instances OU mono-instance multi-symbol ?
- Surface partagée (cross-currency) ou indépendante ?
- Expérience montre : indépendant est plus simple, partagé donne edge cross-currency.
- Si partagé un jour : cf. paper Skiadopoulos sur cross-currency vol surfaces.

---

## C5 — Cohérence interne du user guide

### C5.1 — Panel 1 dit "STOP" si pre_event mais Panel 3 dit "checks bloquent submit si régime = pre_event"

Ces deux sont cohérents et redondants → bien. Aucune action.

### C5.2 — Couleurs : "vert / orange / rouge" pour signal status (Panel 4)

Cohérent dans le doc. Aucune action sauf vérifier accessibilité (daltonisme : ajouter glyphes ●!× en plus de couleurs).

---

## Résumé pour priorisation Claude Code

| ID | Effort | Bloquant pour live trading ? | Recommandation timing |
|---|---|---|---|
| C1.1 (PCA panel) | 2-3 sem (option A) ou 3-5j (option B) | Oui | Décider option A/B avant tout autre travail panel |
| C1.2 (Trade preview) | 4-6 sem | Oui | Après C1.1 |
| C1.3 (Positions) | 3-4 sem | Oui | Parallèle à C1.2 (équipes différentes idéalement) |
| C1.4 (GMM regime) | court 1-2j + long 1-2 sem | Non (heuristique fonctionne) | Court terme = obligatoire (flag mock), long terme = quand priorité |
| C1.5 (VRP estimation) | 3-5j mesure + 2-3 sem estimation | Oui (biais systématique sinon) | À faire AVANT live |
| C2.1 (cycle 30s vs 180s doc) | 5 min | Non | Trivial, faire immédiatement |
| C2.2 (contract clarification) | 1 jour | Oui (risque pricing) | Audit immédiat |
| C2.3 (VRP variance vs vol) | 1-2j | Non (impact ordre 2) | Quand C1.5 fait |
| C2.4 (fair-smile heatmap) | 1-2 sem | Non | Après C1.5 |
| C2.5 (SVI rmse) | 1-2j | Non | Quand temps libre |
| C3.x | divers | Non | Backlog |
| C4.x | clarifications doc | Non | À chaque revue de doc |

---

## Checklist préliminaire avant tout commit / PR

Pour l'agent code, à chaque PR liée à ce système :

- [ ] Le code commit modifie `core/vol/*` ? → relire la section correspondante du user guide et vérifier qu'elle reflète le nouveau comportement.
- [ ] Le code introduit un nouveau panel UI ? → vérifier que les inputs existent dans le payload `latest_vol_surface` ou un autre payload Redis documenté.
- [ ] Le code utilise une valeur "magique" (constante hardcodée) ? → la déclarer dans `VolTradingConfig` et documenter dans `VOL_ENGINE_REFERENCE.md` §6.
- [ ] Le code modifie une formule mathématique ? → mettre à jour la formule dans le user guide ET le engine reference.
- [ ] Le code change la cadence d'un cycle ? → mettre à jour mention "180s" dans les deux docs.
- [ ] Le code introduit un nouveau modèle (PCA, GMM, etc.) ? → ajouter section dédiée au Panel 6 (Model Health) avec métriques de validation.

---

## Ouvertures (non-actionnable mais à garder en tête)

1. **Aucun backtest documenté** : les deux docs décrivent un système temps réel, jamais un harness de validation historique. Sans backtest walk-forward sur 3-5 ans, impossible de mesurer l'edge.
2. **Aucun cost model** : pas de spread, pas de commission, pas de coût de hedging récurrent. P&L attendu Section C du Panel 3 est gross, pas net.
3. **Aucune capacité analysis** : à quel notional la stratégie commence-t-elle à bouger le marché options FX (qui est moins liquide que le spot) ?

Ces 3 points ne sont pas des "discrepancies" mais des absences. À traiter au niveau projet, pas au niveau code.
