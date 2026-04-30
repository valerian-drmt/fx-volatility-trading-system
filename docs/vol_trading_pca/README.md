# vol-trading — README

> Système de trading vol systématique sur EUR/USD FOP via PCA factor model.
> Statut : refactor v2 in progress.
>
> **Pour l'agent code (Claude Code)** : ce README est ton entry point. Lis-le entièrement avant tout commit.
> Tous les détails techniques sont dans `docs/`. Ce fichier ne fait que pointer vers les bonnes specs.

---

## Lire dans cet ordre (obligatoire)

1. **`docs/DECISIONS.md`** — 39 ADRs qui régissent toutes les décisions techniques. Si tu hésites entre 2 implémentations, vérifie d'abord si un ADR a tranché.
2. **`docs/VOL_DISCREPANCY_REPORT.md`** — gap analysis entre user guide existant et code actuel. Indique ce qui manque vs ce qui prétend exister.
3. **Le step doc concerné par ta tâche courante** (cf. table ci-dessous).

Ne pas commencer à coder sans avoir lu (1), (2), et (3) pour la tâche courante.

---

## Architecture en une page

### Services (event-driven via Redis pubsub, source de vérité Postgres)

| Service | Cadence | Rôle | Statut |
|---|---|---|---|
| `vol-engine` | cycle 180s | Surface IV + signaux per-tenor | **existe**, à refactorer |
| `pca-fitter` | cron hebdo | Refit PCA sur 12 mois rolling | **à créer** |
| `snapshot-collector` | cron horaire | Persiste surface 30-dim pour PCA | **à créer** |
| `execution-engine` | event-driven IB | Submit orders + tracking fills | **à créer** |
| `position-monitor` | cycle 60s | MTM + exit rules + delta hedge | **à créer** |
| `db-writer` | event-driven | Sink batch des writes async | **existe** |
| `api` | request-driven | REST + WebSocket frontend | **existe**, à étendre |

### Pipeline workflow utilisateur

```
Étape 1 (Regime gating)        → autorise ou bloque trade
   ↓ si autorisé
Étape 2 (PCA signal detection) → 3 z-scores PC1/PC2/PC3 + actionable flag
   ↓ si actionable
Étape 3 (Trade preview)        → structure + pricing + greeks + sizing + checks
   ↓ si valid_for_submit
Étape 4 (Execution IB)         → orders → fills → position created
   ↓
Étape 5 (Active monitoring)    → MTM + exit rules + delta hedge → close
```

### Stack tech (cf. ADR-002)

- Backend : Python 3.11+
- Database : Postgres 15+ (TIMESTAMPTZ, JSONB, BIGSERIAL ids)
- Cache/bus : Redis 7+ (pubsub + K/V + TTL)
- Broker : IB via `ib_insync`
- Frontend : React + TypeScript
- Math : numpy, scipy, sklearn, pandas
- Pricing : implémentation maison Black-Scholes (pas QuantLib)

### Conventions (cf. ADR-006)

- Tous timestamps en UTC, type `TIMESTAMPTZ`, conversion locale uniquement à l'affichage
- snake_case partout (tables, colonnes, JSON keys, Python vars)
- IDs en `BIGSERIAL` (pas UUID)
- Booléens préfixés `is_`, `has_`, `needs_`

---

## Index des docs

| Doc | Contenu | Effort dev |
|---|---|---|
| `docs/DECISIONS.md` | 39 ADRs — toute décision technique tranchée | — |
| `docs/specs/STEP1_REGIME_GATING.md` | Panel 1 + tables régime + heuristique 3-states | 3.5 j |
| `docs/specs/STEP2_SIGNAL_DETECTION.md` | Panel 2 + PCA fit + signaux PC1/2/3 + stability | 13 j |
| `docs/specs/STEP3_TRADE_PREVIEW.md` | Panel 3 + pricing multi-leg + greeks + sizing + checks | 15 j |
| `docs/specs/STEP4_EXECUTION.md` | Submit IB + fills tracking + rollback partial | 15.5 j |
| `docs/specs/STEP5_ACTIVE_POSITIONS.md` | Panel 4 + MTM + 5 exit rules + delta hedge | 16 j |
| `docs/specs/BACKTEST_WALK_FORWARD.md` | Walk-forward backtest + cost model + validation gates | 16 j |
| `docs/INDEX.md` | Index complet de la doc | — |
| `docs/architecture/services.md` | Diagrammes services + dependencies (à créer) | — |
| `docs/architecture/data_flow.md` | Flux données end-to-end (à créer) | — |

