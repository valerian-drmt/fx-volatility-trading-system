# Audit 8 — Infrastructure & Operations

## Why

The system runs 24/5 on a single EC2 box with 11+ containers, a nightly IB gateway
restart, and a continuous-deploy pipeline. Past incidents (disk-fill from image
accumulation, gateway version drift crash-loop, watchdog cron never running due to
exec-bit) were all *operational* failures, not code bugs. v1.0 must be able to survive
a reboot, a bad deploy, and a full-disk event without manual archaeology.

## Scope

- `docker-compose.yml` (+ profiles `engines`, `obs`), all Dockerfiles.
- `scripts/aws/`, `scripts/local/`, `scripts/ops/` (code review only — user-run scripts
  are never executed by the auditor).
- `.github/workflows/` deploy chain (build → deploy, paths-ignore guards).
- EC2 host expectations: cron, disk, watchdog, log rotation (reviewed via scripts/docs,
  live checks run by Valérian).
- Observability profile (7 services) + healthchecks.

## What to check

### Container resilience
- [ ] Restart policies on every service; startup ordering/dependency correctness
      (`depends_on` + healthcheck conditions, not just ordering).
- [ ] Healthchecks *mean* something: engines' checks are heartbeat-only (known gap) —
      does any check actually verify IB connectivity / data freshness? Define what each
      check should assert.
- [ ] Resource limits: memory limits on JVM (ib-gateway) and Python engines so one leak
      can't OOM the box; disk: log rotation on every container.
- [ ] Full reboot test: `docker compose down && up` (and host reboot) — everything
      returns healthy unattended, including after the ~02:00 gateway self-restart.

### Deploy pipeline
- [ ] Image pruning in place (disk-fill regression guard); pinned base images (gateway
      pin lesson: volume/version drift).
- [ ] Rollback path: documented, tested procedure to return to the previous sha- image.
- [ ] Migration safety in deploy: alembic upgrade ordering vs new code rollout; behavior
      if migration fails mid-deploy.
- [ ] Smoke checks assert real responses (JSON), not SPA-fallback 200s (past gotcha);
      nginx bind-mount reload step present.

### Backup & recovery
- [ ] Postgres backup: exists? schedule, retention, restore *tested* end-to-end on a
      scratch instance. This is the single most important ops item for v1.0.
- [ ] Redis: acceptable-loss statement (cache-only is fine — but write it down).
- [ ] SSM parameters: documented inventory so a new environment can be rebuilt.

### Monitoring & alerting
- [ ] The `obs` profile: what's actually collected; is it running in prod or opt-in only?
- [ ] Alerting: who is told, how, when a container crash-loops, disk passes 80%, IB
      session drops outside the nightly window, or db-writer lags. "Nobody" is a finding.
- [ ] Watchdog cron: exec bits, schedule, and its own failure visibility (known past bug).

### Host hygiene
- [ ] Disk headroom monitoring; docker prune scheduled; journal/log caps.
- [ ] Security updates policy for the AMI/base images.

## Method

Config review + local-stack chaos drills (kill containers, fill a small volume, break a
healthcheck) run locally. Prod checks are a documented checklist executed by Valérian
via the `admin` profile / SSM.

## Deliverable & exit criteria

- Findings report + a one-page **runbook**: reboot, rollback, restore-from-backup,
  IB-session recovery (committed to `docs/ops/`).
- **Exit:** restore-from-backup demonstrated once; reboot drill clean; alerting exists
  for crash-loop + disk + IB-down; zero Blocker/High open.