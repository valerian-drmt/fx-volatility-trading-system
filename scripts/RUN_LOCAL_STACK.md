# Run the local v2 stack

Windows / PowerShell. Deux façons de lancer : **tout d'un coup** ou **container par container**.

Stack : `postgres` + `redis` + `api` + `frontend` + `nginx` + `market-data` + `vol-engine` + `risk-engine` + `db-writer` + `ib-gateway`.

---

## Quel compose utiliser ?

Le repo contient **deux fichiers compose**, à usage exclusif l'un de l'autre :

- **`docker-compose.yml`** — stack complet (10 services : nginx, api, frontend, engines, ib-gateway, postgres, redis). Seul `nginx` expose `80/443`. Postgres et Redis sont **isolés** sur le réseau `fxvol-internal` (pas de port host). → pour tester le système "prod-like" de bout en bout.
- **`docker-compose.dev.yml`** — **Postgres + Redis uniquement**, exposés sur `127.0.0.1:5433` (postgres) et `127.0.0.1:6380` (redis). → pour dev local : brancher PyCharm Database, `psql`, ou l'app PyQt v1 du host directement sur la DB/Redis.

**Règle** : tu en lances **un seul à la fois**. Les deux partagent le même volume `postgres_data` mais le container name diffère (`fxvol-postgres` vs `fxvol-postgres-dev`) — passer de l'un à l'autre sans `docker compose down` au préalable te laissera deux containers qui se marchent dessus.

**Pour brancher PyCharm → Database** :
```powershell
docker compose down                              # coupe le stack prod s'il tourne
docker compose -f docker-compose.dev.yml up -d   # lance postgres+redis exposés
```
Puis dans PyCharm : host `localhost`, port `5433`, db `fxvol`, user `fxvol`, password `fxvol`.

---

## 0. Prérequis

- Docker Desktop (WSL2 backend) lancé
- Python 3.11 + venv activé
- Node 20 (dev frontend uniquement)

Setup venv (une fois) :
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Env vars (chaque session) :
```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
$env:DB_PASSWORD = "fxvol"
$env:VNC_PASSWORD = "local-dev"
$env:IB_USERID = ""
$env:IB_PASSWORD = ""
$env:REDIS_URL = "redis://localhost:6380/0"
$env:DATABASE_URL = "postgresql+asyncpg://fxvol:fxvol@localhost:5433/fxvol"
```

---

## 1. Lancer tous les containers d'un seul coup

Stack complet (10 containers, profiles `engines` + `ib` activés) :
```powershell
docker compose --profile engines --profile ib up -d --build
```

Appliquer les migrations Alembic :
```powershell
docker compose exec api python -m alembic -c src/persistence/alembic.ini upgrade head
```

Vérifier l'état :
```powershell
docker compose ps
```

Smoke rapide :
```powershell
curl.exe -fsS http://localhost/api/v1/health
curl.exe -I http://localhost/
```

Dashboard : **<http://localhost/>**

Arrêter :
```powershell
docker compose --profile engines --profile ib down
# ou avec drop des volumes :
docker compose --profile engines --profile ib down --volumes
```

---

## 2. Lancer les containers 1 par 1

### 2.1 Postgres
```powershell
docker compose up -d postgres
docker compose exec postgres pg_isready -U fxvol -d fxvol
```

### 2.2 Redis
```powershell
docker compose up -d redis
docker compose exec redis redis-cli PING
```

### 2.3 API (FastAPI)
```powershell
$env:DB_PASSWORD = "fxvol"; $env:VNC_PASSWORD = "local-dev"                         
docker compose up -d --build --force-recreate api
docker compose exec api python -m alembic -c src/persistence/alembic.ini upgrade head
docker exec fxvol-api curl -fsS http://127.0.0.1:8000/api/v1/health
```

### 2.4 Frontend (bundle React + Nginx interne port 8080)
```powershell
docker compose up -d --build frontend
```

### 2.5 Nginx (reverse proxy public :80/:443)
```powershell
docker compose up -d nginx
curl.exe -fsS http://localhost/
curl.exe -fsS http://localhost/api/v1/health
```

### 2.6 IB Gateway (profile `ib`, requires `IB_USERID`/`IB_PASSWORD`)
```powershell
docker compose --profile ib up -d ib-gateway
docker compose logs -f ib-gateway
```

### 2.7 Market Data engine
```powershell
docker compose --profile engines up -d --build market-data
docker compose exec redis redis-cli GET heartbeat:market_data
```

