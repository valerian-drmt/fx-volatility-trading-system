# Run & verify the local v2 stack

Guide de vérification pas-à-pas pour tester **tous les liens** entre les 10 containers. Windows / PowerShell.

Stack (10 services) : `postgres` · `redis` · `api` · `db-writer` · `market-data` · `vol-engine` · `risk-engine` · `frontend` · `nginx` · `ib-gateway`.

Un seul compose : `docker-compose.yml`. Un override automatique `docker-compose.override.yml` expose Postgres/Redis sur l'host en dev. En prod on lance avec `-f docker-compose.yml` pour ignorer l'override.

---

## 0. Prérequis

Docker Desktop (WSL2 backend) + Python 3.11 + Node 20. Setup venv une fois :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Variables d'env à chaque session PowerShell :

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

Table des ports exposés sur l'host en dev :

| Service | Port host | Port container | Usage |
|---|---|---|---|
| postgres | **5433** | 5432 | PyCharm Database, `psql` |
| redis | **6380** | 6379 | `redis-cli` |
| nginx | **80** / 443 | 80 / 443 | navigateur, front + api |
| ib-gateway (TWS) | **4002** | 4002 | `ib_insync` hors docker |
| ib-gateway (VNC) | **5900** | 5900 | VNC viewer |

API, engines, db-writer, frontend ne sont **pas** exposés sur l'host — ils sont joignables uniquement via nginx (`http://localhost/api/...`) ou via `docker exec`.

---

## 1. Lancer tout en un coup (option rapide)

```powershell
docker compose --profile engines --profile ib up -d --build
docker compose exec api python -m alembic -c persistence/alembic.ini upgrade head
docker compose ps
```

Statut attendu : 10 containers `Up (healthy)` ou `Up (running)`.

Arrêt :

```powershell
docker compose --profile engines --profile ib down             # coupe tout
docker compose --profile engines --profile ib down --volumes   # + purge DB/Redis
```

Pour la vérif pipeline détaillée, préférer la section § 2 (lancement service par service).

---

## 2. Lancer + vérifier chaque container

### 2.1 Postgres

**Lancer**
```powershell
docker compose up -d postgres
docker compose exec postgres pg_isready -U fxvol -d fxvol
# attendu : "accepting connections"
```

**Appliquer les migrations Alembic** (une fois le container sain)
```powershell
# depuis l'host via le venv :
python -m alembic -c persistence/alembic.ini upgrade head
# OU depuis le container api s'il tourne :
docker compose exec api python -m alembic -c persistence/alembic.ini upgrade head
```

**Se connecter depuis PyCharm (Database panel)**
1. `View → Tool Windows → Database` → `+` → `Data Source` → `PostgreSQL`
2. Remplir :
   - Host : `localhost`
   - Port : `5433`
   - Database : `fxvol`
   - User : `fxvol`
   - Password : `fxvol`
   - URL auto : `jdbc:postgresql://localhost:5433/fxvol`
3. Cliquer **Test Connection** (PyCharm télécharge le driver JDBC si absent)
4. **OK** — le schéma `public` apparaît dans le panel

**Vérifier les tables créées par Alembic**
```powershell
docker compose exec postgres psql -U fxvol -d fxvol -c "\dt"
# attendu : 8 tables — positions, position_snapshots, signals, vol_surfaces,
#           trades, account_snaps, backtest_runs, alembic_version
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT version_num FROM alembic_version;"
# attendu : hash Alembic de la dernière migration (ex: 254fc54bb36f)
```

**Logs**
```powershell
docker compose logs postgres              # tous les logs
docker compose logs -f postgres           # follow (live)
docker compose logs --tail=50 postgres    # 50 dernières lignes
```

À chercher : `database system is ready to accept connections`, pas d'erreur FATAL.

**psql direct depuis l'host** (si `psql` est installé localement)
```powershell
psql -h localhost -p 5433 -U fxvol -d fxvol
```

---

### 2.2 Redis

**Lancer**
```powershell
docker compose up -d redis
docker compose exec redis redis-cli PING
# attendu : PONG
```

