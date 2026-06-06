# Run the local v2 stack

> **TL;DR** — `.\scripts\ops\start_stack.ps1` lance tout. Une seule commande.

Stack 10 containers : `postgres` · `redis` · `api` · `db-writer` · `market-data` · `vol-engine` · `risk-engine` · `frontend` · `nginx` · `ib-gateway`.

Compose unique `docker-compose.yml`, override automatique `docker-compose.override.yml` qui expose Postgres :5433 / Redis :6380 sur l'host (dev seulement). En prod, l'override n'est pas dans le clone.

---

## Commandes essentielles

Vérifier le profil AWS (one-shot, après une nouvelle machine ou des access keys expirées) :

```powershell
aws configure --profile fxvol-dev
aws sts get-caller-identity --profile fxvol-dev
cd .\Documents\'Python Project'\fx-volatility-trading-system
.\scripts\ops\start_stack.ps1
```

Lancer la stack :

```powershell
.\scripts\ops\start_stack.ps1
```

Recharger les secrets dans la session courante:

```powershell
.\scripts\ops\load_secrets.ps1
```

Tout nettoyer :

```powershell
.\scripts\ops\start_stack.ps1 -Down              # stop tout (data preservée)
```

---

## Nettoyage Docker (libérer la RAM)

Docker Desktop sur Windows accumule vite plusieurs Go entre les images de
build / les volumes anonymes / les buildx caches. Quand le ventilateur du
laptop devient bruyant, stack arrêtée, lancer le bloc ci-dessous.

> ⚠️ **Ne PAS lancer `docker image prune -af`** sans avoir d'abord rebuild
> `fxvol-ib-gateway:local` ensuite — c'est une image locale (pas pull-able
> depuis un registry), `prune -af` la supprime et au prochain `up` compose
> retombe sur l'image upstream `gnzsnz/ib-gateway:latest` régulièrement
> cassée. Cf. `infrastructure/ib-gateway/README.md` pour le rebuild.

```powershell
.\scripts\ops\start_stack.ps1 -Down       # stop stack, data preservée
docker container prune -f                 # containers exited / dead
docker image prune -f                     # dangling images uniquement (PAS -a)
docker volume prune -f                    # volumes anonymes orphelins
docker network prune -f                   # networks non rattachés
docker builder prune -af                  # tout le cache buildx (5-15 Go)
wsl --shutdown                            # rend la RAM réservée par WSL2 à Windows
```

### Limiter la RAM allouée à Docker Desktop (réglage permanent)

Docker Desktop → Settings → Resources → **Memory : 6 Go** suffit largement
pour les 10 containers (postgres + redis légers, `api` ~400 Mo, chaque
engine 200-300 Mo). Au-delà de 8 Go, c'est du gâchis pour ce projet.

### `vmmemWSL` qui squatte 3 Go en idle

Symptôme : Task Manager affiche `Vmmem` ou `vmmemWSL` à 2-4 Go alors qu'il
n'y a aucun container actif. Cause : WSL2 (le backend Linux de Docker
Desktop sur Windows) **réserve jusqu'à 50 % de la RAM host par défaut**
et ne rend pas la mémoire à Windows même quand Docker est arrêté — son
kernel garde du cache et ne fait pas de balloon-back automatique.

**Fix permanent** : créer `%UserProfile%\.wslconfig` (Windows path :
`C:\Users\<toi>\.wslconfig`) avec un cap explicite :

```ini
[wsl2]
memory=4GB
processors=4
swap=2GB

# Permet à WSL2 de rendre la RAM à Windows quand inactive
# (Windows 11 22H2+ / WSL 1.0+ uniquement).
autoMemoryReclaim=gradual
```

Puis appliquer :

```powershell
wsl --shutdown            # tue la VM WSL → RAM rendue immédiatement
# Docker Desktop redémarre auto au prochain `docker` ou via l'icône.
```

**Fix ponctuel** (sans toucher au config) : `wsl --shutdown` libère les
3 Go instantanément. À refaire chaque fois que Docker reste idle longtemps.

**Quitter Docker Desktop complètement** : icône systray → Quit Docker
Desktop. WSL reste vivant tant qu'une distro tourne (Ubuntu / docker-
desktop-data) — le `wsl --shutdown` ci-dessus est nécessaire.

### Diagnostic — qui mange la RAM/disque ?

```powershell
docker system df -v                # taille images / containers / volumes / build cache
docker stats --no-stream           # snapshot CPU/RAM par container actif
wsl --list --running               # distros WSL en vie (= sources du Vmmem)
```

---

## Détail de `start_stack.ps1`

Ce que ça fait, dans l'ordre :

| # | Étape | Skip avec |
|---|---|---|
| 1 | Vérifie `docker`, `aws`, `python`, `git` sur le PATH | — |
| 2 | `git pull --ff-only origin main` (si branche = main) | `-NoPull` |
| 3 | Crée `.venv` + `pip install -e ".[dev,api,quant,ib,writer]"` si absent | `-RecreateVenv` pour forcer |
| 4 | Charge les 5 secrets depuis SSM en `$env:*` | — |
| 5 | `docker compose up -d --build` (profils `engines` + `ib`) | `-NoBuild` |
| 6 | Attend Postgres `healthy` (max 60s) | — |
| 7 | `alembic upgrade head` dans le container `api` | — |
| 8 | Restart nginx (refresh DNS upstreams) | — |
| 9 | Ouvre Windows Terminal : 10 tabs `logs -f` + 1 tab healthcheck | `-NoTabs` |

Durée typique :
- Premier lancement (venv neuf + build images) : **~5 min**
- Lancement quotidien (`-NoBuild`) : **~30 s**

