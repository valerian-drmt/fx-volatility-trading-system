# `tests/` — structure et conventions

> Doc de référence pour savoir **où mettre un test** et **comment exécuter**.

---

## Pyramide à 2 niveaux + smoke externalisé

```
tests/
├── fixtures/               # builders réutilisables (factories de positions, vol surfaces, etc.)
│
├── unit/                   # in-process, no I/O, < 100ms par test
│   └── <mirror src/>
│
├── integration/            # I/O réel (DB/Redis/HTTP), secondes par test
│   └── pipeline_<sub-system>/
│
└── old/                    # tests historiques en attente de triage (pré-R9)
                            # NON collectés par pytest depuis Step 17 :
                            # testpaths = ["tests/unit","tests/integration"]
```

Le **3e étage de la pyramide (smoke / e2e)** vit hors de `tests/` : validation
manuelle de la stack complète + Playwright côté frontend (`frontend/e2e/`).

Cette répartition reflète la nature des tests :

| Niveau | Question répondue | Outil | Vitesse | Indépendance |
|---|---|---|---|---|
| unit | "ce module fait-il son boulot ?" | pytest | < 100ms | mocks, pas d'I/O |
| integration | "ces N modules ensemble produisent-ils le bon output ?" | pytest + containers | secondes | DB/Redis/IB réels (ou stubs pour IB) |
| smoke | "le user voit-il ce qu'il doit voir ?" | manuel + Playwright | min | stack complète |

---

## `tests/unit/` — mirror de `src/`

**Règle** : `tests/unit/<X>/test_<Y>.py` teste `src/<X>/<Y>.py`. Path = transformation directe.

```
src/                            tests/unit/
├── api/                  →     ├── api/
│   ├── orchestration/    →     │   ├── orchestration/
│   ├── schemas/          →     │   └── (schemas/ — Pydantic, no logic to test)
│   ├── routers/          →     │   └── (routers/ — covered by integration)
├── bus/                  →     ├── bus/
├── core/                 →     ├── core/
├── persistence/          →     ├── persistence/
├── engines/              →     ├── engines/
│   ├── db_writer/        →     │   ├── db_writer/
│   ├── execution/        →     │   ├── execution/
│   ├── market_data/      →     │   ├── market_data/
│   ├── risk/             →     │   ├── risk/
│   └── vol/              →     │   └── vol/
└── shared/               →     └── shared/
```

**Pas de folders `tests/unit/postgres/`, `tests/unit/redis/`, `tests/unit/ib-gateway/`** parce que ces containers utilisent des images off-the-shelf (postgres:16, redis:7, gnzsnz/ib-gateway) sans code Python custom à unit-tester. Leur validation passe par le smoke manuel de la stack complète.

**Pas de folder `tests/unit/nginx/`** non plus — la conf nginx est testée via `tests/integration/docker_compose/` (syntaxe + reload).

**Pas de folder `tests/unit/frontend/`** — le frontend a son propre framework (Vitest + Playwright) dans `frontend/tests/` ou `frontend/__tests__/` selon la convention adoptée plus tard.

### Critères pour qu'un test soit "unit"

Tous obligatoires :

- ✅ Pas d'I/O réseau (pas de `redis.from_url`, pas de `httpx.get`, pas de socket)
- ✅ Pas d'I/O disque hors tmp (pas d'écriture SQLite réel ; `tmp_path` fixture pytest OK)
- ✅ Pas de container Docker
- ✅ Mocks/stubs pour les dépendances externes (`AsyncMock` pour ib_insync, `fakeredis` ou MagicMock pour Redis)
- ✅ < 100ms par test typique (le cumul de `tests/unit/` doit rester < 30s)

**Règle simple côté reviewer** : si tu vois `import redis` ou `import psycopg` ou `from ib_insync import IB` dans le test, c'est de l'integration, pas du unit. Exception : import de types pour l'annotation ne compte pas.

---

## `tests/integration/` — par pipeline (sous-système)

**Règle** : on regroupe par **chemin de données end-to-end-partiel**, pas par container individuel ni par edge isolée. Chaque pipeline représente un sous-système qui doit fonctionner ensemble.

