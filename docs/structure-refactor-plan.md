# Project Structure Refactor — Master Plan

> **Goal.** Migrate the repository to a structure that is unambiguous, scalable
> to dozens of additional services, and immediately legible — to a junior
> reading the code for the first time, and to a senior auditing it.
>
> **Constraint we deliberately ignore.** Engineering hours. Each step is
> sized to be safe (≤ 200 LOC, one concept), but the *number* of steps is
> chosen for cleanness, not for speed.
>
> **Working mode.** This refactor is executed on the active `sandbox/*`
> branch as a sequence of atomic commits. **No PRs are opened during the
> refactor.** Decomposition into proper feature branches and PRs happens
> later, once the sandbox is stable, following the standard PLAYBOOK
> workflow. Each numbered step below is therefore *one atomic commit* on
> the sandbox branch — not a pull request.
>
> **Audience.** Public GitHub portfolio. Every choice optimizes for "someone
> lands on the repo cold and understands it in 10 minutes" — folder names
> tell the story, file locations match expectations, single source of truth
> for everything.

---

## 1. Goals

A reader should be able to answer the following without running anything :

1. **Where does HTTP traffic enter ?** → one obvious folder.
2. **Where are the business rules (vol models, pricing) ?** → one obvious folder, no I/O inside.
3. **Where does each long-running service live ?** → one folder per container, one entry point each.
4. **What does the system depend on ?** → one file (`pyproject.toml`).
5. **Which tests cover which code ?** → `tests/` mirrors `src/` 1-to-1.

Every other goal (CI, deployment, perf) is downstream of these five.

## 2. Non-goals

- No rename of `core/` (Clean Architecture canonical name — keep).
- No move of `frontend/` into `src/` (PEP 621 / src-layout convention — keep).
- No change to the public REST or WS contract.
- No change to the database schema (Alembic stays where it is, just under a
  cleaner parent).
- No new framework, no new language, no new database. This refactor is
  structural only.

---

## 3. Tooling decision — `uv` over `pip-tools`

### 3.1 Recommendation

**Adopt `uv` (Astral) as the sole Python package manager and lockfile
generator.** Drop `requirements.txt`, `requirements/*.txt`, `pytest.ini`,
`ruff.toml` ; consolidate into `pyproject.toml` + `uv.lock`.

### 3.2 Why `uv` for this project specifically

| Criterion | `uv` (Astral, 2024+) | `pip-tools` (legacy) |
|---|---|---|
| **Single tool** | replaces pip + pip-tools + virtualenv + pipx + pyenv | requires pip + pip-tools + virtualenv |
| **Speed** | 10–100× faster than pip on cold install | pip-class speed |
| **Lockfile format** | native `uv.lock`, cross-platform, hash-pinned, includes all extras | `requirements.txt` per environment (multiple files) |
| **Workspace / monorepo** | first-class workspace support (`[tool.uv.workspace]`) | none |
| **Reproducibility** | `uv sync --frozen` is byte-stable across machines | requires careful `pip-sync` discipline |
| **PEP 621 native** | yes (the modern standard) | yes |
| **Vendor coherence** | same team as `ruff` (already used here) | unrelated vendors |
| **Maturity** | 1.0 shipped Q4 2024, used by FastAPI, Pydantic, Polars, … in 2025 | 2014, very mature |
| **Risk** | newer tool — smaller surface of community Q&A | safe bet |

For a 10-container project that already has 4 disjoint dependency sets
(`base`, `ib`, `quant`, `writer`) and is *growing* (events pipeline, R6
infrastructure, R8 deployment), the workspace + extras model in `uv` is
exactly the abstraction we need. Coherence with the existing `ruff`
adoption tips the choice further. The single `uv.lock` replaces 4 pinned
files plus the unmaintained drift between them.

### 3.3 What we get concretely

- One `pyproject.toml` declaring : project metadata, deps, optional-deps
  groups, ruff config, pytest config, mypy config.
- One `uv.lock` committed to the repo, pinning every transitive dep
  with hashes — `uv sync` recreates a byte-identical environment anywhere.
- `Dockerfile` per service uses `uv pip install ".[ib,quant]"` or
  `uv sync --extra ib --extra quant --frozen`. No `requirements/*.txt`
  needs to be maintained.
- CI uses `uv sync --extra dev` then `uv run pytest`, `uv run ruff`,
  `uv run mypy` — same commands locally and in CI.

### 3.4 What we lose (and why it's fine)

