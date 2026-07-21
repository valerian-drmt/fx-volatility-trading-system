# Run the local v2 stack

> **TL;DR** — `.\scripts\ops\stack.ps1` launches everything. A single command.

10-container stack: `postgres` · `redis` · `api` · `db-writer` · `market-data` · `vol-engine` · `risk-engine` · `frontend` · `nginx` · `ib-gateway`.

Single compose file `docker-compose.yml`, automatic override `docker-compose.override.yml` which exposes Postgres :5433 / Redis :6380 on the host (dev only). In prod, the override is not in the clone.

---

## Essential commands

Check the AWS profile (one-shot, after a new machine or expired access keys):

```powershell
aws configure --profile fxvol-dev
aws sts get-caller-identity --profile fxvol-dev
cd .\Documents\'Python Project'\fx-volatility-trading-system
.\scripts\ops\stack.ps1
```

Launch the stack:

```powershell
.\scripts\ops\stack.ps1
```

Reload the secrets in the current session:

```powershell
.\scripts\ops\load_secrets.ps1
```

Clean everything up:

```powershell
.\scripts\ops\stack.ps1 -Down              # stop everything (data preserved)
```

---

## Docker cleanup (free up RAM)

Docker Desktop on Windows quickly accumulates several GB between build
images / anonymous volumes / buildx caches. When the laptop fan gets
loud, with the stack stopped, run the block below.

> ⚠️ **Do NOT run `docker image prune -af`** without rebuilding
> `fxvol-ib-gateway:local` right after — it is a local image (not pull-able
> from a registry), `prune -af` deletes it and on the next `up` compose
> falls back to the upstream image `gnzsnz/ib-gateway:latest`, which is
> regularly broken. See `infrastructure/ib-gateway/README.md` for the rebuild.

```powershell
.\scripts\ops\stack.ps1 -Down       # stop stack, data preserved
docker container prune -f                 # exited / dead containers
docker image prune -f                     # dangling images only (NOT -a)
docker volume prune -f                    # orphaned anonymous volumes
docker network prune -f                   # unattached networks
docker builder prune -af                  # entire buildx cache (5-15 GB)
wsl --shutdown                            # returns the RAM reserved by WSL2 to Windows
```

### Cap the RAM allocated to Docker Desktop (permanent setting)

Docker Desktop → Settings → Resources → **Memory: 6 GB** is more than enough
for the 10 containers (postgres + redis are lightweight, `api` ~400 MB, each
engine 200-300 MB). Beyond 8 GB is a waste for this project.

### `vmmemWSL` squatting 3 GB while idle

Symptom: Task Manager shows `Vmmem` or `vmmemWSL` at 2-4 GB while
no container is running. Cause: WSL2 (the Linux backend of Docker
Desktop on Windows) **reserves up to 50% of host RAM by default**
and does not give the memory back to Windows even when Docker is stopped — its
kernel keeps cache around and does not balloon-back automatically.

**Permanent fix**: create `%UserProfile%\.wslconfig` (Windows path:
`C:\Users\<you>\.wslconfig`) with an explicit cap:

```ini
[wsl2]
memory=4GB
processors=4
swap=2GB

# Lets WSL2 return RAM to Windows when idle
# (Windows 11 22H2+ / WSL 1.0+ only).
autoMemoryReclaim=gradual
```

Then apply:

```powershell
wsl --shutdown            # kills the WSL VM → RAM returned immediately
# Docker Desktop restarts automatically on the next `docker` command or via the icon.
```

**One-off fix** (without touching the config): `wsl --shutdown` frees the
3 GB instantly. Redo it every time Docker stays idle for a long while.

**Quit Docker Desktop completely**: systray icon → Quit Docker
Desktop. WSL stays alive as long as a distro is running (Ubuntu / docker-
desktop-data) — the `wsl --shutdown` above is required.

### Diagnostics — what is eating the RAM/disk?

```powershell
docker system df -v                # size of images / containers / volumes / build cache
docker stats --no-stream           # CPU/RAM snapshot per running container
wsl --list --running               # live WSL distros (= sources of Vmmem)
```

---

## `stack.ps1` in detail

What it does, in order:

| # | Step | Skip with |
|---|---|---|
| 1 | Checks `docker`, `aws`, `python`, `git` on the PATH | — |
| 2 | `git pull --ff-only origin main` (if branch = main) | `-NoPull` |
| 3 | Creates `.venv` + `pip install -e ".[dev,api,quant,ib,writer]"` if absent | `-RecreateVenv` to force |
| 4 | Loads the 5 secrets from SSM into `$env:*` | — |
| 5 | `docker compose up -d --build` (profiles `engines` + `ib`) | `-NoBuild` |
| 6 | Waits for Postgres `healthy` (max 60s) | — |
| 7 | `alembic upgrade head` inside the `api` container | — |
| 8 | Restart nginx (refresh DNS upstreams) | — |
| 9 | Opens Windows Terminal: 10 `logs -f` tabs + 1 healthcheck tab | `-NoTabs` |

Typical duration:
- First launch (fresh venv + image builds): **~5 min**
- Daily launch (`-NoBuild`): **~30 s**

---

## Common variants

