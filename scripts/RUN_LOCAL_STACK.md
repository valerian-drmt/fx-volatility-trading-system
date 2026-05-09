# Run the local stack — build & visualize

Pas-à-pas pour builder et démarrer chaque brique du système en local sur Windows (PowerShell ou Git Bash). Cette branche (`feat/r5-dockerfile-web-schema-guard`) contient tout R1-R5 : Postgres + Alembic, Redis bus, FastAPI, frontend React, Nginx, Dockerfile.web.

## Pré-requis

- Docker Desktop (Windows) — doit tourner avant toute commande `docker`
- Node 20 LTS — pour le frontend dev server
- Python 3.11 + venv activé (voir section 0)

---

## 0. Setup Python venv (une seule fois puis activation par session)

### PowerShell
```powershell
# Une seule fois (à la racine du repo)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# À chaque nouvelle session
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
$env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
$env:REDIS_URL = "redis://localhost:6380/0"
```

---

## 1. Postgres

```bash
docker compose -f docker-compose.dev.yml up -d postgres
docker compose -f docker-compose.dev.yml ps
# → fxvol-postgres-dev, state healthy, host port 5433 → container 5432
```

Appliquer les migrations Alembic :
```bash
python -m alembic -c persistence/alembic.ini upgrade head
# wrapper équivalent :
python scripts/db_apply.py
```

Autres wrappers :
```bash
python scripts/db_rollback.py                   # downgrade -1
python scripts/db_reset.py                      # drop all + upgrade head (dev only)
```

Créer une nouvelle révision (autogenerate) — **ordre strict** :
```bash
# 1. La DB doit déjà être à head (sinon erreur "Target database is not up to date")
python scripts/db_apply.py

# 2. Modifier les models ORM dans src/persistence/models/

# 3. Générer la révision
python scripts/db_new_revision.py "add col X"

# 4. Relire/éditer la migration dans persistence/migrations/versions/

# 5. Appliquer
python scripts/db_apply.py
```

Vérifier :
```bash
docker compose -f docker-compose.dev.yml exec postgres psql -U fxvol -d fxvol -c "\dt"
```

Shutdown :
```bash
docker compose -f docker-compose.dev.yml down          # garde les volumes
docker compose -f docker-compose.dev.yml down -v       # drop la data
```

---

## 2. Redis

```bash
docker compose -f docker-compose.dev.yml up -d redis
```

---

## 3. FastAPI backend

```bash
$env:PYTHONPATH = "src" 
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

Endpoints utiles :
- <http://localhost:8000/docs> — Swagger UI
- <http://localhost:8000/openapi.json> — schéma OAS 3.1
- <http://localhost:8000/metrics> — Prometheus
- <http://localhost:8000/api/v1/health> — liveness
- <http://localhost:8000/api/v1/health/extended> — DB + Redis + heartbeats

### En container
R6 ajoutera `Dockerfile.api`. En attendant, l'API tourne en process local contre le compose.

---

## 4. Frontend React

### Mode dev (HMR, Vite dev server) — usage quotidien
```bash
cd frontend
npm ci                        # une seule fois
npm run dev
```
→ <http://localhost:5173> (Vite proxy `/api` et `/ws` vers FastAPI :8000)

### Container (Dockerfile.web + Nginx) — simuler la prod
```bash
docker build -f infrastructure/docker/Dockerfile.web -t fx-options-frontend:local .
docker run --rm -d --name fxfrontend -p 8080:8080 fx-options-frontend:local
# → http://localhost:8080 (Nginx sert index.html + assets hashés, SPA fallback actif)
docker stop fxfrontend
```

Régénérer les types TS depuis FastAPI (si le backend a changé) :
```bash
# FastAPI doit tourner (section 3)
cd frontend
npm run gen:api               # écrit src/api/schema.d.ts
npm run gen:api:check         # exit 1 si drift
```

---

## 5. Stack complet (3 terminaux — R6 livrera un compose orchestré)

```bash
# Terminal 1 — services
docker compose -f docker-compose.dev.yml up -d
python -m alembic -c persistence/alembic.ini upgrade head    # 1ère fois uniquement

# Terminal 2 — API
powershell scripts/run_api.ps1

# Terminal 3 — frontend
cd frontend && npm run dev
```

Navigateur → <http://localhost:5173>.

---

## 6. Visualiser le frontend — checklist rapide

| URL | Attendu | Si ça ne marche pas |
|---|---|---|
| <http://localhost:5173> | Dashboard React (header + 9 panels) | Vérifier que `npm run dev` tourne |
| <http://localhost:5173/api/v1/health> | `{"status":"OK"}` (proxifié) | FastAPI arrêté → lancer `scripts/run_api.ps1` |
| Dot statut en header | Vert `open` / Jaune `connecting|retry` / Rouge `closed` | FastAPI ou Redis down → `docker compose ps` |
| `StatusPanel` Ticks count | S'incrémente à chaque `redis-cli PUBLISH ticks …` | Redis down ou bridge WS inactif |

**Flux de démo type** (~30s) :
```bash
# T1
docker compose -f docker-compose.dev.yml up -d
python -m alembic -c persistence/alembic.ini upgrade head
powershell scripts/run_api.ps1

# T2
cd frontend && npm run dev
# → ouvrir http://localhost:5173

# T3 — simuler un tick
docker compose -f docker-compose.dev.yml exec redis redis-cli PUBLISH ticks '{"symbol":"EURUSD","bid":1.0849,"ask":1.0851,"mid":1.085}'
# → StatusPanel Ticks: 1, Bid/Ask/Mid remplis
```

---

## 7. Tests

### Backend Python
```bash
python -m pytest                                    # unit + fast
WEB_RUN_INTEGRATION=1 python -m pytest tests/test_openapi_schema_stable.py   # drift guard
```

### Frontend Vitest (unit + composants)
```bash
cd frontend
npm run test                                        # 56 tests vitest
npm run test:coverage                               # rapport HTML dans coverage/
```

### Frontend Playwright (e2e, zero backend)
```bash
cd frontend
npm run build
npm run test:e2e                                    # 6 tests chromium en ~5s
npx playwright test --ui                            # debug visuel interactif
```

### Nginx configs
```bash
python -m pytest tests/test_nginx_config_syntax.py  # 3 tests parse
docker run --rm -v "$PWD/infrastructure/nginx:/conf:ro" nginx:alpine \
  sh -c "printf 'events{}\nhttp{\n  include /conf/nginx-dev.conf;\n}\n' > /tmp/n.conf && nginx -t -c /tmp/n.conf"
```

---

## 8. Smoke notebooks

```bash
jupyter lab scripts/redis_bus_smoke.ipynb          # flux tick → Redis → WS
jupyter lab scripts/fastapi_smoke.ipynb            # tous les endpoints REST
```

---

## 9. Cleanup complet

```bash
docker compose -f docker-compose.dev.yml down -v       # drop postgres + redis + volumes
docker rmi fx-options-frontend:local                    # si image build localement
rm -rf frontend/node_modules frontend/dist frontend/playwright-report
```