- A small bus factor : `uv` is one company's tool. Mitigated by
  pyproject.toml being PEP 621 — switching back to pip-tools later is a
  one-day job because the source of truth is standard.
- Slightly less Stack Overflow content for `uv` than for pip — mitigated by
  `uv` having the cleanest CLI (`uv add`, `uv sync`, `uv lock`,
  `uv run`) and Astral's docs being best-in-class.

---

## 4. Target structure

```
fx-volatility-trading-system/
│
├── pyproject.toml                # single source of truth (deps, ruff, pytest, mypy)
├── uv.lock                       # committed lockfile
├── docker-compose.yml
├── docker-compose.override.yml
├── .python-version               # uv-managed Python pin
├── README.md
├── CLAUDE.md
├── LICENSE
│
├── src/                          # Python only
│   ├── api/                      # → fxvol-api container
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI app + lifespan
│   │   ├── config.py             # Settings(BaseSettings)
│   │   ├── dependencies.py       # FastAPI dependency providers
│   │   ├── routers/              # 12 routers (HTTP transport only)
│   │   ├── ws/                   # WebSocket transport
│   │   ├── middleware/           # logging / rate_limit / timing
│   │   ├── schemas/              # Pydantic request/response (was: api/models/)
│   │   └── orchestration/        # use-case orchestration (was: api/services/)
│   │
│   ├── engines/                  # 5 long-running services (was: src/services/)
│   │   ├── market_data/          # ib_insync → Redis
│   │   ├── vol/                  # SVI/SSVI/GARCH/HAR/PCA/GMM
│   │   ├── risk/                 # greeks aggregation
│   │   ├── db_writer/            # Redis pub/sub → Postgres
│   │   └── execution/            # order submission HTTP server
│   │
│   ├── core/                     # pure domain (no I/O)
│   │   ├── vol/                  # garch, har_rv, svi, ssvi, …
│   │   ├── pricing/              # bs.py
│   │   ├── risk/                 # greeks.py
│   │   └── types/                # Strike, Tenor, Currency, … (was: scattered)
│   │
│   ├── persistence/              # ONLY the DB adapter
│   │   ├── __init__.py
│   │   ├── models.py             # 20 ORM classes
│   │   ├── db.py                 # engine + AsyncSession factory
│   │   ├── alembic.ini
│   │   └── migrations/
│   │
│   ├── bus/                      # ONLY the Redis adapter
│   │   ├── __init__.py
│   │   ├── client.py             # connection factory (merged duplicate)
│   │   ├── publisher.py
│   │   ├── channels.py
│   │   └── keys.py
│   │
│   └── shared/                   # cross-cutting infra
│       ├── __init__.py
│       ├── config.py             # base Settings (loaded by api/config + engines)
│       ├── logging.py            # structlog setup
│       └── ib_connection.py      # IB sync wrapper
│       # ❌ db_queue.py (deleted — dead code)
│       # ❌ redis_client.py (moved into bus/)
│
├── frontend/                     # TypeScript SPA (intentionally outside src/)
│   ├── package.json, vite.config.ts, tsconfig.json
│   ├── src/
│   │   ├── api/                  # generated schema + typed client
│   │   ├── components/
│   │   │   ├── panels/           # 15 React panels
│   │   │   ├── charts/, common/, layout/
│   │   ├── hooks/, store/, pages/, utils/
│   ├── e2e/                      # Playwright
│   └── tests/                    # Vitest
│
├── infrastructure/
│   ├── docker/                   # one Dockerfile per service
│   │   ├── api.Dockerfile        # was: Dockerfile.api (consistent naming)
│   │   ├── engines.Dockerfile
│   │   ├── execution.Dockerfile
│   │   ├── web.Dockerfile
│   │   └── ib-stub.Dockerfile
│   ├── nginx/                    # nginx.conf + nginx-dev.conf + frontend.conf
│   ├── aws/                      # SSM + KMS bootstrap
│   ├── ec2/                      # systemd unit + provisioning
│   └── postgres/                 # init scripts (if any)
│
├── scripts/                      # categorized
│   ├── ops/                      # production / runtime
│   │   ├── load_secrets.ps1, load_secrets.sh
│   │   └── start_stack.ps1
│   ├── dev/                      # developer utilities
│   │   ├── dump_openapi.py
│   │   └── gmm_diagnostic.py
│   ├── migrations/               # one-shot data migrations
│   │   ├── backfill_iv_history_for_gmm.py
│   │   └── seed_events_manual.py
│   └── smoke/                    # re-runnable Jupyter notebooks
│       ├── api/, db-writer/, frontend/, ib-gateway/, market-data/,
│       └── nginx/, postgresql/, redis/, vol-engine/
│
├── tests/                        # mirrors src/ 1-to-1
│   ├── conftest.py
│   ├── fixtures/                 # shared pytest fixtures
│   ├── unit/                     # pure, fast, no I/O
│   │   ├── core/
│   │   │   ├── vol/
│   │   │   ├── pricing/
│   │   │   └── risk/
│   │   ├── api/
│   │   │   ├── routers/
│   │   │   ├── ws/
│   │   │   └── orchestration/
│   │   ├── engines/
│   │   │   ├── market_data/
│   │   │   ├── vol/
│   │   │   └── …
│   │   ├── bus/
│   │   └── persistence/
│   ├── integration/              # needs Postgres / Redis / IB (gated by markers)
│   │   ├── db/
│   │   ├── redis/
│   │   └── ib/
│   └── e2e/                      # full compose stack (engines-split)
│       └── compose/
│   # ❌ tests/old/ (deleted — Qt era; git history preserves it)
│   # ❌ tests/sandbox_r9/ (moved out of CI scope; lives only on sandbox branch)
│
└── docs/
    ├── README.md                 # index of docs
    ├── architecture.md           # was: project-architecture.md (renamed)
    ├── deployment.md             # was: DEPLOYMENT.md (lowercase, consistent)
    ├── performance.md
    ├── branch-protection.md
    ├── vol-model-refactor-plan.md
    ├── vol-trading-user-guide.md
    ├── preventing-spaghetti-code.md
    ├── structure-refactor-plan.md  # this file
    └── RUN_LOCAL_STACK.md        # moved from scripts/ (it's a doc)
```

