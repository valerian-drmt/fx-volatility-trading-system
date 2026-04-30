# DECISIONS.md — Architectural Decision Records

> Registre central des décisions de design du projet vol-trading.
>
> **But** : tracer le pourquoi des choix architecturaux, faciliter onboarding, éviter
> de réouvrir des débats déjà tranchés, et permettre audit a posteriori.
>
> **Format** : ADR (Architecture Decision Record) léger. Chaque décision a :
> - Un ID stable (ex: ADR-012)
> - Un statut (`proposed | accepted | superseded | deprecated`)
> - Une date
> - Le contexte (pourquoi la décision se pose)
> - La décision (ce qui a été décidé)
> - Les conséquences (positives et négatives)
> - Optionnel : alternatives considérées et rejetées
>
> **Source de vérité** : ce document. Toute décision notable d'architecture **doit** y
> être consignée avec un ID. Les sections "Décisions de design notables" des step docs
> sont des résumés ; les détails canoniques vivent ici.
>
> **Convention** : un PR qui introduit ou modifie une décision modifie aussi ce fichier.

---

## Table des matières

### Décisions transversales (architecture globale)
- ADR-001 — Architecture micro-services event-driven via Redis pubsub
- ADR-002 — Stack technique : Python + Postgres + Redis + IB
- ADR-003 — Séparation cycles vs events
- ADR-004 — Postgres comme source de vérité, Redis comme cache et bus
- ADR-005 — Monorepo avec services dans src/services/
- ADR-006 — TimestampTZ pour tous les timestamps, snake_case partout
- ADR-007 — Documentation comme code, ADRs versionés avec git

### Décisions étape 1 — Regime gating
- ADR-101 — Heuristique 3-seuils en MVP, GMM en V2
- ADR-102 — feature_history en wide format
- ADR-103 — Stationnarité régime requise sur 3 cycles
- ADR-104 — vrp_table_default migrée en table SQL
- ADR-105 — events.yaml manuel en MVP

### Décisions étape 2 — Signal detection PCA
- ADR-201 — Option A (PCA factor model) plutôt que Option B (per-tenor)
- ADR-202 — PCA fit sur IV en pourcentage standardisé
- ADR-203 — Fenêtre rolling 12 mois, refit hebdomadaire
- ADR-204 — 6 PCs stockés mais 3 utilisés
- ADR-205 — Sign correction obligatoire à chaque refit
- ADR-206 — Z-scores empiriques rolling 90j (pas N(0,1) théorique)
- ADR-207 — Stability log séparé du model log
- ADR-208 — Recommended structure dans table externe
- ADR-209 — Stability gate strict sur signal actionable
- ADR-210 — Backfill IB historical comme bootstrap

### Décisions backtest walk-forward
- ADR-301 — Walk-forward refit mensuel
- ADR-302 — Aucune carry-over de positions entre folds
- ADR-303 — Cost model OBLIGATOIRE en V1
- ADR-304 — MTM tracking par cycle, pas seulement à exit
- ADR-305 — Skipped cycles loggés row par skip
- ADR-306 — Validation gates strictes
- ADR-307 — Pas d'optimization paramètre dans le harness

### Décisions étape 3 — Trade preview
- ADR-401 — Catalogue de structures externalisé
- ADR-402 — Preview avec expiration 2 minutes
- ADR-403 — Sizing 100% mécanique, pas de discretion
- ADR-404 — Risk limits dans table dédiée hot-reloadable
- ADR-405 — Pre-submit checks séparés en module testable
- ADR-406 — Hedge future modélisé comme leg
- ADR-407 — Limit price avec tolerance 0.5%

### Décisions étape 4 — Execution
- ADR-501 — Service execution-engine séparé
- ADR-502 — ib_insync plutôt que ibapi raw
- ADR-503 — Combo order si possible, séparé sinon
- ADR-504 — Rollback inclut unwind des partial fills
- ADR-505 — Position créée seulement quand fully_filled
- ADR-506 — Lock Redis sur preview_id (TTL 10s)
- ADR-507 — Stuck order detection sans auto-cancel
- ADR-508 — Audit log keep indéfiniment
- ADR-509 — Stratégie de déploiement par phases (mock → paper → live micro → live full)

### Décisions étape 5 — Active positions
- ADR-601 — Service position-monitor séparé, cycle 60s
- ADR-602 — Closing = nouvelle structure
- ADR-603 — 5 exit rules en parallèle, priorité sur conflit
- ADR-604 — Cooldown 5 min sur alerts ET hedges
- ADR-605 — order_role ajouté à orders (pas table closing séparée)
- ADR-606 — Attribution P&L analytique vs full re-pricing
- ADR-607 — Auto-execute en MVP (pas confirmation humaine)

### Décisions transversales aux user preferences
- ADR-701 — Pas de Personal CRM, pas de gamification du trading

---

## Décisions transversales (architecture globale)

### ADR-001 — Architecture micro-services event-driven via Redis pubsub

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Le système se décompose naturellement en plusieurs préoccupations qui ont des cadences différentes : vol-engine (180s), position-monitor (60s), execution-engine (event-driven sur fills), pca-fitter (hebdomadaire). Les coupler dans un monolithe créerait des conflits de scheduling, des dépendances de versioning, et un blast radius énorme en cas de bug.