```powershell
.\scripts\ops\stack.ps1 -NoPull -NoBuild   # quick restart (~30s)
.\scripts\ops\stack.ps1 -RecreateVenv      # rebuild venv (after pyproject.toml change)
.\scripts\ops\stack.ps1 -NoTabs            # CI / scripting / no WT
```

---

## Verify it is running

The `healthcheck` tab (auto-opened at the end) runs these probes after a 20s wait:

- `docker compose ps` — the 10 containers in `Up (healthy)`
- `pg_isready` + `redis-cli PING`
- `curl http://localhost/api/v1/health` → `{"status":"OK"}`
- `curl http://localhost/api/v1/health/extended` → `{"status":"OK", redis:..., postgres:..., engines:{...}}`
- 4 engine heartbeats (`market_data`, `vol_engine`, `risk_engine`, `db_writer`) — each with an ISO timestamp
- `Test-NetConnection 127.0.0.1 -Port 4002` — IB Gateway reachable

URLs to know:
- **Dashboard**: http://localhost/
- **API**: http://localhost/api/v1/
- **OpenAPI Swagger**: http://localhost/docs
- **Postgres host** (from Windows): `psql postgresql://fxvol:$env:DB_PASSWORD@localhost:5433/fxvol`
- **Redis host** (from Windows): `redis-cli -h 127.0.0.1 -p 6380`

---

## Security rule: zero secret exposure

The 5 secrets (`IB_USERID`, `IB_PASSWORD`, `DB_PASSWORD`, `VNC_PASSWORD`, `TRADING_MODE`) live **only** in AWS SSM Parameter Store and in RAM in the shell session. **No `.env` on disk.**

Forbidden — they print a secret in cleartext:
- `echo $env:IB_PASSWORD`, `Write-Host $env:DB_PASSWORD`
- `Get-ChildItem Env:`, `printenv`, `env`
- `cat .env`, `Get-Content .env`
- `aws ssm get-parameter --with-decryption` without `--query 'Parameter.Name'`

Allowed — they check presence without exposing the value:
```powershell
if ($env:IB_PASSWORD) { "set, $($env:IB_PASSWORD.Length) chars" } else { "MISSING" }
aws ssm get-parameter --name /fxvol/prod/IB_USERID --query 'Parameter.Name' --output text --profile fxvol-dev
```

The hook `.claude/hooks/block_secrets.ps1` (configured in `.claude/settings.local.json`) automatically blocks these commands on the Claude Code side. It lives in `.claude/` because it is a Claude Code tool, not a user-runnable script.

If a secret accidentally shows up somewhere:
1. Rotation: AWS console → SSM Parameter Store → `/fxvol/prod/<NAME>` → Edit → new value. If IB is compromised, ALSO reset it on the IB portal side.
2. Purge PSReadLine: `Clear-History; Remove-Item (Get-PSReadlineOption).HistorySavePath`
3. Close every window that has seen the value

---

## Troubleshooting

| Symptom | Probable cause | Fix |
|---|---|---|
| `Missing required tool : 'aws'` | AWS CLI v2 missing | `winget install -e --id Amazon.AWSCLI` |
| `Docker daemon not reachable` | Docker Desktop not running | start Docker Desktop, wait for the green icon |
| `AWS profile 'fxvol-dev' not usable` | Access keys expired / missing | `aws configure --profile fxvol-dev` then retry |
| `Postgres did not become healthy within 60s` | Corrupted PG image or orphaned volume | `.\scripts\ops\stack.ps1 -Down -DropVolumes` then relaunch |
| `nginx` 502 on `/api/...` | API container not ready when nginx booted | already handled by the auto-restart in step 8; otherwise `docker compose restart nginx` |
| Heartbeats `<nil>` in the healthcheck | engines crash at boot | `docker compose logs vol-engine` (dedicated auto tab) |
| WT tabs do not open | `wt.exe` missing | install Windows Terminal (Microsoft Store) |

---

## Scripts still present (and why)

| Script | Usage |
|---|---|
| `stack.ps1` | **THE one-shot command** above |
| `load_secrets.ps1` | Called by `stack.ps1` — fetch SSM → `$env:*` |
| `load_secrets.sh` | Linux equivalent of `load_secrets.ps1`. Source it in a bash session : `. scripts/aws/load_secrets.sh` |
| `.claude/hooks/block_secrets.ps1` | Claude Code hook (PreToolUse) that blocks commands exposing a secret. **Lives in `.claude/`** (gitignored) because it is Claude harness config, not a user script. |
| `db_apply.py` / `db_rollback.py` / `db_new_revision.py` / `db_reset.py` | Alembic wrappers (for use outside the container, e.g. creating a new migration locally) |
| `dump_openapi.py` | Regenerates `frontend/src/api/schema.d.ts` after a Pydantic change |

---

## References

- AWS prep : `infrastructure/aws/` (KMS+SSM+IAM setup, EC2 prep)
- Target architecture : repo root `README.md` + in-app **Stack** dev tab (17 containers, wiring)
- Live DB schema : in-app **DB Schema** dev tab (introspects `Base.metadata` — no static doc to drift)
- API endpoints : `http://localhost/docs` (FastAPI Swagger) + generated `frontend/src/api/schema.d.ts`