---

## 5. Naming conventions — the decision table

Every rename in this plan is justified by a single principle : *the name
matches what the reader expects to find inside*.

| Before | After | Reason |
|---|---|---|
| `src/services/` | `src/engines/` | the rest of the project (compose service names, docs, READMEs) already calls them "engines". Eliminates collision with `src/api/services/`. |
| `src/api/services/` | `src/api/orchestration/` | "services" was overloaded ; "orchestration" describes the actual role (composes domain + persistence + bus). |
| `src/api/models/` | `src/api/schemas/` | "models" collides with `persistence/models.py` (ORM). "schemas" is FastAPI's own term for Pydantic. |
| `src/persistence/payloads.py` | `src/api/schemas/types.py` (or `src/core/types/`) | DTOs are not part of persistence. |
| `src/persistence/writer.py` | merged into `src/engines/db_writer/` | the writer *is* a service. Move the logic there ; persistence keeps only ORM + session. |
| `src/shared/redis_client.py` + `src/bus/redis_client.py` | single `src/bus/client.py` | one redis adapter, one place. |
| `src/shared/config.py` + `src/api/config.py` | `src/shared/config.py` (base) + `src/api/config.py` (api-specific extension) | clear inheritance, not duplication. |
| `src/shared/db_queue.py` | deleted | dead code, confirmed unused. |
| `Dockerfile.api`, `Dockerfile.web` | `api.Dockerfile`, `web.Dockerfile` | extension-first naming sorts cleanly in `ls` and is the convention used by Docker docs. |
| `tests/old/` | deleted | Qt era ; git keeps the history, that is its job. |
| `requirements.txt` + `requirements/*.txt` | `pyproject.toml` + `uv.lock` | one source of truth. |
| `pytest.ini`, `ruff.toml` | merged into `pyproject.toml` | one source of truth. |
| `scripts/RUN_LOCAL_STACK.md` | `docs/RUN_LOCAL_STACK.md` | it's documentation, not a script. |
| `scripts/db/` (notebooks) | `scripts/smoke/postgresql/` | already what the equivalent for other services looks like ; keep it consistent. |

---

## 6. Migration plan — step by step

Each step is one atomic commit on the active sandbox branch, sized for
≤ 200 lines net diff, one concept, green local checks (ruff + pytest +
compileall, plus relevant smoke notebooks). Order is chosen so no step
breaks the next ; later, when the sandbox is broken into proper feature
branches, each step maps cleanly to one PR.

### Group A — dead code and trivial moves (low risk, high signal)

**Step 1 — Fix the `db_queue.py` misnomer**
- Original plan claimed `src/shared/db_queue.py` was dead — *audit error*. The
  file is actively used : it exposes `publish_db_event` (Redis pub/sub
  publisher) consumed by vol-engine, db-writer, and a smoke notebook.