### 2.8 Vol engine
```powershell
docker compose --profile engines up -d --build vol-engine
docker compose exec redis redis-cli GET heartbeat:vol_engine
```

### 2.9 Risk engine
```powershell
docker compose --profile engines up -d --build risk-engine
docker compose exec redis redis-cli GET heartbeat:risk_engine
```

### 2.10 DB Writer
```powershell
docker compose --profile engines up -d --build db-writer
docker compose exec redis redis-cli GET heartbeat:db_writer
```

---

## 3. URLs & endpoints utiles

Une fois le stack up (§ 1 ou §§ 2.1 → 2.10), tout passe par le reverse proxy nginx (port 80). Postgres et Redis ne sont PAS exposés sur le host quand tu lances `docker-compose.yml` — il faut `docker-compose.dev.yml` pour les atteindre depuis le host.

### Frontend (dashboard React)
- **<http://localhost/>** — bundle React servi par nginx (proxy sur `frontend:8080` interne)

### Backend API (FastAPI)
- **<http://localhost/api/v1/health>** — liveness probe (renvoie `{"status":"ok"}`)
- **<http://localhost/api/v1/health/extended>** — DB + Redis pings
- **<http://localhost/api/docs>** — Swagger UI (OpenAPI interactive, tous les endpoints avec "Try it out")
- **<http://localhost/api/redoc>** — Redoc (rendu statique, meilleur pour lecture)
- **<http://localhost/api/openapi.json>** — spec OpenAPI brute (utilisée par le frontend pour codegen)

### IB Gateway (profil `ib` uniquement)
- **TWS API** : `127.0.0.1:4002` (pour un client ib_insync hors Docker, ex: PyQt v1 sur le host)
- **VNC viewer** : `vnc://127.0.0.1:5900` (password = `$env:VNC_PASSWORD`, défaut `local-dev`) — utile pour voir l'écran Gateway et déverrouiller un prompt 2FA si besoin. Client VNC recommandé : TightVNC ou RealVNC.

### Postgres (host accès direct)
Seulement si tu lances le compose dev **à côté** :
```powershell
docker compose -f docker-compose.dev.yml up -d postgres
# host port 5433 → container 5432
psql -h localhost -p 5433 -U fxvol -d fxvol
```
Sinon depuis un conteneur du stack prod : `docker compose exec postgres psql -U fxvol -d fxvol`.

### Redis (host accès direct)
Idem, via le compose dev :
```powershell
docker compose -f docker-compose.dev.yml up -d redis
# host port 6380 → container 6379
redis-cli -h localhost -p 6380
```
Sinon : `docker compose exec redis redis-cli`.

### Postgres & Redis (stack prod uniquement)
Pas de port exposé. Pour les atteindre il faut passer par un conteneur du réseau `fxvol-internal` :
```powershell
docker compose exec postgres psql -U fxvol -d fxvol -c "\dt"
docker compose exec redis redis-cli KEYS 'heartbeat:*'
docker compose exec redis redis-cli GET heartbeat:market_data
```

### Engines (heartbeats Redis)
Pas d'UI, on interroge Redis :
```powershell
docker compose exec redis redis-cli GET heartbeat:market_data
docker compose exec redis redis-cli GET heartbeat:vol_engine
docker compose exec redis redis-cli GET heartbeat:risk_engine
docker compose exec redis redis-cli GET heartbeat:db_writer
```
Chaque valeur est un timestamp Unix float ; un heartbeat "frais" = `now - ts < TTL` (60s pour market-data, 300s pour vol, 30s pour risk/writer — cf. healthchecks compose).

### Docker Desktop
- UI graphique : onglet "Containers" liste les 10 services, Stats, Logs, Exec intégré.
- Nginx reçoit tout le trafic public (80/443), Postgres/Redis/engines restent invisibles à l'extérieur par design (réseau `fxvol-internal` isolé).

---

## 4. Nettoyage disque

Après plusieurs `--build` le cache BuildKit gonfle vite (11+ GB possible). Les conteneurs running sont **épargnés** par les prune, c'est safe.

Voir l'occupation courante :
```powershell
docker system df
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"
```

Purge courante (récupère typiquement 8-15 GB) :
```powershell
docker builder prune -f     # cache BuildKit (layers intermédiaires)
docker image prune -f       # images dangling sans tag
```

Purge agressive (supprime aussi les images non utilisées par un conteneur running) :
```powershell
docker image prune -a -f
```

Reset complet du stack (volumes inclus, **perte des données Postgres**) :
```powershell
docker compose --profile engines --profile ib down --volumes
docker volume prune -f
```
