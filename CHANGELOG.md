# Changelog

All notable changes to this project are documented in this file. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/).

## [1.9.0] — R8 release (deploy prod + deprecation PyQt)

### Headline — breaking change

The desktop PyQt app (`app.py`, `src/controller.py`, `src/ui/`) is
**removed**. The supported surface from this release onwards is the
FastAPI backend + React frontend + containerised engines stack,
deployable via `docker compose up` or the new `deploy-prod` GitHub
Actions workflow.

### Added

- **`deploy.yml`** CI workflow : pushes `v*.*.*` tags and manual
  `workflow_dispatch` with a `deploy_sha` rollback input, SSH's into
  the EC2 host, renders `/opt/fxvol/.env` from repo secrets, pulls the
  six GHCR images, runs `docker compose up -d` + `alembic upgrade head`,
  and smoke-checks `GET /api/v1/health` before reporting success.
- **`infrastructure/ec2/setup.sh`** : one-shot idempotent provisioning
  for Ubuntu 22.04 — docker, ufw (22/80/443), fxvol user, systemd unit
  for compose auto-start, certbot renew + Postgres backup to S3 cron
  jobs.
- **`infrastructure/ec2/load_secrets.sh`** : AWS Secrets Manager →
  `.env` renderer for the non-GHA bootstrap path.
- **`infrastructure/ec2/fxvol-compose.service`** : systemd unit that
  keeps the stack running after reboots.
- **Six GHCR images published on every push to main** via the
  `build.yml` matrix : `fx-options-api`, `fx-options-frontend`,
  `fx-options-market-data`, `fx-options-vol-engine`,
  `fx-options-risk-engine`, `fx-options-db-writer`. Each image carries
  a `sha-<commit>` tag for immutable rollbacks and a `latest` tag for
  convenience.
- **`codeql.yml`** workflow : matrix Python + JavaScript/TypeScript
  with `security-extended` queries, triggered on push + PR + weekly.
- **`security-scan.yml`** workflow : Trivy vulnerability scan of the
  six production images, weekly + manual, SARIF upload to the Security
  tab with `HIGH,CRITICAL` gate and `ignore-unfixed: true`.
- **Consolidated frontend CI** (`frontend` job) : dump live OpenAPI
  schema, drift-check against `schema.d.ts`, lint, typecheck, vitest
  with 70% line coverage threshold, build and upload `fx-options-frontend-dist`
  artefact.
- **`frontend-e2e-compose`** CI job : full stack docker-compose up +
  Alembic migration + Playwright e2e against `http://localhost` (Nginx
  entrypoint) as a real-wiring integration test.
- **IB Gateway stub** (`infrastructure/docker/Dockerfile.ib-stub` +
  `server.py`) : minimal TCP accept on port 4002 for CI scenarios
  where the real IB credentials are unavailable.
- **`docs/DEPLOYMENT.md`** : operator runbook with provisioning, TLS
  cert bootstrap, tagged-release and manual-sha deployment paths,
  rollback procedure, backups and troubleshooting table.
- **`docs/IB_OPERATIONS.md`** gains a "Recovery procedures" section
  covering five production scenarios (IB Gateway login failures,
  silent Redis keys, rollback with Alembic edge cases, Let's Encrypt
  renewal failures, smoke-test triage).
- **`tests/test_pyqt_absence.py`** : AST-level guard that fails CI if
  `PyQt5` or `pyqtgraph` imports reappear anywhere in the source tree.
- **`tests/test_post_deploy_smoke.py`** (gate `PROD_SMOKE=1`) : health,
  health/extended, bundle served, assets immutable, OpenAPI schema
  matches committed `schema.d.ts`, TLS cert valid, WebSocket upgrade
  handshake accepted.
- **`tests/test_deploy_workflow.py`** : 55+ parse-level cases locking
  the full CI/CD surface (build, deploy, frontend, e2e-compose, codeql,
  trivy) against regression.

### Changed

- **`requirements.txt`** drops PyQt5 and pyqtgraph. Comment line
  explaining the removal preserved for archaeology.
- **`pytest.ini`** drops the `ui` marker. Removed from conftest.py are
  the `qapp` session fixture and `QT_QPA_PLATFORM=offscreen` env var.