**Se connecter depuis l'host** — `redis-cli` n'étant pas fourni sur Windows, deux options :

1. **Préfixer par `docker compose exec redis`** (pas d'install) :
   ```powershell
   docker compose exec redis redis-cli -h localhost -p 6379 PING
   ```
2. **Créer un alias PowerShell** (à mettre dans `$PROFILE` pour être permanent) :
   ```powershell
   function redis-cli { docker compose exec redis redis-cli @args }
   redis-cli PING                          # marche depuis l'host
   redis-cli GET heartbeat:market_data
   ```

Avec l'alias, toutes les commandes `redis-cli ...` du reste du doc fonctionnent directement.

**Inspecter les clés**
```powershell
docker compose exec redis redis-cli KEYS '*'
docker compose exec redis redis-cli KEYS 'heartbeat:*'
docker compose exec redis redis-cli GET heartbeat:market_data
```

**Suivre le pub/sub (preuve du bus)**
```powershell
# dans un terminal : abonnement
docker compose exec redis redis-cli SUBSCRIBE ticks vol_update risk_update
# dans un autre : publication test
docker compose exec redis redis-cli PUBLISH ticks.EURUSD '{"bid":1.08,"ask":1.081}'
```

**Logs**
```powershell
docker compose logs -f redis
```

---

### 2.3 API (FastAPI)

**Lancer**
```powershell
docker compose up -d --build api
# appliquer les migrations si pas déjà fait :
docker compose exec api python -m alembic -c persistence/alembic.ini upgrade head
```

**Vérifier que l'API répond (depuis le container, avant nginx)**
```powershell
docker exec fxvol-api curl -fsS http://127.0.0.1:8000/api/v1/health
# attendu : {"status":"ok"}
```

**Endpoints clés (à tester via nginx une fois § 2.5 lancé, sinon via `docker exec` comme ci-dessus)**

| Endpoint | Ce qu'il fait | Comment tester |
|---|---|---|
| `GET /api/v1/health` | Liveness — ne touche ni DB ni Redis | `curl http://localhost/api/v1/health` |
| `GET /api/v1/health/extended` | **Prouve le lien DB+Redis** | `curl http://localhost/api/v1/health/extended` |
| `GET /api/docs` | Swagger UI interactif | navigateur |
| `GET /api/redoc` | Redoc statique | navigateur |
| `GET /api/openapi.json` | Spec OpenAPI brute | `curl -o openapi.json http://localhost/api/openapi.json` |
| `GET /api/v1/pricing/price?...` | Pricer BS | Swagger → "Try it out" |
| `GET /api/v1/vol/surface` | Dernière surface vol | idem |
| `GET /api/v1/portfolio/positions` | Positions depuis Postgres | idem |
| `WS /api/v1/ws/ticks` | WebSocket ticks | voir § 3.4 |

**Swagger** — le meilleur outil pour tester tous les endpoints sans écrire de curl : <http://localhost/api/docs>, chaque route a un bouton "Try it out".

**Logs**
```powershell
docker compose logs -f api
# à chercher :
# - "Application startup complete" au démarrage
# - "Uvicorn running on http://0.0.0.0:8000"
# - Les lignes des requêtes entrantes
```

**Lien API ↔ Postgres**
```powershell
# endpoint dédié qui PING la DB :
curl.exe http://localhost/api/v1/health/extended
# attendu : {"status":"ok","database":"ok","redis":"ok"}
# si database = "error" → API ne parle plus à postgres (vérif DATABASE_URL + alembic)
```

**Lien API ↔ Redis** : même endpoint `/health/extended` ci-dessus (champ `redis`).

---

### 2.4 Frontend (React bundle)

**Lancer**
```powershell
docker compose up -d --build frontend
```

**Vérifier le bundle servi**
```powershell
docker exec fxvol-frontend wget -qO- http://127.0.0.1:8080/ | Select-Object -First 20
# attendu : HTML avec <div id="root">
```

Le frontend écoute en **interne** sur le port 8080 — il n'est joignable depuis le navigateur qu'après le proxy nginx (§ 2.5).

**Logs**
```powershell
docker compose logs -f frontend
```

---

### 2.5 Nginx (reverse proxy public :80)

**Lancer**
```powershell
docker compose up -d nginx
```

**Tester la route frontend**
```powershell
curl.exe -I http://localhost/
# attendu : HTTP/1.1 200 OK, Content-Type: text/html
```

**Tester la route API via nginx (proxy)**
```powershell
curl.exe http://localhost/api/v1/health
# attendu : {"status":"ok"}
```

Dashboard complet dans le navigateur : **<http://localhost/>**

**Logs**
```powershell
docker compose logs -f nginx
# chaque ligne = une requête HTTP proxyée
```

---

### 2.6 IB Gateway (profile `ib`, credentials requis)

**Lancer** — ne démarre qu'avec `IB_USERID` + `IB_PASSWORD` non vides dans `.env`
```powershell
docker compose --profile ib up -d ib-gateway
docker compose logs -f ib-gateway
```

**Vérifier que le port TWS API est ouvert**
```powershell
Test-NetConnection 127.0.0.1 -Port 4002
# attendu : TcpTestSucceeded : True
```

**Voir l'écran du Gateway via VNC**
```
VNC viewer → vnc://127.0.0.1:5900
Password : valeur de $env:VNC_PASSWORD (défaut local-dev)
```

Utile pour déverrouiller un prompt 2FA ou diagnostiquer un login bloqué.

---

### 2.7 Market Data engine (profile `engines`)

**Lancer**
```powershell
docker compose --profile engines up -d --build market-data
```

**Vérifier le heartbeat (preuve du lien engine ↔ Redis)**
```powershell
docker compose exec redis redis-cli GET heartbeat:market_data
# attendu : timestamp Unix récent (now - value < 60s)
```

**Vérifier que les ticks sont publiés (preuve du pipeline market-data → Redis)**
```powershell
docker compose exec redis redis-cli SUBSCRIBE ticks
# laisser tourner → des messages {bid, ask, mid, ts} apparaissent si IB Gateway est connecté
```

**Logs**
```powershell
docker compose logs -f market-data
# à chercher :
# - "Connected to IB gateway"
# - "Publishing tick for EURUSD"
# - heartbeat cyclique
```

---

### 2.8 Vol engine (profile `engines`)

**Lancer**
```powershell
docker compose --profile engines up -d --build vol-engine
```

**Heartbeat**
```powershell
docker compose exec redis redis-cli GET heartbeat:vol_engine
```

**Lien vol-engine → Redis (publication surface vol)**
```powershell
docker compose exec redis redis-cli SUBSCRIBE vol_update
# attendu : messages vol.surface, vol.term_structure toutes les 30s
```

**Logs**
```powershell
docker compose logs -f vol-engine
# à chercher : "GARCH fit", "BS inversion for 80 strikes", heartbeat
```

---

### 2.9 Risk engine (profile `engines`)

```powershell
docker compose --profile engines up -d --build risk-engine
docker compose exec redis redis-cli GET heartbeat:risk_engine
docker compose logs -f risk-engine
```

**Lien risk → Redis + Postgres** : risk publie sur `risk_update` et insère des positions via le db-writer.

---

### 2.10 DB Writer (profile `engines`)

**Lancer**
```powershell
docker compose --profile engines up -d --build db-writer
docker compose exec redis redis-cli GET heartbeat:db_writer
```

**Preuve du lien writer ↔ Postgres**
```powershell
# attendre une minute que l'engine ait écrit des snapshots, puis :
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT COUNT(*) FROM position_snapshots;"
# attendu : count > 0 qui augmente au cours du temps
```

**Logs**
```powershell
docker compose logs -f db-writer
# à chercher : "batch inserted N rows", heartbeat
```

---

## 3. Tests de liens end-to-end

### 3.1 Lien complet IB → market-data → Redis → vol-engine → API → frontend

1. Ouvrir le dashboard : <http://localhost/>
2. Dans un autre terminal :
   ```powershell
   docker compose exec redis redis-cli SUBSCRIBE ticks vol_update
   ```
3. Attendre que des messages apparaissent (IB Gateway connecté)
4. Le dashboard doit afficher les ticks **et** la surface vol en temps réel
5. Si les ticks arrivent sur Redis mais pas sur le front → problème côté API WebSocket ou frontend hook

### 3.2 Lien complet risk → db-writer → Postgres

```powershell
# avant :
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT COUNT(*) FROM position_snapshots;"
# attendre 2 min
# après :
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT COUNT(*) FROM position_snapshots;"
# le compteur doit avoir augmenté
```

### 3.3 Healthcheck global
```powershell
curl.exe http://localhost/api/v1/health/extended
# attendu : {"status":"ok","database":"ok","redis":"ok"}
```

### 3.4 WebSocket ticks (lien API ↔ front)
Depuis le Swagger, tester `/api/v1/ws/ticks` n'est pas possible (Swagger ne fait pas WS). Utiliser :

```powershell
# avec wscat (npm install -g wscat) :
wscat -c ws://localhost/api/v1/ws/ticks
# attendu : messages JSON de ticks en continu si market-data tourne
```

---

## 4. Inspecter les containers

### Lister ce qui tourne
```powershell
docker compose ps
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Entrer dans un container (shell)
```powershell
docker compose exec api bash
docker compose exec postgres bash       # puis psql -U fxvol -d fxvol
docker compose exec redis sh            # puis redis-cli
```

### Inspecter les variables d'env d'un container
```powershell
docker exec fxvol-api printenv | sort
docker exec fxvol-postgres printenv POSTGRES_PASSWORD
```

### Voir le réseau Docker
```powershell
docker network ls
docker network inspect fx-volatility-trading-system_fxvol-internal
# liste les containers attachés + leurs IPs internes
```

### Logs agrégés (tous les services)
```powershell
docker compose logs -f                  # follow tout
docker compose logs -f --tail=20 api market-data vol-engine    # services choisis
```

---

## 5. Mode prod (override ignoré)

Pour reproduire le comportement prod (pas de ports Postgres/Redis exposés sur l'host) :

```powershell
docker compose -f docker-compose.yml --profile engines --profile ib up -d --build
```

Dans ce mode, PyCharm Database ne peut plus se connecter à Postgres directement — il faut passer par `docker compose exec postgres psql` comme en prod réelle.

---

## 6. Nettoyage disque

Après plusieurs `--build` le cache BuildKit gonfle vite (10+ GB). Les conteneurs running sont **épargnés** par les prune.

```powershell
docker system df                        # occupation courante
docker builder prune -f                 # cache BuildKit (récupère ~8-15 GB)
docker image prune -f                   # images dangling sans tag
docker image prune -a -f                # + images non utilisées par un conteneur running
```

Reset complet (⚠️ perte DB locale) :
```powershell
docker compose --profile engines --profile ib down --volumes
docker volume prune -f
```

---

## 7. Troubleshooting express

| Symptôme | Cause probable | Fix |
|---|---|---|
| PyCharm "password authentication failed" | `.env` pas chargé / volume créé avec un autre password | `docker exec fxvol-postgres printenv POSTGRES_PASSWORD` pour récupérer le vrai, ou reset volume |
| `/api/v1/health` OK, `/health/extended` KO sur database | API ne parle pas à Postgres | Vérifier `DATABASE_URL` dans le container api (`docker exec fxvol-api printenv DATABASE_URL`) |
| Pas de heartbeat sur Redis pour un engine | Engine crashé | `docker compose logs <engine>` puis redémarrer `docker compose restart <engine>` |
| 502 Bad Gateway sur <http://localhost/> | nginx tourne mais frontend ou api down | `docker compose ps` pour voir les états |
| Port 5433 already in use | un Postgres natif tourne sur l'host | `Get-NetTCPConnection -LocalPort 5433` pour identifier le process |
| IB Gateway reste "login required" | `IB_USERID`/`IB_PASSWORD` vides dans `.env` | Remplir `.env`, `docker compose restart ib-gateway` |