- The real defect is the *name* : "queue" no longer matches behaviour
  (it became Redis pub/sub at R7).
- Action : rename `src/shared/db_queue.py` → `src/shared/db_events.py` and
  update the four import sites + the `__init__.py` re-export.
- `tests/old/` is **not** deleted : per `tests/STRUCTURE.md` it is the
  deliberate quarantine of historical tests pending triage. It is
  re-classified during Group E.
- **Validation** : `ruff` passes, `compileall` passes, `pytest -m "not integration"` passes.

**Step 2 — Move `RUN_LOCAL_STACK.md` to `docs/`**
- `git mv scripts/RUN_LOCAL_STACK.md docs/RUN_LOCAL_STACK.md`.
- Update any link in the rest of the docs.
- **Validation** : grep for "RUN_LOCAL_STACK" and update each hit.

**Step 3 — Categorize `scripts/`**
- Create `scripts/{ops,dev,migrations}/`.
- `git mv` the existing files into their category.
- Update CLAUDE.md, README.md, CI workflow paths.
- **Validation** : CI green, `start_stack.ps1` still works (operator runs it).

### Group B — collapse duplicates (one source of truth)

**Step 4 — Merge `redis_client.py` into a single `src/bus/client.py`**
- Compare `src/bus/redis_client.py` and `src/shared/redis_client.py`,
  keep the union of features in `src/bus/client.py`.
- Delete `src/shared/redis_client.py`.
- Search-and-replace imports : `from shared.redis_client` →
  `from bus.client`, `from bus.redis_client` → `from bus.client`.
- **Validation** : `redis_integration` test suite green ; `engines-split` CI
  job green.

**Step 5 — Decide what `shared/config.py` vs `api/config.py` each own**
- `shared/config.py` keeps the base `Settings` (DATABASE_URL, REDIS_URL,
  IB_HOST/PORT, log level, …) — used by all services.
- `api/config.py` is a subclass adding API-only settings (CORS origins,
  rate limits, …) — used by `src/api/main.py`.
- Engines import directly from `shared/config.py`.
- **Validation** : every service starts ; CI green.

**Step 6 — Move `src/persistence/payloads.py` out of persistence**
- Read the file, decide : if these are Pydantic DTOs → `src/api/schemas/`. If
  they are plain dataclasses used across layers → `src/core/types/`.
- Move + update imports.
- **Validation** : compileall, ruff, pytest, frontend openapi-drift CI.

**Step 7 — Resolve `persistence/writer.py` ↔ `engines/db_writer/` overlap**
- Read both ; either (a) `persistence/writer.py` is a low-level helper used
  by `engines/db_writer/` — keep it but rename to `persistence/batch.py`,
  or (b) it is a duplicate — delete it and keep only `engines/db_writer/`.
- **Validation** : `engines-split` CI job green ; db-writer smoke notebook re-runs.

### Group C — disambiguating renames (touches many imports)

**Step 8 — Rename `src/services/` → `src/engines/`**
- `git mv src/services src/engines`.
- Search-and-replace `from services.` → `from engines.` in src/, tests/, scripts/.
- Update `Dockerfile.engines` (COPY paths), `docker-compose.yml` (build context),
  `.dockerignore`, CI workflows.
- Update CLAUDE.md, README.md, `docs/architecture.md`.
- **Validation** : all CI jobs green ; full compose `up -d --profile engines`
  smoke locally.

**Step 9 — Rename `src/api/services/` → `src/api/orchestration/` + `src/api/models/` → `src/api/schemas/`**
- Two `git mv` + import rewrites.
- Update FastAPI router imports.
- **Validation** : openapi-drift CI green ; vitest green ; `frontend/src/api/schema.d.ts` regenerated.

**Step 10 — Rename Dockerfiles to `<service>.Dockerfile` convention**
- `git mv infrastructure/docker/Dockerfile.api → api.Dockerfile`, etc.
- Update `docker-compose.yml` `build.dockerfile` paths.
- **Validation** : `docker compose build` works ; CI image-build job green.

### Group D — modernize tooling (the big one)

**Step 11 — Bootstrap `pyproject.toml` (no behavior change yet)**
- Create `pyproject.toml` declaring project metadata, dependencies (= union
  of `requirements.txt` and `requirements/*.txt`), optional-dependencies for
  `[ib]`, `[quant]`, `[writer]`, `[dev]`, `[test]`.
