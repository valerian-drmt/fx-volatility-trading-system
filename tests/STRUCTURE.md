# `tests/` — structure et conventions

> Doc de référence pour savoir **où mettre un test** et **comment exécuter**.
> Posée 28/04/2026 (R9 sandbox), pré-migration des anciens tests qui sont
> dans `tests/old/` en attendant d'être triés.

---

## Pyramide à 2 niveaux + smoke externalisé

```
tests/
├── conftest.py             # fixtures globales, sys.path, Qt offscreen
├── fixtures/               # builders réutilisables (factories de positions, vol surfaces, etc.)
│
├── unit/                   # in-process, no I/O, < 100ms par test
│   └── <mirror src/>
│
├── integration/            # I/O réel (DB/Redis/HTTP), secondes par test
│   └── pipeline_<sub-system>/
│
└── old/                    # tests historiques en attente de triage (pré-R9)
```

Le **3e étage de la pyramide (smoke / e2e)** vit hors de `tests/` :

- **`scripts/smoke/<container>/0N_test_*.{py,ipynb}`** — tests interactifs Jupyter
  (ou `.py` pour ib-gateway à cause de l'incompat ib_insync ↔ Jupyter).
  Validés à la main avec inspection visuelle, pas via pytest.

Cette répartition reflète la nature des tests :

| Niveau | Question répondue | Outil | Vitesse | Indépendance |
|---|---|---|---|---|
| unit | "ce module fait-il son boulot ?" | pytest | < 100ms | mocks, pas d'I/O |
| integration | "ces N modules ensemble produisent-ils le bon output ?" | pytest + containers | secondes | DB/Redis/IB réels (ou stubs pour IB) |
| smoke | "le user voit-il ce qu'il doit voir ?" | notebook Jupyter | min | stack complète |

---

## `tests/unit/` — mirror de `src/`

**Règle** : `tests/unit/<X>/test_<Y>.py` teste `src/<X>/<Y>.py`. Path = transformation directe.

```
src/                            tests/unit/
├── api/                  →     ├── api/
├── bus/                  →     ├── bus/
├── core/                 →     ├── core/
├── persistence/          →     ├── persistence/
├── services/             →     ├── services/
│   ├── db_writer/        →     │   ├── db_writer/
│   ├── market_data/      →     │   ├── market_data/
│   ├── risk/             →     │   ├── risk/
│   └── vol/              →     │   └── vol/
└── shared/               →     └── shared/
```

**Pas de folders `tests/unit/postgres/`, `tests/unit/redis/`, `tests/unit/ib-gateway/`** parce que ces containers utilisent des images off-the-shelf (postgres:16, redis:7, gnzsnz/ib-gateway) sans code Python custom à unit-tester. Leur validation passe par le smoke (cf. `scripts/smoke/{postgresql,redis,ib-gateway}/`).

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

`pipeline_api_serving/` n'est pas e2e : il teste l'api isolée du frontend. L'e2e (user clique un bouton, voit un nombre changer) c'est `scripts/smoke/<container>/` côté notebooks ou Playwright côté frontend.

---

## `tests/fixtures/` — builders réutilisables

Pour éviter de dupliquer les setup data dans chaque test :

```python
# tests/fixtures/positions.py
from persistence.models import Position

def make_long_call(strike=1.17, qty=1, ...):
    return Position(
        symbol="EURUSD", instrument_type="FOP",
        right="C", strike=strike, quantity=qty, ...
    )
```

Importable dans n'importe quel test :

```python
from tests.fixtures.positions import make_long_call
```

---

## `tests/old/` — quarantine pré-migration

Tous les tests historiques sont là en attendant d'être déplacés. Trois actions possibles par fichier :

1. **Promote en unit** : si pas d'I/O → `git mv tests/old/test_X.py tests/unit/<module>/`
2. **Promote en integration** : si I/O → `git mv tests/old/test_X.py tests/integration/<pipeline>/`
3. **Drop** : si test obsolète/redondant → supprimer

À faire **post-R8** (PR dédiée) pour ne pas casser les rebases en cours dans la queue PLAYBOOK.

---

## Configuration pytest (à poser plus tard)

```toml
# pyproject.toml ou pytest.ini

[tool.pytest.ini_options]
testpaths = ["tests/unit", "tests/integration"]   # ignore tests/old
addopts = "--strict-markers -ra"
markers = [
    "integration: requires real Postgres/Redis/IB-stub (slow)",
    "live_ib: requires real IB Gateway connection (manual only, IB_RUN_INTEGRATION=1)",
]
```

CI split (`.github/workflows/build.yml` à updater) :

```yaml
- name: Unit tests (fast)
  run: python -m pytest tests/unit -v

- name: Integration tests (slow)
  if: github.ref == 'refs/heads/main' || contains(github.event.pull_request.labels.*.name, 'run-integration')
  run: python -m pytest tests/integration -v -m integration
```

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

# Smoke notebooks — hors pytest
jupyter lab scripts/smoke/postgresql/02_setup.ipynb
python scripts/smoke/ib-gateway/01_test_connection.py
```

---

## Décision de design (FAQ)

**Q : Pourquoi pas `tests/smoke/` en pytest ?**
A : Les smoke tests du projet sont **interactifs** (notebooks avec OK/FAIL en sortie + troubleshooting markdown). Recoder ça en pytest serait redondant. Garde la séparation : pytest = automatique CI-friendly, notebooks = inspection manuelle.

**Q : Que faire si un test est unit ET integration ?**
A : Probablement deux tests distincts. La couche unit mock les I/O ; la couche integration les exerce vraiment. Si tu peux pas séparer, classe en integration (le filet de sécurité plus large l'emporte).

**Q : `tests/unit/services/` est obligatoire ou je peux flatter en `tests/unit/{db_writer,market_data,risk,vol}/` ?**
A : Reflète `src/`. `src/services/` existe pour grouper les 4 engines, donc `tests/unit/services/` aussi. Cohérence du mapping path-to-path > économie de profondeur.