**Total effort estimé : ~79 jours dev**

---

## Ordre d'implémentation recommandé

Strict. Chaque step doit être **fully tested et v1.0 tagged** avant de passer au suivant.

```
Phase Foundation (bloquante)
├── 1. Migration discrepancy fixes (cf. docs/specs/VOL_DISCREPANCY_REPORT.md C1-C2 high-criticality)
│      → corrige cycle 30s→180s docs, contract clarification, etc.
│      Effort : 1-2 j
│
├── 2. Étape 1 (Regime gating) — STEP1_REGIME_GATING.md
│      → Panel 1 fonctionnel, _regime dans payload
│      Effort : 3.5 j
│      Tag : v0.1
│
├── 3. Backfill IB historical (prérequis étape 2)
│      → scripts/backfill_ib_historical.py, ~252 obs daily
│      Effort : 1-2 sem
│      Bloque étape 2 phase production (pas le dev/tests qui peut commencer en parallèle)
│
└── 4. Étape 2 (PCA signal detection) — STEP2_SIGNAL_DETECTION.md
       → Panel 2 fonctionnel avec stability gates visibles
       Effort : 13 j
       Tag : v0.2
       NE PAS ENGAGER EN LIVE TANT QUE BACKTEST NON FAIT

Phase Validation (parallélisable avec Phase Trading)
├── 5. Backtest walk-forward — BACKTEST_WALK_FORWARD.md
│      → Run 1 sanity check, puis Run 3 plein historique
│      Effort : 16 j
│      Tag : v0.3
│      Output : verdict passed / failed sur la stratégie
│      
│      Si verdict = failed → retour étapes 1-2 pour comprendre pourquoi
│      Si verdict = passed → continuer Phase Trading

Phase Trading (séquentielle)
├── 6. Étape 3 (Trade preview) — STEP3_TRADE_PREVIEW.md
│      → Panel 3 fonctionnel, structures pricables, sizing mécanique
│      Effort : 15 j
│      Tag : v0.4
│
├── 7. Étape 4 (Execution) — STEP4_EXECUTION.md
│      → IB connectivity + submit + fills tracking
│      Effort : 15.5 j
│      Phase déploiement : MOCK only en dev. Paper account après backtest.
│      Tag : v0.5
│
└── 8. Étape 5 (Active positions) — STEP5_ACTIVE_POSITIONS.md
       → Monitoring + exit rules + delta hedge
       Effort : 16 j
       Phase déploiement : read-only d'abord, auto-action progressivement.
       Tag : v1.0 ← MVP complet
```

**Règle d'or** : ne jamais commencer un step sans avoir tag v0.X du précédent. Pas de repo à 70% (cf. user preferences).

---

## Setup local minimal (pour démarrer)

```bash
# Prerequis
# - Python 3.11+
# - Postgres 15+
# - Redis 7+
# - IB Gateway (paper account pour dev)
# - Node 20+ (frontend)

# Clone + install
git clone <repo>
cd vol-trading
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd src/frontend && npm install && cd ../..

# DB setup
createdb voltrading
alembic upgrade head

# Config (cf. .env.example)
cp .env.example .env
# Éditer .env avec :
# - POSTGRES_URL
# - REDIS_URL
# - IB_GATEWAY_HOST, IB_GATEWAY_PORT (7497 paper, 7496 live)
# - IB_ACCOUNT_ID

# Run services (dev mode, séparés en terminaux)
python -m src.services.vol_engine
python -m src.services.api
cd src/frontend && npm run dev

# Tests
pytest tests/
```

---

## Garde-fous critiques pour Claude Code

### NE PAS faire

1. **Ne pas trader live sans backtest validé**. Cf. ADR-509 stratégie de déploiement par phases.
2. **Ne pas modifier un ADR en place**. Si décision doit changer : `superseded` + nouvel ADR. Cf. méta-décisions DECISIONS.md.
3. **Ne pas hardcoder des valeurs dans le code** si elles peuvent vivre en table SQL (risk_limits, vrp_table, signal_recommendations_map, exit_rules_config). Hot-reload + audit > simplicité.
4. **Ne pas mock des fills IB en prod**. Mock uniquement dans tests, jamais en runtime.
5. **Ne pas afficher dans l'UI des données fabriquées**. Si une feature n'est pas implémentée (ex: probabilités GMM en MVP), afficher null + "not available", pas une valeur fictive.
6. **Ne pas utiliser data future dans backtest**. Strict cutoff timestamp <= current_fold_time. Cf. ADR-301-302.
7. **Ne pas implémenter d'optimization de paramètres** dans le backtest harness. Si N variantes : N runs distincts. Cf. ADR-307.
8. **Ne pas auto-cancel les ordres stuck**. Detection + alert critical only. Cf. ADR-507.
9. **Ne pas créer de Personal CRM ou gamification**. Cf. ADR-701.