- **CI workflow `frontend` job** now owns the OpenAPI drift check ;
  the former standalone `openapi-contract` job is deleted (folded in).
- **`vitest.config.ts`** gains `coverage.thresholds.lines = 70` so
  `npm run test:coverage` enforces the same gate locally and in CI.
  Visual shell components covered by Playwright are excluded from the
  coverage include list.
- **CI `quality` + `live-integration` jobs** drop the ~10 apt packages
  (`libxcb-*`, `libgl1`, …) that were installed for PyQt5 offscreen mode.
- **`compileall` + `ruff check`** targets now cover the whole post-R7
  tree (`src api core shared services`) instead of `src app.py`.

### Removed

- **`app.py`** at the repo root.
- **`src/controller.py`** and **`src/controller_settings.py`**.
- **`src/ui/`** : `main_window.py` and the eleven PyQt panels.
- **`tests/test_controller.py`**, **`test_controller_persistence.py`**,
  **`test_controller_consumer_mode.py`**, **`test_panels_ui.py`** — the
  test surface for the removed code.

### Security

- CodeQL analysis blocks merges on HIGH/CRITICAL OWASP + CWE findings.
- Trivy scans every image weekly for CVEs against the latest fix DB.
- EC2 provisioning script locks ufw to 22/80/443 and runs containers
  as the `fxvol` user.
- `deploy.yml` uses minimal permissions (`contents: read`, `packages:
  read`) and renders `.env` via SSH heredoc so secrets never reach the
  runner disk.

### Migration notes

- **Stop any running `python app.py`** on existing developer machines
  — it will no longer find `controller.py`.
- **Update your dev workflow** to `docker compose up` + `scripts/run_api.ps1`
  (locally) or wait for the deploy workflow (prod).
- **Bump `requirements.txt`** re-install if running a pre-R8 branch
  locally : `pip install -r requirements.txt` will uninstall PyQt5 +
  pyqtgraph.

## [1.8.x] — R8 increments

- 1.8.1 : remove PyQt stack and tests
- 1.8.2 : matrix build-and-push for the four engine images
- 1.8.3 : consolidated frontend pipeline (openapi + coverage + artefact)
- 1.8.4 : Playwright e2e against ephemeral docker-compose
- 1.8.5 : `deploy.yml` workflow + EC2 provisioning
- 1.8.6 : CodeQL + Trivy security scans
- 1.8.7 : post-deploy smoke tests + this CHANGELOG

## [1.7.0] — R7 release

Engines split into four standalone containers :
`market-data` / `vol-engine` / `risk-engine` / `db-writer`. Each service
owns its own IB client_id, publishes to Redis and is restart-independent.
`ENGINES_IN_PROCESS=false` lets the legacy PyQt controller run as a
consumer instead of a producer.

## [1.6.0] — R5 release

React 18 + TypeScript 5 + Vite 5 frontend with OpenAPI-generated types,
zustand stores, WebSocket hooks with reconnect backoff, nine panels
mirroring the PyQt layout, Playwright e2e and Nginx reverse-proxy
configs. Multi-stage Dockerfile.web publishes the bundle as a
`nginx:alpine` image.

## [1.5.0] — R4 release

FastAPI backend with 18 endpoints (pricing, vol, portfolio, analytics),
WebSocket bridge from Redis to the browser, structlog JSON access logs
and Prometheus metrics, middleware stack (CORS, rate limiting, timing).

## [1.4.0] — R3 release

Redis bus : pub/sub channels + latest-state cache with TTLs, throttled
tick publisher, engines wired to publish, live integration tests.

## [1.3.0] — R2 release

Async database writer : asyncio.Queue → batch `INSERT` → Postgres.
ON CONFLICT DO NOTHING for idempotency, exponential retries on
transient `OperationalError`, graceful shutdown drains the queue.

## [1.2.0] — R1 release

PostgreSQL schema via SQLAlchemy 2.x async ORM + Alembic migrations.
Seven tables : `account_snaps`, `vol_surfaces`, `signals`,
`backtest_runs`, `positions`, `position_snapshots`, `trades`.

## [1.1.0] — R0 release

CI/CD baseline : GitHub Actions workflow with compileall + ruff +
pytest, PR template, branch protection documentation and CI badge.
