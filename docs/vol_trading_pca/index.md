# Index — `docs/vol_trading_pca/`

Index navigable de la documentation du projet vol-trading PCA. Ce fichier ne contient
**aucune** information primaire — juste des pointeurs vers les vraies sources.

> Ordre de lecture conseillé pour un nouvel arrivant (humain ou agent code) :
> 1. `README.md` (ce dossier) — entry point, garde-fous, ordre d'implémentation
> 2. `project_architecture.md` — vue topologique containers × steps
> 3. `architecture/service.md` — diagrammes services + dépendances
> 4. `architecture/data_flow.md` — flux de données end-to-end
> 5. `DECISIONS.md` — 39 ADRs (consulter au cas par cas)
> 6. `specs/STEPx_*.md` — spec détaillée du step en cours

---

## 1. Entry points

| Fichier | Rôle |
|---|---|
| [`README.md`](README.md) | Vue projet, garde-fous, ordre d'implémentation, setup local |
| [`project_architecture.md`](project_architecture.md) | Matrice container × step, pipeline runtime, plan onglets frontend |
| [`DECISIONS.md`](DECISIONS.md) | 39 ADRs — toute décision technique tranchée |

---

## 2. Architecture

| Fichier | Rôle |
|---|---|
| [`architecture/service.md`](architecture/service.md) | Diagrammes services + matrice de dépendances, ports, IB clientIds |
| [`architecture/data_flow.md`](architecture/data_flow.md) | Flux end-to-end : ticks → surface → signal → preview → order → position → exit |

### Containers (1 fiche par service)

| Container | Fichier | Statut |
|---|---|---|
| `market-data` | [`architecture/container_market_data.md`](architecture/container_market_data.md) | ✅ existe |
| `vol-engine` | [`architecture/container_vol_engine.md`](architecture/container_vol_engine.md) | ✅ existe (refactor) |
| `pca-fitter` | [`architecture/container_pca_fitter.md`](architecture/container_pca_fitter.md) | ❌ à créer |
| `snapshot-collector` | [`architecture/container_snapshot_collector.md`](architecture/container_snapshot_collector.md) | ❌ à créer |
| `risk` | [`architecture/container_risk.md`](architecture/container_risk.md) | ✅ existe |
| `execution-engine` | [`architecture/container_execution_engine.md`](architecture/container_execution_engine.md) | ✅ existe |
| `db-writer` | [`architecture/container_db_writer.md`](architecture/container_db_writer.md) | ✅ existe |
| `api` | [`architecture/container_api.md`](architecture/container_api.md) | ✅ existe (à étendre) |
| `frontend` | [`architecture/container_frontend.md`](architecture/container_frontend.md) | ✅ existe (à étendre) |
| `backtest-runner` | [`architecture/container_backtest_runner.md`](architecture/container_backtest_runner.md) | ❌ à créer |

---

## 3. Specs (1 fichier par step + backtest)

| Step | Fichier | Effort | Tag |
|---|---|---|---|
| 1 — Regime gating | [`specs/STEP1_REGIME_GATING.md`](specs/STEP1_REGIME_GATING.md) | 3.5 j | v0.1 |
| 2 — PCA signal detection | [`specs/STEP2_SIGNAL_DETECTION.md`](specs/STEP2_SIGNAL_DETECTION.md) | 13 j | v0.2 |
| Backtest walk-forward | [`specs/BACKTEST_WALK_FORWARD.md`](specs/BACKTEST_WALK_FORWARD.md) | 16 j | v0.3 |
| 3 — Trade preview | [`specs/STEP3_TRADE_PREVIEW.md`](specs/STEP3_TRADE_PREVIEW.md) | 15 j | v0.4 |
| 4 — Execution IB | [`specs/STEP4_EXECUTION.md`](specs/STEP4_EXECUTION.md) | 15.5 j | v0.5 |
| 5 — Active positions | [`specs/STEP5_ACTIVE_POSITIONS.md`](specs/STEP5_ACTIVE_POSITIONS.md) | 16 j | v1.0 |

**Total : ~79 jours dev** (cf. README §Index des docs).

---

## 4. ADRs par thème (extraits de `DECISIONS.md`)

| Thème | ADR range |
|---|---|
| Architecture générale (stack, conventions, persistence) | ADR-001 → ADR-009 |
| Step 1 — Regime gating | ADR-101 → ADR-109 |
| Step 2 — PCA factor model | ADR-201 → ADR-209 |
| Step 3 — Trade preview & pricing | ADR-301 → ADR-309 |
| Step 4 — Execution & IB | ADR-401 → ADR-409 |
| Step 5 — Active positions & exits | ADR-501 → ADR-509 |
| Backtest harness | ADR-601 → ADR-609 |
| Méta (out-of-scope, non-goals) | ADR-701 → ADR-709 |

> Les numéros sont logiques (groupés par phase), pas séquentiels stricts. Vérifier le
> sommaire de `DECISIONS.md` pour la liste exacte.

---

## 5. Conventions transverses (rappel rapide)

- **Timestamps** : `TIMESTAMPTZ` UTC partout (ADR-006)
- **IDs** : `BIGSERIAL`, pas UUID (ADR-006)
- **JSON** : `JSONB` Postgres, `snake_case` keys (ADR-006)
- **Booléens** : préfixés `is_`, `has_`, `needs_` (ADR-006)
- **Postgres = source de vérité** : persist avant publish Redis (ADR-004)
- **Pas de Personal CRM, gamification, optim auto** (ADR-701, ADR-307)

Tout le reste : voir DECISIONS.md.