### TOUJOURS faire

1. **Lire le step doc + ADRs avant de coder**. Pas de devinette.
2. **Persister à Postgres avant de publish à Redis**. Postgres = source de vérité. Cf. ADR-004.
3. **Utiliser TIMESTAMPTZ en UTC**. Cf. ADR-006.
4. **Préfixer booléens `is_`, `has_`, `needs_`**. Cf. ADR-006.
5. **Écrire tests pour chaque module avant merge**. Coverage minimum non négocié pour les sections "Tests à écrire" des step docs.
6. **Updater DECISIONS.md** si tu prends une décision technique non triviale qui n'y est pas. Pas optionnel.
7. **Updater le step doc concerné** si l'implémentation diverge de la spec. Doc = vérité.
8. **Logger via execution_audit_log** pour tout événement execution non trivial (submission, ack, fill, rejection, rollback).
9. **Respecter les phases de déploiement** ADR-509. Mock → Paper → Live micro → Live full. Pas de raccourci.
10. **Re-vérifier la fraîcheur des données** dans tout calcul critique (IV < 2 min, regime stable 3 cycles, etc.).

---

## Tests requis avant chaque tag

Pour tagger v0.X, ces tests doivent passer :

| Tag | Tests minimum |
|---|---|
| v0.1 | Step 1 acceptance criteria (cf. STEP1_REGIME_GATING.md §10) |
| v0.2 | Step 2 acceptance + PCA fit déterministe + sign correction + stability gates |
| v0.3 | Backtest harness E2E + cost model validé sur sample + walk-forward isolation no-lookahead |
| v0.4 | Step 3 acceptance + pricing réconcilie BS + 7 pre-submit checks |
| v0.5 | Step 4 acceptance + happy path E2E paper + rollback partial fill + idempotence |
| v1.0 | Step 5 acceptance + 5 exit rules + delta hedge + position close E2E paper |

---

## Commandes Claude Code utiles

```bash
# Avant de commencer un step :
cat docs/DECISIONS.md | grep -A5 "ADR-1"   # ADRs étape 1 par exemple
cat docs/specs/STEP1_REGIME_GATING.md       # spec complète

# Pendant dev :
pytest tests/test_regime_pipeline.py -v     # tests step 1
alembic revision --autogenerate -m "..."    # nouvelle migration
alembic upgrade head                        # appliquer

# Avant commit :
ruff check src/                              # lint
ruff format src/                             # format
pytest tests/ --cov=src --cov-report=term   # tests + coverage

# Avant tag :
pytest tests/integration/                   # E2E
docker compose up -d && pytest tests/e2e/  # full stack
```

---

## Quand tu doutes

Si une décision technique n'est pas couverte par :
1. Le step doc concerné
2. Les ADRs dans DECISIONS.md
3. Les conventions de ce README

Alors :
1. **Ne pas deviner**.
2. Stop, log la question dans `docs/QUESTIONS_FOR_USER.md` (à créer).
3. Continuer sur d'autres tâches qui n'attendent pas la réponse.
4. Demander à l'utilisateur lors du prochain sync.

Le mensonge le plus coûteux ici serait de supposer une décision sans la valider et coder dessus N jours.

---

## Références externes utiles

- IB API docs : https://interactivebrokers.github.io/tws-api/
- ib_insync : https://ib-insync.readthedocs.io/
- Gatheral SVI : "Arbitrage-free SVI volatility surfaces", Gatheral & Jacquier 2014
- Yang-Zhang : "Drift Independent Volatility Estimation", Yang & Zhang 2000
- HAR-RV : "A Simple Approximate Long-Memory Model of Realized Volatility", Corsi 2009
- Litterman-Scheinkman PCA term structure : "Common Factors Affecting Bond Returns", 1991

---

## Contact

Owner : Valérian Darmenté.
Contributions : PR avec ADR si décision technique non triviale.