**Décision** : Architecture multi-services indépendants, communiquant via Redis (pubsub pour événements, K/V pour cache surface/signaux). Chaque service a sa propre table Postgres principale qu'il owne, peut lire d'autres tables mais writes uniquement aux siennes.

**Services** :
- `vol-engine` (existant) : cycle 180s, surface + signaux par tenor
- `pca-fitter` (nouveau) : refit PCA hebdo, background
- `snapshot-collector` (nouveau) : persiste surface horaire pour PCA
- `execution-engine` (nouveau) : IB connectivity, orders, fills
- `position-monitor` (nouveau) : cycle 60s monitoring positions ouvertes
- `db-writer` (existant) : sink batch des writes async
- `api` (existant) : REST + WebSocket pour frontend

**Conséquences positives** : services peuvent crash et restart indépendamment, scaling horizontal possible par service, debug isolé.

**Conséquences négatives** : complexité opérationnelle (orchestration via docker-compose ou k8s), latence inter-services (acceptable car cycles longs), gestion d'erreur réseau à ajouter.

**Alternatives rejetées** : monolithe unique (couplage trop fort, restart global = downtime), event sourcing pur (overkill pour la cardinalité de events).

---

### ADR-002 — Stack technique : Python + Postgres + Redis + IB

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Choix de la stack pour 5 ans+ de vie projet. Trade-off rapidité dev / performance / écosystème quant.

**Décision** :
- Backend : Python 3.11+ (compat ib_insync, sklearn, scipy, fastapi)
- Database : Postgres 15+ (JSONB pour flexibilité, indexing avancé)
- Cache / bus : Redis 7+ (pubsub + K/V + TTL)
- Broker : Interactive Brokers via ib_insync
- Frontend : React + TypeScript (cohérent avec roadmap valerian.dev)
- Math libs : numpy, scipy, sklearn, pandas
- Pricing : implémentation maison de Black-Scholes (PAS QuantLib, surcouche inutile pour FX vanilla)

**Conséquences positives** : écosystème mature, talent pool large, libs scientifiques first-class.

**Conséquences négatives** : Python pas optimal pour le pricing latency-critical (mitigé par Rust pour le LOB engine futur, cf. roadmap pro), dépendance ib_insync non-officielle (mais maintenue activement).

**Alternatives rejetées** :
- C++ pour pricing : effort dev × 5, gain marginal au scale individual
- Julia : écosystème quant trop petit, pool talent limité
- TypeScript backend : pricing/ML libraries inférieures
- Postgres alternatives (TimescaleDB, ClickHouse) : surcomplexité pour cardinalité actuelle (~10M rows/an)

---

### ADR-003 — Séparation cycles vs events

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Certains processus sont naturellement périodiques (cycle vol-engine 180s, monitoring 60s), d'autres sont event-driven (fill IB, regime change). Mixer = race conditions, ordre indéterministe, impossibilité de garantir cohérence.

**Décision** : Strict séparation. Services cycle-driven utilisent `asyncio.sleep(N)` ou cron. Services event-driven utilisent callbacks IB ou subscriptions Redis pubsub. Aucun service ne fait les deux pour le même domaine.

**Cas particulier** : position-monitor est cycle-driven (60s) mais pourrait bénéficier d'event-driven sur regime change critique. Compromise : MVP cycle-driven, V2 ajouter trigger event sur regime change vers pre_event uniquement.

**Conséquences positives** : raisonnement plus simple sur ordre des opérations, debug facilité, pas de surprise sur quand ça tourne.

**Conséquences négatives** : un peu de latence sur events qui pourraient bénéficier d'event-driven (acceptable vu horizon trade jours-semaines).

---

### ADR-004 — Postgres comme source de vérité, Redis comme cache et bus

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Risque classique : data divergence entre stores. Quel store dit la vérité quand ils contredisent ?

**Décision** :
- Postgres = source de vérité unique. Tout state qui survit aux restart est dans Postgres.
- Redis = cache éphémère (TTL 600s sur surface) + bus pubsub. Si Redis perd tout, on peut reconstruire depuis Postgres.
- Aucun write n'est durable s'il n'est pas dans Postgres.

**Pattern** :
1. Service calcule un nouvel état
2. Persist à Postgres (transaction)
3. Publish à Redis (cache + pubsub)
4. Consumers lisent de Redis pour latence, mais peuvent fallback sur Postgres si miss

**Conséquences positives** : cohérence garantie, recovery facile, audit complet via Postgres.

**Conséquences négatives** : double-write coût (acceptable), latence Postgres ≥ Redis (acceptable car write batch via db-writer existant).

---

### ADR-005 — Monorepo avec services dans src/services/

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Multi-services peut conduire à mono-repo ou multi-repo. Trade-off versioning, CI, refactoring cross-service.

**Décision** : Monorepo. Structure :
```
src/
├── core/                    # libs partagées (pricing, math, validation)
│   ├── pricing/
│   ├── vol/
│   ├── structures/
│   ├── scenarios/
│   ├── sizing/
│   ├── validation/
│   └── config/
├── persistence/             # ORM models partagés
├── bus/                     # Redis publishers/channels
├── services/
│   ├── vol_engine/
│   ├── pca_fitter/
│   ├── snapshot_collector/
│   ├── execution_engine/
│   ├── position_monitor/
│   ├── db_writer/
│   └── api/
├── api/                     # routes + models pydantic
└── frontend/                # React app
scripts/                     # one-off (backfill, smoke tests)
docs/                        # ADRs, step specs, architecture
tests/
```

