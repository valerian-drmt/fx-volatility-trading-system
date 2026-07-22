# `scripts/local/` — local Docker stack

Two PowerShell scripts to run the full stack on the developer laptop. They are
**user-run only** (they load secrets into RAM — never echo a secret value).

| File | Role |
|---|---|
| `load_secrets.ps1` | Pull `/fxvol/prod/*` from AWS SSM into the current shell (RAM only). |
| `stack.ps1` | One entry point for the whole container lifecycle (build / create / start / stop / remove) plus targeted and maintenance actions. |

Everything below is also reachable, without memorising flags, from the
PyCharm run configs (`Local All` / `Local Containers` folders).

---

## `load_secrets.ps1`

Fetches every project secret from AWS SSM Parameter Store and injects it into the
current PowerShell session as environment variables. **Nothing is written to
disk**; the values live in RAM only, and Docker Compose inherits them from the
parent process. Also derives the non-secret `DATABASE_URL`, `REDIS_URL` and
`PYTHONPATH`.

Run it once per shell before any manual `docker compose` command — Compose
interpolates `${DB_PASSWORD:?}` at parse time, so the stack will not even start
without the secrets loaded. `stack.ps1` calls it automatically when the secrets
are missing.

Needs an authenticated AWS profile (`fxvol-dev` by default; `aws sso login`
first if it uses SSO).

---

## `stack.ps1`

### The container lifecycle — 3 mirror pairs

A container goes through three phases. Each has a forward step and a reverse
step. The PyCharm configs are named `ALL <PHASE> <up|down>` so the phase and the
direction are always explicit (`ALL` = every image/container, all profiles).

| Phase | Forward (`up`) | Switch | Reverse (`down`) | Switch |
|---|---|---|---|---|
| **BUILD** — the image | build images (`compose build`) | `-Build` | remove images (`compose down --rmi all`) | `-Purge` |
| **CREATE** — the container | create containers, not started (`compose create`) | `-Create` | remove containers, keep images (`compose down`) | `-Down` |
| **START** — running | start containers (`compose start`) | `-Start` | stop containers, keep them (`compose stop`) | `-Stop` |

`-Build` accepts `-NoCache` to rebuild every layer from scratch.

### `up` — the everyday shortcut

`up` fuses **create + start** in one step (you almost never run `create` then
`start` by hand):

| Config | Switch(es) | What it does |
|---|---|---|
| `ALL BUILD/CREATE/START up` | *(none)* | Full pipeline: prereqs → `git pull` → venv → load secrets → **build + create + start** all containers (engines + ib + obs) → wait Postgres → Alembic → restart nginx. Heaviest, ~5 min. |
| `ALL CREATE/START up` | `-NoBuild` | Create + start from images already built. The daily driver. |
| `ALL status` | `-Status` | `compose ps` — container status + health for every service. |

### Targeted / scoped

| Config | Switch(es) | What it does |
|---|---|---|
| `Core only` | `-Core` | Only api / frontend / nginx / postgres / redis — no engines/ib/obs. Enough for front/api work, avoids the IB-unhealthy noise. |
| `Rebuild: <service>` | `-Service <name>` | Rebuild the image and recreate just that container (e.g. `frontend`, `api`, `vol-engine`). Rebuilding `api` also runs Alembic; `api`/`frontend` also restart nginx. Comma-separated list accepted. Add `-Logs` to tail, `-Down` to stop just it. |

### Maintenance

| Config | Switch(es) | What it does |
|---|---|---|
| `Refresh RAM - kills all WSL!` | `-Refresh` | Stops the stack (volumes kept) → `wsl --shutdown` so Docker's WSL2 VM returns its RAM to Windows → recreates the containers + Alembic + nginx. **⚠ closes ALL your WSL sessions.** Data and images survive. |
| *(no config)* | `-RecreateVenv` | Delete and rebuild `.venv`, reinstalling deps from `pyproject.toml`. |
| *(add to any down)* | `-DropVolumes` | On `-Down` or `-Purge`, also drop the volumes (Postgres DB + Redis cache erased). **Irreversible.** |

---

## Notes

- These scripts drive the **local** stack only. Production images are built on
  GitHub Actions (not this machine); the AWS/EC2 host is driven from
  `scripts/aws/ec2.ps1`.
  long-lived shell, so secrets loaded once stay in RAM for later actions.