- **Do not yet delete** `requirements.txt` and `requirements/*.txt`.
- **Do not yet** move ruff/pytest config.
- Add `tool.ruff` and `tool.pytest.ini_options` and `tool.mypy` blocks
  *as a copy* — keep `ruff.toml` and `pytest.ini` for now.
- **Validation** : `pip install -e .[dev]` produces same site-packages as
  `pip install -r requirements.txt` (diff the lockfile both ways).

**Step 12 — Adopt `uv` and commit `uv.lock`**
- Install uv (`curl -LsSf https://astral.sh/uv/install.sh | sh` — operator only).
- Run `uv lock` to generate `uv.lock` from `pyproject.toml`.
- Commit `uv.lock`.
- Update CONTRIBUTING / README to describe `uv sync --extra dev` as the
  blessed dev setup. Keep `pip install -r requirements.txt` documented as
  fallback.
- **Validation** : `uv sync --extra dev && uv run pytest -m "not integration"`
  green.

**Step 13 — Migrate Dockerfiles to `uv`**
- Each `*.Dockerfile` switches from
  `pip install -r requirements/<x>.txt` to
  `uv sync --frozen --extra <x>` (or `uv pip install ".[<x>]" --system`).
- Use the official `ghcr.io/astral-sh/uv:python3.11-bookworm-slim` base for
  the build stage and copy the binary into the runtime stage.
- **Validation** : `engines-split` CI green ; image sizes equal-or-smaller
  than today's.

**Step 14 — Migrate CI to `uv`** *(this commit only takes effect once the sandbox is pushed and the workflow runs ; verify locally with `act` if needed before committing)*
- `.github/workflows/ci.yml` jobs use `astral-sh/setup-uv@v3` action.
- Replace `pip install -r requirements.txt` lines with `uv sync --frozen --extra dev`.
- Cache key includes `uv.lock` hash.
- **Validation** : every CI job green when sandbox is later pushed ; total CI runtime should drop noticeably (~30-50%).

**Step 15 — Delete `requirements.txt`, `requirements/`, `pytest.ini`, `ruff.toml`**
- Once steps 11-14 are committed and stable for one cycle, delete the legacy files.
- Update CLAUDE.md and README.md to drop any reference to them.
- **Validation** : full CI green ; cold-clone-and-build works on a clean machine.

### Group E — restructure tests to mirror `src/`

**Step 16 — Build the new `tests/` skeleton**
- Create `tests/unit/{core,api,engines,bus,persistence}/`,
  `tests/integration/{db,redis,ib}/`, `tests/e2e/`.
- Move existing test files into the matching subfolder using `git mv`
  (preserves blame).
- Rewrite a handful of imports if collected files relied on flat layout.
- **Validation** : `pytest -m "not integration"` collects the same number of
  tests as before, all green.

**Step 17 — Pin pytest collection to `tests/unit/` + `tests/integration/`**
- Top-level `tests/sandbox_r9/` does not exist — only inside `tests/old/`.
- Pin `testpaths = ["tests/unit", "tests/integration"]` in `pyproject.toml`
  so `tests/old/` and `tests/fixtures/` are excluded from default collection.
- Promotion of files out of `tests/old/` is a separate, ongoing effort
  driven by the existing `tests/STRUCTURE.md` rules — out of scope of this
  refactor.
- **Validation** : `pytest -m "not integration"` collects only `tests/unit/`.

### Group F — final polish

**Step 18 — Lowercase `docs/` filenames + add `docs/README.md` index**
- `git mv DEPLOYMENT.md → deployment.md`, etc.
- `docs/README.md` lists each doc with a one-sentence summary so a new
  reader has a single landing page.
- **Validation** : grep for old uppercase names ; update README badges and links.

**Step 19 — Add `mypy --strict` to CI**
- `tool.mypy` config in `pyproject.toml`, `strict = true`.
- New CI job `typecheck` running `uv run mypy src`.
- Fix or `# type: ignore[<reason>]` the existing failures, one commit-pair at a time
  if the volume is large.
- **Validation** : new CI job green.

**Step 20 — Architecture lint via `import-linter`**
- Add `import-linter` as a dev dependency.
- Declare contracts in `pyproject.toml` :
  - `src/core/` may NOT import from `src/persistence/`, `src/bus/`,
    `src/api/`, `src/engines/`.
  - `src/persistence/` and `src/bus/` may NOT import from `src/api/` or
    `src/engines/`.
  - `src/api/` may NOT import from `src/engines/` (and vice-versa).