**Conséquences positives** : refactoring cross-service trivial, versioning aligné, CI unique, code partagé sans publish package.

**Conséquences négatives** : taille repo croît, build time peut augmenter (mitigé par caching).

**Alternatives rejetées** : multi-repo (versioning hell pour libs partagées en phase de dev rapide).

---

### ADR-006 — TimestampTZ pour tous les timestamps, snake_case partout

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Cohérence conventions. Trading multi-timezone potentiel (EUR/USD = sessions Asia/Europe/US).

**Décision** :
- Tous timestamps en `TIMESTAMPTZ` (Postgres) avec valeurs UTC
- Conversion en local timezone uniquement à l'affichage frontend
- Snake_case partout : tables, colonnes, JSON keys, Python variables
- Camel case interdit sauf dans les payloads externes (IB API qui utilise camelCase)
- IDs en `BIGSERIAL` plutôt que UUID (faster, pas de collision risque à notre scale)
- Booléens préfixés `is_`, `has_`, `needs_` (`is_active`, `has_no_arb_violation`)

**Conséquences positives** : pas d'ambigüité timezone, code lisible, conventions appliquées par linter.

---

### ADR-007 — Documentation comme code, ADRs versionnés avec git

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Toute documentation séparée du code finit obsolète. Les step specs et ADRs doivent vivre dans le repo et évoluer avec le code.

**Décision** :
- Tous docs (.md) dans `docs/` du repo
- ADR ID stable, jamais réutilisé même si superseded
- Modification ADR = nouvelle version avec status `superseded`, ne pas éditer en place
- Step specs et discrepancy reports dans `docs/specs/`
- Schéma migrations Postgres dans `db/migrations/` avec numérotation séquentielle
- README à la racine = entry point, pointe vers docs/INDEX.md
- Chaque PR significatif doit toucher au moins 1 doc (forcé par PR template)

**Conséquences positives** : doc à jour, traçabilité décisions, onboarding facilité.

---

## Décisions étape 1 — Regime gating

### ADR-101 — Heuristique 3-seuils en MVP, GMM en V2

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Le user guide affiche "Probabilités GMM 78% / 18% / 4%". Le code actuel a `detect_regime(vol_level, vol_of_vol, term_slope) → enum`. C'est une heuristique seuils, pas un GMM.

**Décision** : MVP garde l'heuristique seuils (déjà codée). Le payload `_regime` expose `method="threshold_heuristic"` et `probabilities=null`. Frontend cache zone "Probabilités GMM" tant que `probabilities is None`. V2 implémente vrai GMM (sklearn `GaussianMixture(n_components=3)`) sur features étendues.

**Conséquences positives** : pas de mensonge UI, MVP shippable rapidement, V2 path clair.

**Conséquences négatives** : panel moins riche en MVP (pas de barres de probabilités).

**Alternatives rejetées** : hardcoder probas fictives (mensonge à soi-même), bloquer livraison étape 1 jusqu'à GMM (gate pour rien).

---

### ADR-102 — feature_history en wide format

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Stockage des features pour calcul rolling stats. Choix entre long format (1 row par (timestamp, feature_name)) et wide format (1 row par timestamp avec colonnes par feature).

**Décision** : Wide format. Colonnes explicites : `iv_atm_1m_pct, iv_atm_3m_pct, iv_atm_6m_pct, rv_yz_pct, vol_of_vol_30d_pct, term_slope_pct, vol_level_z90, vol_of_vol_z90, term_slope_z90`.

**Conséquences positives** : lecture Panel 1 = 1 SELECT (pas de pivot), z-scores stockés dénormalisés, indexation simple.

**Conséquences négatives** : ajouter une nouvelle feature = ALTER TABLE. Acceptable car features évoluent lentement (à chaque ADR feature, pas à chaque commit).

---

### ADR-103 — Stationnarité régime requise sur 3 cycles

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Régime peut flipper entre cycles si feature near-threshold. Trader sur un régime qui flipe = bruit, pas signal.

**Décision** : Avant d'autoriser trade, vérifier que `regime_label` est identique sur les 3 derniers cycles consécutifs (= 9 minutes à 180s/cycle). Logic dans `gate_decision()`.

**Conséquences positives** : élimine 90% des faux positifs dûs à features near-threshold.

**Conséquences négatives** : retard de 6-9 min après vrai régime change avant trade. Acceptable vu horizon trade jours-semaines.

**Alternatives rejetées** : 1 cycle (trop bruité), 5 cycles (trop conservateur).

---

### ADR-104 — vrp_table_default migrée en table SQL

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Aujourd'hui dans `core/vol/vrp.py` Python comme constante hardcodée. Plusieurs problèmes : pas de hot-reload, pas d'audit des changements, pas de chemin propre vers `vrp_estimated` future (C1.5 du discrepancy report).

**Décision** : Migrer en table `vrp_table_default(regime, tenor, vrp_vol_pts, calibration_method, calibration_date, notes)`. Seedée avec valeurs actuelles. `calibration_method='hardcoded_placeholder'` initial. Quand C1.5 livré : nouvelle table `vrp_estimated`, fallback sur `vrp_table_default` si miss.

