# Run & verify the local v2 stack

Guide de vérification pas-à-pas pour tester **tous les liens** entre les 10 containers. Windows / PowerShell.

Stack (10 services) : `postgres` · `redis` · `api` · `db-writer` · `market-data` · `vol-engine` · `risk-engine` · `frontend` · `nginx` · `ib-gateway`.

Un seul compose : `docker-compose.yml`. Un override automatique `docker-compose.override.yml` expose Postgres/Redis sur l'host en dev. En prod on lance avec `-f docker-compose.yml` pour ignorer l'override.

---

## Règle sécurité : zéro exposition des secrets en clair

Les secrets (`IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`) vivent
dans AWS SSM Parameter Store et sont chargés en RAM par `load_secrets.ps1`.
**Aucun `.env` n'est écrit sur disque** (depuis R9 commit #3).

Règles opérationnelles :

- **Jamais** `echo $env:IB_PASSWORD` ni équivalent — le secret apparaîtrait dans
  le `PSReadLine` history (`Get-PSReadlineOption`.HistorySavePath) et dans le
  scrollback de la fenêtre
- **Jamais** `cat .env`, `Get-Content .env`, `printenv`, `Get-ChildItem Env:` —
  ces commandes dumpent tous les secrets d'un coup
- **Jamais** `aws ssm get-parameter ... --with-decryption` sans `--query` qui
  exclut le champ `Value` (par défaut la valeur remonte dans la sortie JSON)

Pour vérifier qu'un secret est chargé sans l'afficher :
```powershell
if ($env:IB_PASSWORD) { "set, $($env:IB_PASSWORD.Length) chars" } else { "MISSING" }
```

Pour vérifier qu'un paramètre SSM existe sans sa valeur :
```powershell
aws ssm get-parameter --name /fxvol/prod/IB_USERID `
    --query 'Parameter.Name' --output text --profile fxvol-dev