| Folder | Pipeline testé | Containers impliqués |
|---|---|---|
| `pipeline_redis_bus/` | producers + consumers Redis (`bus.publisher`, channels, cache TTL) | redis + 1-2 producer/consumer Python in-process |
| `pipeline_db_writer/` | events Redis → db-writer → Postgres (idempotency, retry, shutdown) | postgres + redis + db-writer |
| `pipeline_vol/` | ib-stub → market-data → redis → vol-engine → postgres (SVI fit, signal generation) | ib-stub + market-data + redis + vol-engine + postgres |
| `pipeline_risk/` | spot+surface in redis → risk-engine → greeks+pnl_curve out (full cycle) | redis + risk-engine + (postgres pour positions stub future) |
| `pipeline_api_serving/` | REST endpoints lisant DB+Redis : `/health`, `/api/v1/risk`, `/api/v1/pnl-curve`, etc. | postgres + redis + api + (nginx optionnel) |
| `pipeline_ws_bridge/` | engine PUBLISH → api SUBSCRIBE → broadcast WS → client receive | un engine + redis + api + nginx + client websocket |
| `ci_workflows/` | tests sur `.github/workflows/*.yml` (existence, structure, déclencheurs) | aucun container (analyse statique YAML) |
| `docker_compose/` | tests sur `docker-compose.yml` (syntaxe `compose config`, services attendus, healthchecks bien définis) | docker daemon mais pas de container réellement up |

### Pourquoi pas un folder par paire de containers (edge)

Granularité trop fine : il y a 14+ edges dans le graphe. Tu te retrouves avec 14 dossiers de 1-2 fichiers chacun, et la cohésion sémantique se perd ("test_redis_market_data" vs "test_redis_vol_engine" sont presque identiques mais doublonnés).

**À l'inverse** : `pipeline_vol/` regroupe `test_market_data_writes_spot.py`, `test_vol_reads_spot_writes_surface.py`, `test_vol_signal_publishes.py` — tous lisibles dans le même dossier comme une chaîne logique.

### Pourquoi pas un folder par scenario (e2e)

`pipeline_api_serving/` n'est pas e2e : il teste l'api isolée du frontend. L'e2e (user clique un bouton, voit un nombre changer) c'est la validation manuelle de la stack ou Playwright côté frontend.

---

## `tests/fixtures/` — builders réutilisables

Pour éviter de dupliquer les setup data dans chaque test :

```python
# tests/fixtures/positions.py
from persistence.models import OpenPosition

def make_long_call(strike=1.17, qty=1, ...):
    return OpenPosition(
        symbol="EURUSD", instrument_type="FOP",
        right="C", strike=strike, quantity=qty, ...
    )
```

Importable dans n'importe quel test :

```python
from tests.fixtures.positions import make_long_call
```

---

## `tests/old/` — supprimée

Quarantine historique vidée lors du cleanup repo (2026-06-06) puis
**supprimée en R11**. Son dernier survivant, **`test_nginx_config_syntax.py`**
(offline parse-level validation des configs nginx, complément du `nginx -t`
live), a été déplacé dans **`tests/unit/infrastructure/`** : il est désormais
collecté avec la suite unit et reste référencé explicitement par le job
`nginx-config` de `.github/workflows/ci.yml`. Le chemin des configs y est
résolu via remontée jusqu'au `pyproject.toml`, donc insensible à un futur
déplacement.

---

## Configuration pytest

Vit dans `pyproject.toml § [tool.pytest.ini_options]` (single source of
truth, cf. CLAUDE.md). Markers et `testpaths` y sont déjà définis :

```toml
[tool.pytest.ini_options]
testpaths = ["tests/unit", "tests/integration"]
markers = [
    "integration: requires real IB Gateway (IB_RUN_INTEGRATION=1)",
    "db_integration: requires real Postgres (DB_RUN_INTEGRATION=1)",
    "redis_integration: requires real Redis (REDIS_RUN_INTEGRATION=1)",
]
```

CI exerce ces markers dans `.github/workflows/ci.yml` (job
`live-integration` pour db + redis, job `integration` manuel pour IB).

---

## Commandes courantes

```bash
# Tout le suite (unit + integration)
python -m pytest

# Que les unit tests (rapide, dev loop)
python -m pytest tests/unit

# Un seul module unit
python -m pytest tests/unit/core/ -v

# Un pipeline integration spécifique
python -m pytest tests/integration/pipeline_db_writer/ -v
```

---

## Décision de design (FAQ)

**Q : Pourquoi pas `tests/smoke/` en pytest ?**
A : Les smoke tests du projet sont **interactifs** (notebooks avec OK/FAIL en sortie + troubleshooting markdown). Recoder ça en pytest serait redondant. Garde la séparation : pytest = automatique CI-friendly, notebooks = inspection manuelle.

**Q : Que faire si un test est unit ET integration ?**
A : Probablement deux tests distincts. La couche unit mock les I/O ; la couche integration les exerce vraiment. Si tu peux pas séparer, classe en integration (le filet de sécurité plus large l'emporte).

**Q : `tests/unit/engines/` est obligatoire ou je peux flatter en `tests/unit/{db_writer,market_data,risk,vol}/` ?**
A : Reflète `src/`. `src/engines/` existe pour grouper les 5 engines, donc `tests/unit/engines/` aussi. Cohérence du mapping path-to-path > économie de profondeur.