**Conséquences positives** : hot-reload, audit, path d'évolution propre.

**Conséquences négatives** : un SELECT supplémentaire par cycle (négligeable, cacheable).

---

### ADR-105 — events.yaml manuel en MVP

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : event_dampener nécessite calendrier économique. Sources pro (Trading Economics API, Bloomberg) coûteuses. Sources gratuites (ForexFactory scrape, ICS feeds) fragiles.

**Décision** : MVP utilise un fichier `events.yaml` à la racine du repo, mis à jour manuellement chaque mois (15 minutes). Format simple :
```yaml
events:
  - date: 2026-05-08
    type: ECB_meeting
    impact: high
```
Reader Python le parse et populate la table `events` (tronquer + reload mensuel). V2 : intégration API si valeur démontrée.

**Conséquences positives** : zéro dépendance externe, contrôle total, suffit pour MVP démonstrable.

**Conséquences négatives** : maintenance manuelle, oublis possibles. Mitigation : alarme si le `next_event` est NULL ou > 30 jours dans le futur.

---

## Décisions étape 2 — Signal detection PCA

### ADR-201 — Option A (PCA factor model) plutôt que Option B (per-tenor)

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Deux architectures possibles pour détection signal :
- **Option A** : PCA factor model (3 PCs orthogonaux : level, slope, smile)
- **Option B** : per-tenor signals (6 z-scores corrélés)

Trade-off détaillé dans la conversation : A plus pro mais plus exigeant en data ; B plus rapide à shipper mais moins crédible en interview HF.

**Décision** : Option A. Justification : objectif pédagogique = signaling pro. PCA est le standard Litterman-Scheinkman pour modèles factoriels, attendu par benchmarks niche (CFM, Squarepoint, Citadel). Reconstruction historique IB possible (~1 an daily) → atteint T ≥ 300 minimum viable.

**Conséquence importante** : engagement non négociable sur composants validation (cf. ADR-209 stability gate, ADR-205 sign correction). Sans ces composants, A devient cosmétique et perd son avantage signaling vs B.

**Alternatives rejetées** : B (jugé naïf en signaling), hybrid A+B (complexité non justifiée).

---

### ADR-202 — PCA fit sur IV en pourcentage standardisé

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Choix de l'espace de fit PCA. Options : IV brute en %, log(IV), variance σ², moneyness-normalized IV.

**Décision** : IV en pourcentage (ex: 6.05) standardisée par feature (μ, σ par colonne). Loadings interprétables directement en termes de mouvements de surface IV.

**Conséquences positives** : interprétation directe des loadings, comparable aux conventions OTC vanilla FX.

**Conséquences négatives** : variance hétérogène entre tenors (1M plus volatile que 6M), mitigée par standardisation. Outliers extrêmes peuvent dominer (mitigation : winsorization optionnelle V2).

**Alternatives rejetées** : log(IV) (compresse les wings utilement mais ajoute complexité interprétation), variance (unités² peu lisibles).

---

### ADR-203 — Fenêtre rolling 12 mois, refit hebdomadaire

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Trade-off taille fit window vs fraîcheur du modèle.
- Fenêtre courte → s'adapte vite mais bruit (loadings instables)
- Fenêtre longue → loadings stables mais rate les régimes nouveaux

**Décision** : 12 mois rolling, refit chaque dimanche à 02:00 UTC.

**Conséquences positives** : compromise standard FX, ~2200 obs daily ou ~52800 hourly = T/p sain.

**Conséquences négatives** : un changement de régime fundamental (post-2008 type) prend ~3 mois à être pleinement intégré.

**Alternatives rejetées** : refit quotidien (loadings trop instables), fenêtre adaptative (ajout complexité non justifiée MVP).

---

### ADR-204 — 6 PCs stockés mais 3 utilisés

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Combien de PCs garder en post-fit ?

**Décision** : Stocker 6 (sur 30 possibles), utiliser 3 pour signaux.

**Conséquences positives** :
- 3 utilisés = standard interprétable (level/slope/smile)
- 6 stockés permet (a) analyse ex-post : PC4-6 contiennent-ils du signal ? (b) calculer reconstruction error pour détection régime change.

**Conséquences négatives** : stockage légèrement plus grand. Négligeable.

**Alternatives rejetées** : tout stocker (waste), 3 seulement (perd flexibilité analytique).

---

### ADR-205 — Sign correction obligatoire à chaque refit

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Eigenvectors ont signe arbitraire (mathématiquement, +v et -v sont équivalents). PCA refits successifs flippent aléatoirement → z-scores discontinus côté frontend, panel cassé pour user.

**Décision** : À chaque refit, comparer les 3 premiers loadings vs ceux de la version active précédente via cosine similarity. Si `cos < 0` → flip eigenvector (`-v`). Persister le flip en colonne `sign_flip_pcN` pour audit.

**Conséquences positives** : continuité temporelle des z-scores garantie, pas de réinterprétation utilisateur entre refits.

**Conséquences négatives** : le tout premier fit n'a pas de référence (skip flip). Acceptable car convention figée à partir du 2e fit.

---

### ADR-206 — Z-scores empiriques rolling 90j (pas N(0,1) théorique)

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Une fois projection raw_score calculée, comment la convertir en z-score ? Standardiser vs distribution théorique N(0,1) (variance = eigenvalue) ou vs distribution empirique des projections passées.