---

## Variantes courantes

```powershell
.\scripts\ops\start_stack.ps1 -NoPull -NoBuild   # quick restart (~30s)
.\scripts\ops\start_stack.ps1 -RecreateVenv      # rebuild venv (après pyproject.toml change)
.\scripts\ops\start_stack.ps1 -NoTabs            # CI / scripting / pas de WT
```

---

## Vérifier que ça tourne

Le tab `healthcheck` (auto-ouvert à la fin) lance ces probes après 20s d'attente :

- `docker compose ps` — les 10 containers en `Up (healthy)`
- `pg_isready` + `redis-cli PING`
- `curl http://localhost/api/v1/health` → `{"status":"OK"}`
- `curl http://localhost/api/v1/health/extended` → `{"status":"OK", redis:..., postgres:..., engines:{...}}`
- 4 heartbeats engines (`market_data`, `vol_engine`, `risk_engine`, `db_writer`) — chacun avec un timestamp ISO
- `Test-NetConnection 127.0.0.1 -Port 4002` — IB Gateway accessible

URLs à connaître :
- **Dashboard** : http://localhost/
- **API** : http://localhost/api/v1/
- **OpenAPI Swagger** : http://localhost/docs
- **Postgres host** (depuis Windows) : `psql postgresql://fxvol:$env:DB_PASSWORD@localhost:5433/fxvol`
- **Redis host** (depuis Windows) : `redis-cli -h 127.0.0.1 -p 6380`

---

## Règle sécurité : zéro exposition des secrets

Les 5 secrets (`IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`, `TRADING_MODE`) vivent **uniquement** dans AWS SSM Parameter Store et en RAM dans la session shell. **Pas de `.env` sur disque.**

Interdits — affichent un secret en clair :
- `echo $env:IB_PASSWORD`, `Write-Host $env:DB_PASSWORD`
- `Get-ChildItem Env:`, `printenv`, `env`
- `cat .env`, `Get-Content .env`
- `aws ssm get-parameter --with-decryption` sans `--query 'Parameter.Name'`

Autorisés — vérifient la présence sans exposer la valeur :
```powershell
if ($env:IB_PASSWORD) { "set, $($env:IB_PASSWORD.Length) chars" } else { "MISSING" }
aws ssm get-parameter --name /fxvol/prod/IB_USERID --query 'Parameter.Name' --output text --profile fxvol-dev
```

Le hook `.claude/hooks/block_secrets.ps1` (configuré dans `.claude/settings.local.json`) bloque automatiquement ces commandes côté Claude Code. Vit dans `.claude/` car c'est un outil Claude Code, pas un script user-runnable.

Si un secret apparaît accidentellement quelque part :
1. Rotation : console AWS → SSM Parameter Store → `/fxvol/prod/<NAME>` → Edit → nouvelle valeur. Si IB compromis, faire AUSSI le reset côté portail IB.
2. Purge PSReadLine : `Clear-History; Remove-Item (Get-PSReadlineOption).HistorySavePath`
3. Fermer toutes les fenêtres qui ont vu la valeur

---

## Troubleshooting

| Symptôme | Cause probable | Fix |
|---|---|---|
| `Missing required tool : 'aws'` | AWS CLI v2 absent | `winget install -e --id Amazon.AWSCLI` |
| `Docker daemon not reachable` | Docker Desktop pas lancé | démarrer Docker Desktop, attendre l'icône verte |
| `AWS profile 'fxvol-dev' not usable` | Access keys expirées / absentes | `aws configure --profile fxvol-dev` puis re-tester |
| `Postgres did not become healthy within 60s` | Image PG corrompue ou volume orphelin | `.\scripts\ops\start_stack.ps1 -Down -DropVolumes` puis re-lancer |
| `nginx` 502 sur `/api/...` | API container pas prêt au boot de nginx | déjà géré par le restart auto étape 8 ; sinon `docker compose restart nginx` |
| Heartbeats `<nil>` dans le healthcheck | engines crashent au boot | `docker compose logs vol-engine` (tab dédié auto) |
| WT tabs ne s'ouvrent pas | `wt.exe` absent | installer Windows Terminal (Microsoft Store) |

---

## Scripts encore présents (et pourquoi)

| Script | Usage |
|---|---|
| `start_stack.ps1` | **THE one-shot command** ci-dessus |
| `load_secrets.ps1` | Appelé par `start_stack.ps1` — fetch SSM → `$env:*` |
| `load_secrets.sh` | Linux equivalent of `load_secrets.ps1`. Source it in a bash session : `. scripts/ops/load_secrets.sh` |
| `.claude/hooks/block_secrets.ps1` | Hook Claude Code (PreToolUse) qui bloque les commandes exposant un secret. **Vit dans `.claude/`** (gitignored) car c'est de la config harness Claude, pas un script utilisateur. |
| `db_apply.py` / `db_rollback.py` / `db_new_revision.py` / `db_reset.py` | Wrappers Alembic (pour usage hors container, ex: créer une nouvelle migration en local) |
| `dump_openapi.py` | Régénère `frontend/src/api/schema.d.ts` après changement Pydantic |

---

## Références

- AWS prep : `infrastructure/aws/` (KMS+SSM+IAM setup, EC2 prep)
- Architecture cible : repo root `README.md` + in-app **Stack** dev tab (17 containers, wiring)
- Schéma DB live : in-app **DB Schema** dev tab (introspects `Base.metadata` — no static doc to drift)
- API endpoints : `http://localhost/docs` (FastAPI Swagger) + generated `frontend/src/api/schema.d.ts`