```

Un hook Claude Code (`scripts/hooks/block_secrets.ps1`) bloque automatiquement
ces commandes si elles sont tentées par un outil. Voir `.claude/settings.local.json`.

Si un secret apparaît accidentellement dans un terminal ou un log :
1. Rotation immédiate : `.\scripts\put_secrets.ps1 -Only <NAME>`
2. Purge du `PSReadLine` history : `Clear-History; Remove-Item (Get-PSReadlineOption).HistorySavePath`
3. Fermer toutes les fenêtres qui ont vu la valeur

---

## 0. Prérequis

### 0.a Setup AWS CLI (une seule fois)

Les secrets (`IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`,
`TRADING_MODE`) vivent dans AWS SSM Parameter Store (`/fxvol/prod/*`) et sont
chargés en RAM par `scripts/load_secrets.ps1`. **Aucun `.env` sur disque.**

Bootstrap AWS (compte, KMS CMK, IAM user, SSM params) : voir
`infrastructure/aws/secrets-bootstrap.md`. Une fois fait, configurer le profil
`fxvol-dev` avec les access keys IAM :

```powershell
aws configure --profile fxvol-dev
# AWS Access Key ID     : <fourni lors de la création de l'IAM user>
# AWS Secret Access Key : <idem>
# Default region        : eu-west-1
# Default output format : json
```

Premier push des vraies valeurs dans SSM (une fois aussi, ou pour rotation) :

```powershell
.\scripts\put_secrets.ps1     # prompt SecureString pour IB_USERID/IB_PASSWORD/DB_PASSWORD/VNC_PASSWORD
```

Vérifier le round-trip **sans révéler la valeur** :
```powershell
aws ssm get-parameter --name /fxvol/prod/IB_USERID --with-decryption `
    --profile fxvol-dev --region eu-west-1 `
    --query 'length(Parameter.Value)' --output text
# → sortie : la longueur de la valeur (prouve déchiffrement + présence)
```

### 0.b Session quotidienne

Docker Desktop (WSL2 backend) + Python 3.11 + Node 20. Bloc unique à exécuter à
chaque session PowerShell (le `venv` + `pip install` ne sont utiles qu'à la
première) :

```powershell
cd "$HOME\Documents\Python Project\fx-volatility-trading-system"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
.\scripts\load_secrets.ps1    # fetch SSM → $env:IB_USERID/IB_PASSWORD/DB_PASSWORD/VNC_PASSWORD/TRADING_MODE
                              # + dérive DATABASE_URL, REDIS_URL, PYTHONPATH
```

`load_secrets.ps1` affiche `Loaded 5 secrets from SSM into shell env` si tout
va bien. Si un SSM param est encore à la valeur `PLACEHOLDER_TO_REPLACE`, il
le signale : relancer `put_secrets.ps1`. Si le profil AWS n'est pas utilisable
(access keys révoquées, SSO expiré), il affiche l'action corrective et `throw`.

Les secrets vivent **uniquement dans la session PowerShell courante**. Fermer
la fenêtre = les secrets quittent la RAM. La fenêtre suivante doit relancer
`load_secrets.ps1` (ou lancer `start_stack.ps1` qui le fait automatiquement).

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

## 1. One-shot quotidien — `start_stack.ps1` (**workflow recommande**)

Une seule commande qui : (1) build + lance les 10 containers, (2) joue `alembic upgrade head`, (3) ouvre Windows Terminal avec **un tab `logs -f` par service** (10 tabs).

```powershell
.\scripts\start_stack.ps1              # build + up + logs live
.\scripts\start_stack.ps1 -NoBuild     # up + logs live, sans rebuild (plus rapide)
```

C'est le script a lancer chaque matin. Les tabs restent ouverts en streaming, `Ctrl+C` dans un tab stoppe juste le `logs -f` (le container continue).

Pour arreter la stack a la fin de la journee :
```powershell
docker compose --profile engines --profile ib down
```

---

## 1.bis Lancer tout en un coup (variante manuelle)

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

## 1.ter Healthcheck global — tester tous les containers

Commandes **read-only** uniquement. Rien ne build, rien ne modifie d'état. Chaque ligne a sa sortie attendue en commentaire.

```powershell
docker compose ps                                                                                   # 10 containers Up (healthy)/(running)
docker compose exec postgres pg_isready -U fxvol -d fxvol                                           # accepting connections
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT version_num FROM alembic_version;"   # hash Alembic
docker compose exec redis redis-cli PING                                                            # PONG
docker exec fxvol-api curl -fsS http://127.0.0.1:8000/api/v1/health                                 # {"status":"ok"}
docker exec fxvol-frontend wget -qO- http://127.0.0.1:8080/ | Select-Object -First 5                # HTML <div id="root">
curl.exe -I http://localhost/                                                                       # HTTP/1.1 200 OK
curl.exe http://localhost/api/v1/health                                                             # {"status":"ok"}
curl.exe http://localhost/api/v1/health/extended                                                    # {"status":"ok","database":"ok","redis":"ok"}
Test-NetConnection 127.0.0.1 -Port 4002 | Select-Object TcpTestSucceeded                            # True
docker compose exec redis redis-cli GET heartbeat:market_data                                       # timestamp Unix récent
docker compose exec redis redis-cli GET heartbeat:vol_engine                                        # timestamp Unix récent
docker compose exec redis redis-cli GET heartbeat:risk_engine                                       # timestamp Unix récent
docker compose exec redis redis-cli GET heartbeat:db_writer                                         # timestamp Unix récent
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT COUNT(*) FROM position_snapshots;"   # count > 0
```

---

## 2. Lancer + vérifier chaque container

Chaque sous-section suit le **même triptyque** :
1. **Lancer** — `up -d --build` (rebuild systématique) + ligne de vérif OK
2. **Logs** — 3 variantes : live / 50 dernières lignes / tous les logs
3. **Arrêter** — stop (sans supprimer) ou rm -sf (supprime le container)

Les vérifs métier spécifiques (PyCharm, Alembic, endpoints, pub/sub, heartbeats) restent à la fin de chaque sous-section.

---

### 2.1 Postgres

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose up -d --build postgres
docker compose exec postgres pg_isready -U fxvol -d fxvol
# attendu : "accepting connections"
```

**2) Logs**
```powershell
docker compose logs -f postgres             # live
docker compose logs --tail=50 postgres      # 50 dernières lignes
docker compose logs postgres                # tous les logs
```
À chercher : `database system is ready to accept connections`, pas de `FATAL`.

**3) Arrêter**
```powershell
docker compose stop postgres                # arrête, garde le container + volume
docker compose rm -sf postgres              # stop + supprime le container (volume conservé)
```

**Vérifs métier**

Appliquer les migrations Alembic une fois postgres sain :
```powershell
python -m alembic -c persistence/alembic.ini upgrade head
# ou depuis le container api s'il tourne :
docker compose exec api python -m alembic -c persistence/alembic.ini upgrade head
```

Vérifier les tables créées :
```powershell
docker compose exec postgres psql -U fxvol -d fxvol -c "\dt"
# attendu : 8 tables — positions, position_snapshots, signals, vol_surfaces,
#           trades, account_snaps, backtest_runs, alembic_version
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT version_num FROM alembic_version;"
```

Se connecter depuis PyCharm (Database panel) : Host `localhost` · Port `5433` · DB `fxvol` · User `fxvol` · Password `fxvol` → Test Connection.

psql direct depuis l'host (si installé) :
```powershell
psql -h localhost -p 5433 -U fxvol -d fxvol
```

---

### 2.2 Redis

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose up -d --build redis
docker compose exec redis redis-cli PING
# attendu : PONG
```

**2) Logs**
```powershell
docker compose logs -f redis                # live
docker compose logs --tail=50 redis         # 50 dernières lignes
docker compose logs redis                   # tous les logs
```

**3) Arrêter**
```powershell
docker compose stop redis
docker compose rm -sf redis
```

**Vérifs métier**

Alias PowerShell pratique (à mettre dans `$PROFILE`) :
```powershell
function redis-cli { docker compose exec redis redis-cli @args }
redis-cli PING
```

Inspecter les clés :
```powershell
docker compose exec redis redis-cli KEYS '*'
docker compose exec redis redis-cli KEYS 'heartbeat:*'
docker compose exec redis redis-cli GET heartbeat:market_data
```

Suivre le pub/sub (preuve du bus) :
```powershell
docker compose exec redis redis-cli SUBSCRIBE ticks vol_update risk_update
# dans un autre terminal :
docker compose exec redis redis-cli PUBLISH ticks.EURUSD '{"bid":1.08,"ask":1.081}'
```

---

### 2.3 API (FastAPI)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose up -d --build api
docker exec fxvol-api curl -fsS http://127.0.0.1:8000/api/v1/health
# attendu : {"status":"ok"}
```

**2) Logs**
```powershell
docker compose logs -f api                  # live
docker compose logs --tail=50 api           # 50 dernières lignes
docker compose logs api                     # tous les logs
```
À chercher : `Application startup complete`, `Uvicorn running on http://0.0.0.0:8000`.

**3) Arrêter**
```powershell
docker compose stop api
docker compose rm -sf api
```

**Vérifs métier**

Migrations Alembic depuis le container :
```powershell
docker compose exec api python -m alembic -c persistence/alembic.ini upgrade head
```

Endpoints clés (via nginx une fois § 2.5 lancé, sinon via `docker exec`) :

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

Lien API ↔ Postgres ↔ Redis :
```powershell
curl.exe http://localhost/api/v1/health/extended
# attendu : {"status":"ok","database":"ok","redis":"ok"}
```

---

### 2.4 Frontend (React bundle)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose up -d --build frontend
docker exec fxvol-frontend wget -qO- http://127.0.0.1:8080/ | Select-Object -First 20
# attendu : HTML avec <div id="root">
```
Le frontend écoute en **interne** sur 8080 — joignable depuis le navigateur uniquement via nginx (§ 2.5).

**2) Logs**
```powershell
docker compose logs -f frontend             # live
docker compose logs --tail=50 frontend      # 50 dernières lignes
docker compose logs frontend                # tous les logs
```

**3) Arrêter**
```powershell
docker compose stop frontend
docker compose rm -sf frontend
```

---

### 2.5 Nginx (reverse proxy public :80)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose up -d --build nginx
curl.exe -I http://localhost/
# attendu : HTTP/1.1 200 OK, Content-Type: text/html
curl.exe http://localhost/api/v1/health
# attendu : {"status":"ok"}
```
Dashboard complet : **<http://localhost/>**

**2) Logs**
```powershell
docker compose logs -f nginx                # live (chaque ligne = une requête proxyée)
docker compose logs --tail=50 nginx         # 50 dernières lignes
docker compose logs nginx                   # tous les logs
```

**3) Arrêter**
```powershell
docker compose stop nginx
docker compose rm -sf nginx
```

---

### 2.6 IB Gateway (profile `ib`, credentials requis)

Ne démarre qu'avec `IB_USERID` + `IB_PASSWORD` non vides dans `.env`.

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose --profile ib up -d --build ib-gateway
Test-NetConnection 127.0.0.1 -Port 4002
# attendu : TcpTestSucceeded : True
```

**2) Logs**
```powershell
docker compose logs -f ib-gateway           # live
docker compose logs --tail=50 ib-gateway    # 50 dernières lignes
docker compose logs ib-gateway              # tous les logs
```

**3) Arrêter**
```powershell
docker compose stop ib-gateway
docker compose rm -sf ib-gateway
```

**Vérifs métier**

Voir l'écran du Gateway via VNC (diagnostic si login bloque — pas de 2FA en paper) :
```
VNC viewer → vnc://127.0.0.1:5900
Password : valeur de $env:VNC_PASSWORD (défaut local-dev)
```

---

### 2.7 Market Data engine (profile `engines`)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose --profile engines up -d --build market-data
docker compose exec redis redis-cli GET heartbeat:market_data
# attendu : timestamp Unix récent (now - value < 60s)
```

**2) Logs**
```powershell
docker compose logs -f market-data          # live
docker compose logs --tail=50 market-data   # 50 dernières lignes
docker compose logs market-data             # tous les logs
```
À chercher : `Connected to IB gateway`, `Publishing tick for EURUSD`, heartbeat cyclique.

**3) Arrêter**
```powershell
docker compose stop market-data
docker compose rm -sf market-data
```

**Vérifs métier**

Preuve du pipeline market-data → Redis :
```powershell
docker compose exec redis redis-cli SUBSCRIBE ticks
# messages {bid, ask, mid, ts} apparaissent si IB Gateway est connecté
```

---

### 2.8 Vol engine (profile `engines`)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose --profile engines up -d --build vol-engine
docker compose exec redis redis-cli GET heartbeat:vol_engine
# attendu : timestamp Unix récent
```

**2) Logs**
```powershell
docker compose logs -f vol-engine           # live
docker compose logs --tail=50 vol-engine    # 50 dernières lignes
docker compose logs vol-engine              # tous les logs
```
À chercher : `GARCH fit`, `BS inversion for 80 strikes`, heartbeat.

**3) Arrêter**
```powershell
docker compose stop vol-engine
docker compose rm -sf vol-engine
```

**Vérifs métier**

Lien vol-engine → Redis (publication surface vol) :
```powershell
docker compose exec redis redis-cli SUBSCRIBE vol_update
# attendu : messages vol.surface, vol.term_structure toutes les 30s
```

---

### 2.9 Risk engine (profile `engines`)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose --profile engines up -d --build risk-engine
docker compose exec redis redis-cli GET heartbeat:risk_engine
# attendu : timestamp Unix récent
```

**2) Logs**
```powershell
docker compose logs -f risk-engine          # live
docker compose logs --tail=50 risk-engine   # 50 dernières lignes
docker compose logs risk-engine             # tous les logs
```

**3) Arrêter**
```powershell
docker compose stop risk-engine
docker compose rm -sf risk-engine
```

**Vérifs métier** — risk publie sur `risk_update` et insère des positions via le db-writer.

---

### 2.10 DB Writer (profile `engines`)

**1) Lancer (rebuild + up + vérif)**
```powershell
docker compose --profile engines up -d --build db-writer
docker compose exec redis redis-cli GET heartbeat:db_writer
# attendu : timestamp Unix récent
```

**2) Logs**
```powershell
docker compose logs -f db-writer            # live
docker compose logs --tail=50 db-writer     # 50 dernières lignes
docker compose logs db-writer               # tous les logs
```
À chercher : `batch inserted N rows`, heartbeat.

**3) Arrêter**
```powershell
docker compose stop db-writer
docker compose rm -sf db-writer
```

**Vérifs métier**

Preuve du lien writer ↔ Postgres :
```powershell
# attendre ~1 min que l'engine ait écrit des snapshots, puis :
docker compose exec postgres psql -U fxvol -d fxvol -c "SELECT COUNT(*) FROM position_snapshots;"
# attendu : count > 0 qui augmente au cours du temps
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