**Décision** : Empirique. Pour chaque PC, calculer μ et σ des `raw_scores` historiques sur fenêtre rolling 90 jours, puis `z = (raw - μ) / σ`.

**Conséquences positives** : capture les déviations vs comportement empirique réel (qui peut avoir tails plus lourdes que normal). Plus honnête statistiquement.

**Conséquences négatives** : nécessite ≥ 30 obs historiques pour σ stable. Bootstrap : si < 30 obs, fallback `z=0` et label `FAIR`.

---

### ADR-207 — Stability log séparé du model log

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Diagnostics de stabilité (cosine similarity, sign flips, variance changes) pourraient vivre dans `pca_models` directement. Choix de séparation.

**Décision** : Table dédiée `pca_stability_log(new_model_id, previous_model_id, pc_id, cosine_similarity, sign_flipped, variance_change_pct, stability_verdict)`. 1 row par PC par refit (3 rows par refit hebdo).

**Conséquences positives** : audit décorrélé du model lui-même. Si on purge vieux pca_models, on garde les diagnostics. Permet time series de stability.

**Conséquences négatives** : 1 jointure de plus pour panel. Négligeable.

---

### ADR-208 — Recommended structure dans table externe

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Mapping (PC_id, signal_label) → recommended_structure peut être hardcodé Python ou externalisé.

**Décision** : Table `signal_recommendations_map` avec 6 rows seedées. Permet hot-reload + audit du mapping signal→trade. Découplage concerns.

**Conséquences positives** : changer une recommandation = UPDATE, pas redeploy. Audit trace.

**Conséquences négatives** : un SELECT par signal généré (cacheable).

---

### ADR-209 — Stability gate strict sur signal actionable

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Critère pour qu'un signal PCA passe `actionable=true`. Sans gate strict, on trade sur du bruit (loadings instables).

**Décision** : Signal `actionable=true` SEULEMENT si :
1. `loadings_stable[pc_id]` = true (cosine sim ≥ 0.85 vs précédent refit)
2. `variance_explained[pc_id]` > seuil (PC1 ≥ 60%, PC2 ≥ 15%, PC3 ≥ 5%)
3. `|z_score|` > 1.0
4. Pas de contradiction entre PCs (coherence check)
5. Signal persistent ≥ 3 cycles consécutifs

**Conséquences positives** : c'est ce qui distingue PCA pro-grade de PCA cosmétique sklearn. Gate visible dans Panel 2 = signaling rigueur.