- New CI job `architecture` runs `lint-imports`.
- **Validation** : passes on the cleaned-up codebase ; future violations
  break the build.

---

## 7. Validation strategy

### 7.1 Per-step

Every commit runs the full local check chain (ruff + compileall + pytest
unit suite) and the relevant smoke notebook(s) before being made. CI
matrices on PRs come later when the sandbox is decomposed. No
`--no-verify`, no skipped pre-commit hooks.

### 7.2 Cross-step reproducibility checkpoint

After Group D (`uv` adopted), and after Group C (renames complete), run
**from a clean clone of the sandbox branch** :

```bash
git clone … && cd fx-volatility-trading-system
uv sync --extra dev
uv run pytest -m "not integration and not db_integration and not redis_integration"
docker compose build
docker compose up -d
docker compose --profile engines up -d
# Verify all 10 containers healthy
docker compose ps
```

If any step fails on a fresh machine, the refactor has regressed —
fix before continuing.

### 7.3 Public-readability checkpoint

After Group F, ask someone unfamiliar with the project to answer in 10
minutes :

1. Where do incoming HTTP requests get handled ?
2. Where is the SVI calibration code ?
3. Where does the vol engine live, and what does it consume / produce ?
4. Where would I add a new REST endpoint ?
5. Where would I add a new ORM model ?

If they can answer all five from the folder tree alone, the refactor has
achieved its goal.

---

## 8. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Mass rename breaks an unnoticed import path (steps 8-9) | medium | grep + `compileall` + full local check ; rename in one commit each, never combined ; reviewer engaged when the sandbox is decomposed into PRs |
| `uv` adoption regresses Docker image size or runtime | low | step 13 measures before/after image sizes ; rollback is `git revert <sha>` |
| Git blame loss on moved files | low | `git mv` preserves history ; readers should configure `git log --follow` ; document this in CONTRIBUTING |
| Frontend ↔ backend OpenAPI drift during renames | low | the `openapi-contract` CI job will guard this once the sandbox is pushed |
| Operator confusion during migration | medium | each commit message includes a one-line "what changed for the operator" if anything user-facing moved |

---

## 9. Out of scope (intentionally)

The following are valuable but **not** part of this refactor :

- Async test framework changes (sticking with pytest-asyncio).
- Database changes — Alembic schema is untouched.
- Logging or telemetry overhaul — structlog stays.
- Multi-tenant or multi-symbol support — single-symbol assumption preserved.
- Any change to the public REST or WS API contract.

These deserve their own roadmap items, separate from the structural cleanup.

---

## 10. Success criteria

The refactor is **done** when *all* of the following are true :

- [ ] `pyproject.toml` is the only place declaring Python deps, ruff config,
      pytest config, and mypy config. No `requirements.txt`, no
      `requirements/`, no `pytest.ini`, no `ruff.toml`.
- [ ] `uv.lock` is committed and `uv sync --frozen` reproduces the env on
      every CI run.
- [ ] Every folder under `src/` has a single, unambiguous role — confirmable
      by reading folder names alone.
- [ ] `tests/` mirrors `src/` 1-to-1 ; no `tests/old/`, no
      `tests/sandbox_r9/` in `main`.
- [ ] `import-linter` enforces the dependency direction in CI.
- [ ] `mypy --strict` passes in CI.
- [ ] A reader unfamiliar with the project can locate any concept in
      ≤ 10 seconds from the folder tree.
- [ ] CI total wall time reduced from current baseline (uv install speed-up).
- [ ] CLAUDE.md and README.md describe the *current* structure, with
      example commands that work on a fresh clone.

---

## 11. After the refactor

The repo is positioned to grow without sliding back into spaghetti. Specific
follow-ups that become natural :

- **Per-engine `pyproject.toml` workspace members** — if any engine grows its
  own non-trivial dependency surface, promote it to a `uv` workspace member
  with its own pinned subset.
- **Public PyPI extraction of `src/core/`** — the pure domain code (no I/O)
  is portfolio-grade open-source material ; extracting it into a thin
  importable package (e.g. `fxvol-core`) becomes mechanical once the
  layering is clean.
- **Generated architecture diagrams in CI** — once `import-linter` is in
  place, `pydeps` or similar can render an SVG of the actual module graph
  on every commit, attached as an artifact.

These are *consequences* of doing the refactor right, not part of it.
The point of the cleanup is to make those next moves *cheap* — which
is the only definition of "scalable" that matters.