**Conséquences négatives** : beaucoup de signaux gates-out (acceptable, c'est le but).

---

### ADR-210 — Backfill IB historical comme bootstrap

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Sans historique, PCA fit impossible (T < 100). Démarrer aujourd'hui = 14 mois d'attente avant T=300 daily.

**Décision** : Script one-off `scripts/backfill_ib_historical.py` qui reconstruit ~1 an de surface daily depuis IB historical IV. Effort : 1-2 sem dev + 4-12h run. Bootstrap T initial à ~252 (1 an daily) puis collection live continue d'enrichir.

**Conséquences positives** : démarrage PCA possible en semaines, pas en mois. Pipeline pleinement fonctionnel rapidement.

**Conséquences négatives** : edge cases nombreux (expiry rolls, holidays, holes wings). Script fragile à maintenir.

---

## Décisions backtest walk-forward

### ADR-301 — Walk-forward refit mensuel

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Walk-forward refits le modèle régulièrement pour mimer la production. Quelle fréquence ?

**Décision** : Refit chaque 30 jours dans le backtest. Cohérent avec ADR-203 (refit hebdo en prod, mais mensuel en backtest pour réduire computation cost — diff acceptable car le pattern de refit a un impact ordre 2 sur Sharpe).

**Conséquences positives** : compromise computation vs fidélité. ~48 folds sur 4 ans = manageable.

**Conséquences négatives** : ne mime pas exactement le refit hebdo prod. À noter dans rapport.

---

### ADR-302 — Aucune carry-over de positions entre folds

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : À fin d'un fold (ex: fin du test_window 1 mois), on a peut-être des positions ouvertes. Continuer dans le fold suivant ou close ?

**Décision** : Force-close à fin de fold avec `exit_reason='fold_end'`. Comptabilisé en P&L du fold courant.

**Conséquences positives** : isolation stricte entre folds. Pas de contamination temporelle. Métriques par fold interprétables.

**Conséquences négatives** : surestime turnover (positions auraient pu être tenues plus longtemps). Conservateur sur Sharpe (cost de close supplémentaire).

---

### ADR-303 — Cost model OBLIGATOIRE en V1

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Tentation de shipper "Sharpe gross seul" pour aller vite. Mais Sharpe gross sans Sharpe net = données fausses, illusion d'edge.

**Décision** : Cost model implémenté **avant** premier run backtest. Composantes : bid-ask spread (par leg), commission IB, expected hedge cost (heuristique sur durée + vol). Reporting toujours net.

**Conséquences positives** : pas d'auto-mensonge. Backtest = vérité.

**Conséquences négatives** : cost model V1 imparfait (calibré ±30% sur sample), mais mieux que zero.

---

### ADR-304 — MTM tracking par cycle, pas seulement à exit

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Pour calculer drawdown et equity curve, besoin de MTM continu, pas juste P&L réalisé à exit.

**Décision** : `backtest_mtm_history` row par cycle de test (~720/mois/fold). Permet equity curve tick par tick.

**Conséquences positives** : drawdown calculable, plot equity curve visible, attribution P&L dynamique.

**Conséquences négatives** : volume rows élevé (~1.7M sur 50 runs). Indexer correctement, partition possible si > 10M.

---

### ADR-305 — Skipped cycles loggés row par skip

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Majorité des cycles ne génèrent pas de trade (gate régime, pas de signal, etc.). Logger ou pas ?

**Décision** : Logger. Table `backtest_skipped_cycles(timestamp, skip_reason, skip_detail)`. Volumineux mais critique pour debug.

**Conséquences positives** : permet d'analyser pourquoi le système ne trade pas, calibrer thresholds, détecter régimes morts.

**Conséquences négatives** : ~1.4M rows. Compressible si besoin (BLOB JSONB).

---

### ADR-306 — Validation gates strictes

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Backtest peut donner Sharpe positif sur du bruit. Sans gates strictes, on conclut "passed" à tort.

**Décision** : 7 gates, FAIL si l'un échoue :
1. Sharpe OOS ≥ 0.5
2. Sharpe gap IS-OOS ≤ Sharpe OOS (pas d'overfit sévère)
3. Cost model intégré (pas Sharpe gross)
4. Max drawdown ≤ 30%
5. Hit rate ≥ 45%
6. Capacity sufficient
7. Robustesse cross-régime (positif en calm ET stressed)

**Conséquences positives** : mieux vaut un FAIL qu'un PASS sur du bruit.

**Conséquences négatives** : peut rejeter des stratégies marginalement profitables. Acceptable vu objectif rigueur.

---

### ADR-307 — Pas d'optimization paramètre dans le harness

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Tentation d'ajouter grid search ou bayesian optimization pour trouver "meilleurs" params.

**Décision** : Aucun. Si on veut tester N variantes, lance N runs distincts (chacun avec son `backtest_runs` row). Empêche data snooping caché.

**Conséquences positives** : pas d'overfit déguisé en validation.

**Conséquences négatives** : exploration manuelle plus laborieuse. Acceptable, et c'est le but.

---

## Décisions étape 3 — Trade preview

### ADR-401 — Catalogue de structures externalisé

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Définir les structures (straddle, butterfly, calendar) en code Python ou en table SQL ?

**Décision** : Table `structure_definitions(structure_type, leg_template, ...)`. JSONB pour leg_template (liste de templates de legs). 6 structures seedées.

**Conséquences positives** : ajout structures sans deploy, audit changements, hot-reload.

**Conséquences négatives** : un peu de complexité initiale parsing JSONB. Acceptable.

---

### ADR-402 — Preview avec expiration 2 minutes

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Entre génération preview et clic Submit, surface bouge. Si user attend 10 min puis submit, IV stale → trade sur fausse base.

**Décision** : Preview valide 2 minutes (`expires_at = created_at + 120s`). Au-delà, Submit refuse, user doit re-arm.

**Conséquences positives** : force fraîcheur. Évite trades sur stale.

**Conséquences négatives** : user peut être frustré si réfléchit longtemps. Mitigation : countdown visible UI.

---

### ADR-403 — Sizing 100% mécanique, pas de discretion

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Permettre ou pas l'override manuel du sizing par user ?

**Décision** : Aucun override. Formule explicite `final_qty = base × z_factor × book_penalty × event_dampener × regime_mult`. Aucune intervention humaine sauf cancel total.

**Conséquences positives** : cohérent avec objectif pipeline pro. Discrétion = leak bias humain. Mécanique = backtestable.

**Conséquences négatives** : moins de flexibilité. Acceptable car objectif pédagogique.

---

### ADR-404 — Risk limits dans table dédiée hot-reloadable

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Limites de risque (max_loss_per_trade_pct, max_book_vega_usd) dans config.py ou table SQL ?

**Décision** : Table `risk_limits(limit_name, limit_value, unit, description)`. Hot-reload + audit.

**Conséquences positives** : ajustement runtime sans deploy. Trace des changements.

**Conséquences négatives** : query DB par preview. Cacheable.

---

### ADR-405 — Pre-submit checks séparés en module testable

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Logique de validation peut être inline endpoint ou module séparé.

**Décision** : Module `core/validation/pre_submit.py` avec `run_pre_submit_checks(...)`. Réutilisé par backtest harness pour simuler validation historique.

**Conséquences positives** : tests unitaires faciles, réutilisation cross-component.

---

### ADR-406 — Hedge future modélisé comme leg

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Le delta hedge est un future, pas une option. Modéliser comme leg séparée ou comme entité distincte ?

**Décision** : Leg avec `contract_type='future'`, `strike=null`. Uniformité du pricing/greeks pipeline.

**Conséquences positives** : pas de duplication code. Pipeline unique.

**Conséquences négatives** : quelques `if contract_type == 'future': skip` dans pricer (mineur).

---

### ADR-407 — Limit price avec tolerance 0.5%

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Type d'order à utiliser : market, limit, mid ?

**Décision** : LMT order avec limit_price = preview_price ± 0.5% selon side. Pas market (slippage extrême wings), pas mid-price (risque pas de fill).

**Conséquences positives** : balance risk slippage / fill probability.

**Conséquences négatives** : peut rater fill en régime stressed (spread > 0.5%). À calibrer empiriquement.

---

## Décisions étape 4 — Execution

### ADR-501 — Service execution-engine séparé

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Execution est event-driven (callbacks IB sur fills), vol-engine est cycle-driven. Mixer = race conditions.

**Décision** : Service distinct `execution-engine`. Communique avec autres services via Redis pubsub uniquement.

**Conséquences positives** : isolation des cadences. Crash execution ≠ crash vol-engine.

---

### ADR-502 — ib_insync plutôt que ibapi raw

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : ibapi (officiel IB) ou ib_insync (wrapper async pythonic) ?

**Décision** : `ib_insync`. Async-friendly, plus simple, large adoption.

**Conséquences positives** : code plus lisible, intégration asyncio native.

**Conséquences négatives** : dépendance non-officielle. Mitigation : maintenue activement, pinned version.

---

### ADR-503 — Combo order si possible, séparé sinon

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Multi-leg structure peut être soumise comme combo IB (atomic) ou orders séparés.

**Décision** : Combo si toutes legs même expiry et même contract type (option). Orders séparés sinon (ex: calendar 1M+3M).

**Conséquences positives** : combo réduit risque partial fill (atomic). Séparé permet calendar.

**Conséquences négatives** : code plus complexe (deux paths). Acceptable.

---

### ADR-504 — Rollback inclut unwind des partial fills

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Si une leg fail (rejection ou timeout) après que d'autres sont partiellement filled, que faire des partial ?

**Décision** : Rollback : (a) cancel toutes les orders pending/partially_filled, (b) pour les partial fills, créer un closing order opposite side pour fermer les units déjà filled.

**Conséquences positives** : pas de position naked résiduelle. Risk management strict.

**Conséquences négatives** : double slippage (fill + unwind). Edge case rare. Acceptable.

---

### ADR-505 — Position créée seulement quand fully_filled

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Dès le premier partial fill, on a "une position" partielle. Créer un row dans `positions` ?

**Décision** : Non. `positions` row créée SEULEMENT quand structure passe à `fully_filled`. Avant ça, on suit via `structures.state` et `orders` granulaires.

**Conséquences positives** : pas de position fantôme partielle dans le book. Simplifie sizing pour next trade.

**Conséquences négatives** : pas de monitoring P&L pendant phase de fills (acceptable, durée < 30s typiquement).

---

### ADR-506 — Lock Redis sur preview_id (TTL 10s)

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Double-clic Submit ou retry réseau peut créer 2 trades pour le même preview.

**Décision** : Lock Redis `submit_lock:{preview_id}` avec TTL 10s, set NX. Si déjà set → "already_being_submitted".

**Conséquences positives** : idempotence garantie.

**Conséquences négatives** : si crash entre lock et complete, lock expire après 10s. Acceptable.

---

### ADR-507 — Stuck order detection sans auto-cancel

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Un ordre peut rester en `acknowledged` indéfiniment sans fill (rare mais possible). Auto-cancel ?

**Décision** : MVP : detection + alert critical, PAS d'auto-cancel. Décision humaine requise. V2 peut configurer auto-cancel après N min.

**Conséquences positives** : conservateur, pas d'action surprise.

**Conséquences négatives** : nécessite intervention humaine. Acceptable car edge case rare.

---

### ADR-508 — Audit log keep indéfiniment

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : `execution_audit_log` peut grossir. Purger après N jours ou keep ?

**Décision** : Keep indéfiniment. Volume estimé ~20k rows/an, négligeable. Valeur post-mortem élevée (debugging issues mois après).

---

### ADR-509 — Stratégie de déploiement par phases

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Bug exécution = perte argent réelle. Déploiement progressif obligatoire.

**Décision** :
- Phase 1 : Mock IB (tests). Capital risqué : $0
- Phase 2 : IB Paper account. Capital risqué : $0. Validation : 1-2 mois sans bug
- Phase 3 : IB Live micro size (max qty=1, max loss < $500/trade). Validation : 1 mois sans incident, backtest validé
- Phase 4 : IB Live full size (selon risk_limits). Validation : 3 mois Phase 3 sans incident

**Conséquences** : longueur du chemin assumée. Aligné avec préférence user "jamais de live sans backtest+alpha validé".

---

## Décisions étape 5 — Active positions

### ADR-601 — Service position-monitor séparé, cycle 60s

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Monitoring positions ouvertes : event-driven (sur signal change) ou cycle-driven ?

**Décision** : Cycle 60s. Service distinct `position-monitor`. Compromise : moins réactif que event-driven mais plus simple, suffisant pour horizon trade jours-semaines.

**Conséquences positives** : implémentation simple, debug facile.

**Conséquences négatives** : peut rater moves très rapides intra-cycle (acceptable).

---

### ADR-602 — Closing = nouvelle structure

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Quand exit rule trigger, on crée un trade de fermeture. Modifier la structure existante ou en créer une nouvelle ?

**Décision** : Nouvelle structure avec `closing_structure_id` pointant vers la position. Pipeline étape 4 réutilisé.

**Conséquences positives** : audit clair (entry + closing distincts), pipeline unique.

**Conséquences négatives** : 2 rows `structures` par trade complet. Acceptable.

---

### ADR-603 — 5 exit rules en parallèle, priorité sur conflit

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Plusieurs rules peuvent trigger simultanément. Logic de résolution ?

**Décision** : Toutes les rules s'évaluent en parallèle. Si plusieurs triggered, action prise = celle de la rule avec priorité max.

**Priorités** :
- 6 : pre_event_regime (override absolu)
- 5 : time_to_expiry_critical (<7j)
- 4 : signal_reverse
- 3 : stop_loss_vega
- 2 : time_based

**Conséquences positives** : déterministe, hardcoded en config table pour audit.

---

### ADR-604 — Cooldown 5 min sur alerts ET hedges

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Sans cooldown, même rule peut trigger à chaque cycle = spam alerts ou over-hedging.

**Décision** : Cooldown 5 min entre 2 triggers de la même rule sur la même position. Idem pour hedges (max 1 hedge / 5 min). Configurable via `delta_hedge_config.max_hedge_frequency_seconds`.

**Conséquences positives** : pas de spam, pas d'overtrading.

**Conséquences négatives** : peut louper opportunité de rebalance pendant move rapide. Acceptable.

---

### ADR-605 — order_role ajouté à orders (pas table closing séparée)

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Closing orders : table dédiée ou colonne discriminante sur `orders` ?

**Décision** : Colonne `order_role` sur `orders` ('entry' | 'closing' | 'unwind'). Pas de table séparée.

**Conséquences positives** : pas de duplication schema. Jointures plus simples.

**Conséquences négatives** : un peu de logique conditionnelle. Acceptable.

---

### ADR-606 — Attribution P&L analytique vs full re-pricing

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Pour décomposer P&L en vega/gamma/theta : utiliser greeks à entry × moves (linéarisation analytique) ou re-pricer la structure à chaque shock ?

**Décision** : Analytique (greeks at entry × moves). Reste capturé dans `other_pnl_usd`.

**Conséquences positives** : 100x plus rapide. Suffisant pour reporting.

**Conséquences négatives** : pour gros moves, écart vs full re-pricing significatif. Mitigation : monitor `other_pnl_usd`, si > 10% du gross alerter pour switch full re-pricing.

---

### ADR-607 — Auto-execute en MVP (pas confirmation humaine)

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : Quand exit rule trigger EXIT, fermer auto ou demander confirmation user ?

**Décision** : Auto-execute. Cohérent avec ADR-403 (sizing 100% mécanique).

**Phase déploiement** : Phase 1 (read-only, no auto-action) → Phase 2 (auto-hedge only) → Phase 3 (auto-hedge + auto-exit). Cf. ADR-509.

**Conséquences positives** : aligné avec design pipeline pro, élimine bias humain.

**Conséquences négatives** : confiance totale dans les rules requise. Validation backtest impérative avant live.

---

## Décisions transversales aux user preferences

### ADR-701 — Pas de Personal CRM, pas de gamification du trading

**Statut** : accepted
**Date** : 2026-04-30
**Contexte** : User preferences interdit explicitement Personal CRM (créerait bias instrumental détectable, dégrade lien social). Risque équivalent : gamifier le trading (badges, streaks, points) créerait bias dopaminergique de statut, dégrade rigueur scientifique.

**Décision** : UI minimale, fonctionnelle. Pas de :
- Score / leaderboard / badges
- Streaks de trades gagnants affichés
- Pop-ups célébration sur P&L positif
- Comparaison vs "autres traders"

Au contraire, UI met en avant :
- Distance à validation backtest (combien de gates failed)
- Quality des signaux (stability flags visibles)
- Limitations connues (cf. sections "Ouvertures" des step docs)

**Conséquences positives** : préserve cadre cosmologique sisyphien (lucidité sans illusion), évite circuit dopaminergique de statut.

**Conséquences négatives** : UI moins "engageante" au sens habituel. Mais c'est le but.

---

## Méta-décisions

### Comment ajouter un ADR

1. Créer un PR qui ajoute l'ADR au bon endroit (section thématique)
2. ID = next available (ex: ADR-208 si dernière section 2 est ADR-207)
3. Statut initial : `proposed`
4. Discussion en review PR
5. Merge → statut devient `accepted`

### Comment modifier un ADR

Ne JAMAIS éditer en place. Si un ADR doit changer :
1. Statut de l'ADR existant → `superseded`
2. Ajouter ligne `Superseded by: ADR-XXX`
3. Créer nouvel ADR-XXX avec décision révisée
4. Référencer l'ADR superseded en contexte

### Audit trail

Le git log de ce fichier = historique complet des décisions du projet. Précieux pour onboarding ou post-mortem.

---

## Index inversé : décisions par thème

### Sécurité financière (gate, validation, blocage)
ADR-103, ADR-209, ADR-303, ADR-306, ADR-402, ADR-403, ADR-404, ADR-405, ADR-407, ADR-504, ADR-506, ADR-507, ADR-509, ADR-603, ADR-606, ADR-607

### Audit et traçabilité
ADR-007, ADR-104, ADR-207, ADR-208, ADR-401, ADR-404, ADR-508, ADR-602, ADR-605

### Performance et scale
ADR-002, ADR-102, ADR-204, ADR-304, ADR-403, ADR-606

### Path d'évolution future
ADR-101, ADR-104, ADR-204, ADR-210, ADR-507

### Cohérence avec user preferences
ADR-403, ADR-607, ADR-701

### Conventions de code
ADR-005, ADR-006, ADR-007
